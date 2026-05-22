"""
주식 브리핑 메인 오케스트레이터
"""
import logging
from datetime import date

from accuracy_tracker import format_accuracy_summary, record_prices_for_active_recs
from ai_analyzer import analyze_and_recommend
from database import init_db, save_recommendations
from discord_sender import send_briefing as send_discord_briefing
from kis_api import kis
from news_fetcher import fetch_financial_news

logger = logging.getLogger(__name__)


def _format_index(name: str, data: dict | None) -> str:
    if not data:
        return f"{name}: 조회 불가"
    sign = "▲" if data["change_pct"] >= 0 else "▼"
    return f"{name} {data['current']:,.2f} ({sign}{abs(data['change_pct']):.2f}%)"


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

    # 4. AI 분석
    analysis = analyze_and_recommend(news_text, kospi_display, kosdaq_display)
    recs = analysis["recommendations"]

    # 5. 추천 종목 DB 저장
    save_recommendations(today, recs, analysis.get("market_summary", ""))

    # 6. 추천 종목 현재가 조회
    codes = [r["code"] for r in recs]
    logger.info("추천 종목 현재가 조회: %s", codes)
    price_map = kis.get_multiple_prices(codes)

    # 7. 추천 당일 가격을 entry_price로 업데이트
    from database import update_entry_price
    for code, pdata in price_map.items():
        update_entry_price(today, code, pdata["current_price"])

    # 8. 활성 추천에 대해 오늘 가격 기록 (적중률 추적용)
    record_prices_for_active_recs()

    # 9. Discord 브리핑 전송
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
