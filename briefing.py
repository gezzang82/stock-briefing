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

    # ETF/ETN/펀드 패턴 (검증 단계 2차 방어선)
    ETF_BRAND_PREFIXES = (
        "KODEX ", "TIGER ", "ARIRANG ", "KBSTAR ", "HANARO ", "KOSEF ",
        "KINDEX ", "KIWOOM ", "ACE ", "SOL ", "RISE ", "WOORI ", "TREX ",
        "FOCUS ", "PLUS ", "FN ", "MASTER ", "SMART ", "TIMEFOLIO ",
        "WON ", "BIG ",
    )
    ETF_NAME_KEYWORDS = ("ETF", "ETN", "SPAC", "리츠", "액티브",
                        "선물지수", "인버스", "레버리지")

    def _is_etf_like(name: str) -> bool:
        return (any(name.startswith(p) for p in ETF_BRAND_PREFIXES)
                or any(kw in name for kw in ETF_NAME_KEYWORDS))

    # 3) 인식 + 종목명 확보 + ETF/ETN 제외 + 이름 덮어쓰기
    cleaned = []
    for r in unique:
        code = r["code"]
        pdata = price_map.get(code)
        if not pdata:
            logger.warning("⚠️ KIS 미인식 코드 드롭: %s (AI 이름: %s)", code, r.get("name"))
            continue
        kis_name = kis.get_stock_name(code)
        ai_name = (r.get("name") or "").strip()
        if not kis_name:
            logger.warning("⚠️ KIS 종목명 조회 실패 - 드롭: [%s] (AI 이름: %s)", code, ai_name)
            continue
        if _is_etf_like(kis_name):
            logger.warning("⚠️ ETF/펀드 드롭: [%s] %s", code, kis_name)
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

    # 4a-pre. 시장 상태 판단 (regime별 가중치 결정)
    regime_text = ""
    weights = None
    try:
        from market_regime import detect_regime, get_weights_for_regime, format_regime_for_prompt
        regime_info = detect_regime()
        weights = get_weights_for_regime(regime_info["regime"])
        regime_text = format_regime_for_prompt(regime_info)
    except Exception as e:
        logger.warning("시장 상태 판단 실패 — 기본 가중치: %s", e)
        regime_info = None

    # 4a. 기술적 스크리닝 (regime 가중치 적용)
    tech_candidates = []
    try:
        from technical_screener import screen_candidates, format_for_prompt
        tech_candidates = screen_candidates(top_n=20, weights=weights)
        tech_text = format_for_prompt(tech_candidates)
        logger.info("기술적 후보 %d개를 AI에 전달", len(tech_candidates))
    except Exception as e:
        logger.warning("기술적 스크리닝 실패 — 뉴스 기반으로 폴백: %s", e)
        tech_text = ""

    # 4b. AI 분석 (시장 상태 + 뉴스 + 기술 후보 + 회피)
    recent = get_recent_recommended_stocks(days=7)
    logger.info("최근 7일 추천 이력: %d개 종목 (회피 힌트로 전달)", len(recent))
    analysis = analyze_and_recommend(
        news_text, kospi_display, kosdaq_display,
        recent_excluded=recent,
        tech_candidates_text=tech_text,
        regime_text=regime_text,
    )
    if regime_info:
        analysis["regime_info"] = regime_info

    # 5. 종목 검증 (코드 유효성/중복/이름 정정)
    cleaned_recs, price_map = _validate_and_clean_recommendations(analysis["recommendations"])

    # 6. 점수 필터 — 기술적 스크리닝 score ≥ MIN_SCORE_THRESHOLD인 종목만 유지
    from config import MIN_SCORE_THRESHOLD, TOP_N_STOCKS
    candidate_scores = {c["code"]: c for c in tech_candidates}
    qualified = []
    filtered_out = []
    for rec in cleaned_recs:
        cand = candidate_scores.get(rec["code"])
        if cand is None:
            rec["tech_score"] = None
            filtered_out.append((rec, "기술적 후보 아님 (점수 미산정)"))
            continue
        rec["tech_score"] = cand["score"]
        if cand["score"] < MIN_SCORE_THRESHOLD:
            filtered_out.append((rec, f"점수 {cand['score']:.0f} < {MIN_SCORE_THRESHOLD:.0f}"))
            continue
        qualified.append(rec)

    # 상위 N개 cap + 재정렬
    qualified = qualified[:TOP_N_STOCKS]
    for i, r in enumerate(qualified, 1):
        r["rank"] = i

    logger.info(
        "점수 필터: 검증 %d개 → 자격 %d개 (제외 %d, 기준 ≥ %d)",
        len(cleaned_recs), len(qualified), len(filtered_out), MIN_SCORE_THRESHOLD,
    )
    for rec, reason in filtered_out:
        logger.info("  ↘ 제외 [%s] %s — %s", rec["code"], rec["name"], reason)

    analysis["recommendations"] = qualified

    # 7. 자격 있는 추천 DB 저장 (0개여도 저장 — rec_date 기록용)
    save_recommendations(today, qualified, analysis.get("market_summary", ""))

    # 8. 추천 당일 가격을 entry_price로 업데이트 (자격 있는 종목만)
    from database import update_entry_price
    qualified_codes = {r["code"] for r in qualified}
    for code, pdata in price_map.items():
        if code in qualified_codes:
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
