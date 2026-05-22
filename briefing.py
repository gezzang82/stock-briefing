"""
주식 브리핑 메인 오케스트레이터
"""
import logging
from datetime import date

from accuracy_tracker import format_accuracy_summary, record_prices_for_active_recs
from ai_analyzer import analyze_and_recommend
from database import get_recent_recommended_stocks, init_db, save_recommendations
from discord_sender import send_briefing as send_discord_briefing
from kis_api import kis
from news_fetcher import fetch_financial_news

logger = logging.getLogger(__name__)


def _format_index(name: str, data: dict | None) -> str:
    if not data:
        return f"{name}: 조회 불가"
    sign = "▲" if data["change_pct"] >= 0 else "▼"
    return f"{name} {data['current']:,.2f} ({sign}{abs(data['change_pct']):.2f}%)"


def _validate_and_clean_recommendations(recs: list[dict]) -> tuple[list[dict], dict[str, dict]]:
    """
    AI 추천 종목을 KIS API로 검증:
    - 중복 코드 제거 (상위 랭크 유지)
    - KIS에서 인식 못 하는 코드 드롭
    - AI가 준 이름을 KIS 실제 종목명으로 덮어쓰기 (hallucination 방지)
    - 1부터 재정렬

    Returns: (정제된 추천 목록, KIS 가격 데이터)
    """
    # 1) 중복 제거 (상위 랭크 유지)
    seen_codes: set[str] = set()
    unique = []
    for r in sorted(recs, key=lambda x: x.get("rank", 999)):
        code = (r.get("code") or "").strip().zfill(6)
        if not code or code in seen_codes:
            logger.warning("중복 코드 제거: %s (%s)", code, r.get("name"))
            continue
        seen_codes.add(code)
        r["code"] = code
        unique.append(r)

    # 2) KIS 가격/이름 조회 (검증)
    codes = [r["code"] for r in unique]
    logger.info("종목 검증/시세 조회: %s", codes)
    price_map = kis.get_multiple_prices(codes)

    # 3) 인식 + 종목명 확보 + ETF/ETN 제외 + 이름 덮어쓰기
    cleaned = []
    for r in unique:
        code = r["code"]
        pdata = price_map.get(code)
        if not pdata:
            logger.warning("⚠️ KIS 미인식 코드 드롭: %s (AI 이름: %s)", code, r.get("name"))
            continue
        # 종목명은 inquire-price에 없으므로 별도 조회 (캐시됨)
        kis_name = kis.get_stock_name(code)
        ai_name = (r.get("name") or "").strip()
        if not kis_name:
            logger.warning("⚠️ KIS 종목명 조회 실패 - 드롭: [%s] (AI 이름: %s)", code, ai_name)
            continue
        if kis.is_etf_or_etn(code):
            logger.warning("⚠️ ETF/ETN 드롭: [%s] %s", code, kis_name)
            continue
        if kis_name != ai_name:
            logger.info("종목명 정정: [%s] %s → %s", code, ai_name, kis_name)
            r["name"] = kis_name
        cleaned.append(r)

    # 4) TOP_N_STOCKS로 자르고 1부터 재정렬
    from config import TOP_N_STOCKS
    cleaned = cleaned[:TOP_N_STOCKS]
    for i, r in enumerate(cleaned, 1):
        r["rank"] = i

    logger.info(
        "검증 완료: AI %d개 → 유효 %d개 → 최종 %d개 (드롭 %d)",
        len(recs), len(cleaned), len(cleaned), len(recs) - len(cleaned),
    )
    return cleaned, price_map


def run_daily_briefing():
    """메인 브리핑 실행"""
    today = date.today().isoformat()
    logger.info("=== %s 주식 브리핑 시작 ===", today)

    # 1. DB 초기화
    init_db()

    # 2. 시장 지수 조회
    kospi_data = kis.get_index("KOSPI")
    kosdaq_data = kis.get_index("KOSDAQ")
    kospi_str = _format_index("KOSPI", kospi_data)
    kosdaq_str = _format_index("KOSDAQ", kosdaq_data)
    kospi_display = f"{kospi_data['current']:,.2f}" if kospi_data else "N/A"
    kosdaq_display = f"{kosdaq_data['current']:,.2f}" if kosdaq_data else "N/A"
    logger.info("시장 지수: %s | %s", kospi_str, kosdaq_str)

    # 3. 뉴스 수집
    logger.info("뉴스 수집 중...")
    news_text = fetch_financial_news(max_articles=40)

    # 4a. 기술적 스크리닝 (실패해도 fallback)
    try:
        from technical_screener import screen_candidates, format_for_prompt
        tech_candidates = screen_candidates(top_n=20)
        tech_text = format_for_prompt(tech_candidates)
        logger.info("기술적 후보 %d개를 AI에 전달", len(tech_candidates))
    except Exception as e:
        logger.warning("기술적 스크리닝 실패 — 뉴스 기반으로 폴백: %s", e)
        tech_text = ""

    # 4b. AI 분석 (뉴스 + 기술적 후보 + 최근 7일 회피)
    recent = get_recent_recommended_stocks(days=7)
    logger.info("최근 7일 추천 이력: %d개 종목 (회피 힌트로 전달)", len(recent))
    analysis = analyze_and_recommend(
        news_text, kospi_display, kosdaq_display,
        recent_excluded=recent,
        tech_candidates_text=tech_text,
    )

    # 5. 종목 검증 (코드 유효성/중복/이름 정정) — 시세 조회까지 한 번에
    cleaned_recs, price_map = _validate_and_clean_recommendations(analysis["recommendations"])
    analysis["recommendations"] = cleaned_recs

    # 6. 검증된 추천 종목 DB 저장
    save_recommendations(today, cleaned_recs, analysis.get("market_summary", ""))

    # 7. 추천 당일 가격을 entry_price로 업데이트
    from database import update_entry_price
    for code, pdata in price_map.items():
        update_entry_price(today, code, pdata["current_price"])

    # 8. 활성 추천에 대해 오늘 가격 기록 (적중률 추적용)
    record_prices_for_active_recs()

    # 9. HTML 대시보드 갱신 (GitHub Pages용)
    try:
        from accuracy_tracker import get_all_monthly_stats
        from html_report import generate_html_report
        from weekly_report import fetch_period_items, fetch_sector_performance, HTML_OUTPUT
        generate_html_report(
            get_all_monthly_stats(months_limit=12),
            fetch_period_items(days=7),
            fetch_sector_performance(days=30),
            HTML_OUTPUT,
        )
    except Exception as e:
        logger.warning("HTML 대시보드 생성 실패 (전송은 계속): %s", e)

    # 10. Discord 브리핑 전송
    accuracy_text = format_accuracy_summary()
    logger.info("Discord 브리핑 전송 중...")
    ok = send_discord_briefing(
        today=today,
        kospi_str=kospi_str,
        kosdaq_str=kosdaq_str,
        analysis=analysis,
        price_map=price_map,
        accuracy_text=accuracy_text,
    )
    logger.info("Discord 전송 %s", "성공" if ok else "실패/건너뜀")

    logger.info("=== 브리핑 완료 ===")
    return analysis
