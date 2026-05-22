"""
Discord 웹후크로 브리핑 전송
- 만료/갱신 없음, URL 하나만 있으면 됨
- 임베드(embed)로 풍부한 포매팅, 임베드당 최대 6000자
"""
import logging
import os

import requests

logger = logging.getLogger(__name__)

DISCORD_WEBHOOK_URL = os.getenv("DISCORD_WEBHOOK_URL", "")

# Discord 임베드 색상 (10진수)
COLOR_BLUE = 0x3498DB
COLOR_GREEN = 0x2ECC71
COLOR_RED = 0xE74C3C
COLOR_GRAY = 0x95A5A6

RISK_COLOR = {"낮음": COLOR_GREEN, "중간": COLOR_BLUE, "높음": COLOR_RED}


def _post(payload: dict) -> bool:
    if not DISCORD_WEBHOOK_URL:
        logger.info("DISCORD_WEBHOOK_URL 미설정 — 전송 건너뜀")
        return False
    try:
        resp = requests.post(DISCORD_WEBHOOK_URL, json=payload, timeout=10)
        if resp.status_code in (200, 204):
            return True
        logger.error("Discord 전송 실패: %s %s", resp.status_code, resp.text[:200])
        return False
    except Exception as e:
        logger.error("Discord 요청 예외: %s", e)
        return False


def post_with_file(embeds: list[dict], file_path: str, filename: str = "chart.png") -> bool:
    """임베드 + 파일(이미지) 첨부 전송. 임베드에서 attachment://filename 으로 참조 가능"""
    import json as _json
    if not DISCORD_WEBHOOK_URL:
        logger.info("DISCORD_WEBHOOK_URL 미설정 — 전송 건너뜀")
        return False
    try:
        with open(file_path, "rb") as fp:
            files = {"file": (filename, fp.read(), "image/png")}
        data = {"payload_json": _json.dumps({"embeds": embeds})}
        resp = requests.post(DISCORD_WEBHOOK_URL, data=data, files=files, timeout=15)
        if resp.status_code in (200, 204):
            return True
        logger.error("Discord 파일 전송 실패: %s %s", resp.status_code, resp.text[:200])
        return False
    except Exception as e:
        logger.error("Discord 파일 요청 예외: %s", e)
        return False


def send_text(text: str) -> bool:
    """단순 텍스트 메시지 (최대 2000자)"""
    return _post({"content": text[:2000]})


def send_briefing(
    today: str,
    kospi_str: str,
    kosdaq_str: str,
    analysis: dict,
    price_map: dict[str, dict],
    accuracy_text: str = "",
) -> bool:
    """추천 종목 브리핑을 Discord 임베드로 전송"""
    from datetime import datetime, timezone, timedelta
    from html_report import DASHBOARD_URL
    recs = analysis["recommendations"]
    themes = ", ".join(analysis.get("key_themes", []))

    # 장 시작(09:00 KST) 전이면 "전일 종가" 라벨, 장중이면 "현재가"
    kst_now = datetime.now(timezone(timedelta(hours=9)))
    is_premarket = kst_now.hour < 9
    price_label = "전일 종가" if is_premarket else "현재가"
    pct_label = "전일 등락률" if is_premarket else "등락률"
    timing_note = (
        f"⏰ 데이터 기준: **전일 장마감 종가** (장 시작 전 브리핑)\n\n"
        if is_premarket else ""
    )

    # 1) 메인 임베드: 시장 요약
    main_embed = {
        "title": f"📈 주식 AI 브리핑 [{today}]",
        "url": DASHBOARD_URL,
        "color": COLOR_BLUE,
        "description": (
            f"{timing_note}"
            f"**{kospi_str}**\n**{kosdaq_str}**\n\n"
            f"📰 {analysis.get('market_summary', '')}\n\n"
            f"🔑 **핵심 테마**: {themes}\n\n"
            f"⚠️ **리스크**: {analysis.get('risk_factors', '')}\n\n"
            f"📱 [전체 통계 대시보드 →]({DASHBOARD_URL})"
        )[:4096],
    }

    # 2) 추천 종목 임베드 (종목별 field + 분리 빈 field)
    risk_emoji = {"낮음": "🟢", "중간": "🟡", "높음": "🔴"}
    fields = []
    for i, rec in enumerate(recs):
        code = rec["code"]
        price_info = price_map.get(code, {})
        price = price_info.get("current_price", 0)
        chg = price_info.get("change_pct", 0)
        sign = "▲" if chg >= 0 else "▼"
        mark = risk_emoji.get(rec.get("risk_level", "중간"), "🟡")

        naver_url = f"https://m.stock.naver.com/domestic/stock/{code}/total"
        field_name = f"{rec['rank']}위  {mark}"
        # 종목명을 굵게+밑줄+링크로 → 버튼 느낌
        value = (
            f"**[__{rec['name']}__]({naver_url})**  `{code}`\n"
            f"{price_label} **{price:,.0f}원** ({sign}{abs(chg):.1f}%)\n"
            f"섹터: {rec.get('sector', '-')} · 목표 +{rec.get('target_return', 0):.1f}%\n"
            f"💡 {rec.get('key_catalyst', '')}\n"
            f"_{rec.get('reason', '')}_"
        )
        fields.append({"name": field_name[:256], "value": value[:1024], "inline": False})
        # 마지막 종목이 아니면 분리용 빈 필드 추가 (Discord field 한도 25개 내)
        # 10 종목 + 9 분리 = 19 필드 → 25 한도 내
        if i < len(recs) - 1:
            fields.append({"name": "​", "value": "​", "inline": False})

    recs_embed = {
        "title": f"📊 추천 종목 TOP {len(recs)}",
        "color": COLOR_GREEN,
        "fields": fields[:25],  # Discord 임베드 필드 최대 25개
    }

    if accuracy_text:
        recs_embed["footer"] = {"text": accuracy_text[:2048]}

    # Discord는 한 메시지에 최대 10개 임베드
    return _post({"embeds": [main_embed, recs_embed]})
