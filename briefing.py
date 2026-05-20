"""
주식 브리핑 메인 오케스트레이터
"""
import logging
from datetime import date

from accuracy_tracker import format_accuracy_summary, record_prices_for_active_recs
from ai_analyzer import analyze_and_recommend
from database import init_db, save_recommendations
from kakao_sender import send_briefing_messages
from kis_api import kis
from news_fetcher import fetch_financial_news

logger = logging.getLogger(__name__)

RISK_EMOJI = {"낮음": "🟢", "중간": "🟡", "높음": "🔴"}


def _format_index(name: str, data: dict | None) -> str:
    if not data:
        return f"{name}: 조회 불가"
    sign = "▲" if data["change_pct"] >= 0 else "▼"
    return f"{name} {data['current']:,.2f} ({sign}{abs(data['change_pct']):.2f}%)"


def _format_briefing_messages(
    today: str,
    kospi_str: str,
    kosdaq_str: str,
    analysis: dict,
    price_map: dict[str, dict],
) -> list[str]:
    """카카오톡 200자 제한을 고려한 메시지 분할"""
    recs = analysis["recommendations"]
    themes = ", ".join(analysis.get("key_themes", []))

    # 메시지 1: 헤더 + 시장 요약
    msg1 = (
        f"📈 주식 AI 브리핑 [{today}]\n"
        f"{'─' * 22}\n"
        f"{kospi_str}\n"
        f"{kosdaq_str}\n\n"
        f"📰 {analysis.get('market_summary', '')}\n\n"
        f"🔑 핵심 테마: {themes}\n\n"
        f"⚠️ {analysis.get('risk_factors', '')}"
    )

    messages = [msg1]

    # 메시지 2~: 추천 종목 (2개씩 묶어서)
    chunk = []
    for i, rec in enumerate(recs):
        code = rec["code"]
        price_info = price_map.get(code, {})
        price = price_info.get("current_price", 0)
        chg = price_info.get("change_pct", 0)
        sign = "▲" if chg >= 0 else "▼"
        risk_mark = RISK_EMOJI.get(rec.get("risk_level", "중간"), "🟡")

        stock_text = (
            f"{rec['rank']}위 [{code}] {rec['name']} {risk_mark}\n"
            f"현재가: {price:,.0f}원 ({sign}{abs(chg):.1f}%)\n"
            f"섹터: {rec.get('sector', '-')}\n"
            f"목표수익: +{rec.get('target_return', 0):.1f}%\n"
            f"💡 {rec.get('key_catalyst', '')}\n"
        )
        chunk.append(stock_text)

        if len(chunk) == 2 or i == len(recs) - 1:
            messages.append(f"📊 추천 종목 ({recs[max(0,i-1)]['rank']}~{rec['rank']}위)\n{'─'*22}\n" + "\n".join(chunk))
            chunk = []

    # 마지막 메시지: 적중률 통계
    accuracy_text = format_accuracy_summary()
    if accuracy_text:
        messages.append(accuracy_text)

    return messages


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

    # 9. 카카오톡 브리핑 전송
    messages = _format_briefing_messages(today, kospi_str, kosdaq_str, analysis, price_map)
    logger.info("카카오톡 메시지 %d건 전송 중...", len(messages))
    send_briefing_messages(messages)

    logger.info("=== 브리핑 완료 ===")
    return analysis
