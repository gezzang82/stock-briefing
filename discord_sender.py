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
    recs = analysis["recommendations"]
    themes = ", ".join(analysis.get("key_themes", []))

    # 1) 메인 임베드: 시장 요약
    main_embed = {
        "title": f"📈 주식 AI 브리핑 [{today}]",
        "color": COLOR_BLUE,
        "description": (
            f"**{kospi_str}**\n**{kosdaq_str}**\n\n"
            f"📰 {analysis.get('market_summary', '')}\n\n"
            f"🔑 **핵심 테마**: {themes}\n\n"
            f"⚠️ **리스크**: {analysis.get('risk_factors', '')}"
        )[:4096],
    }

    # 2) 추천 종목 임베드 (각 종목을 field로)
    risk_emoji = {"낮음": "🟢", "중간": "🟡", "높음": "🔴"}
    fields = []
    for rec in recs:
        code = rec["code"]
        price_info = price_map.get(code, {})
        price = price_info.get("current_price", 0)
        chg = price_info.get("change_pct", 0)
        sign = "▲" if chg >= 0 else "▼"
        mark = risk_emoji.get(rec.get("risk_level", "중간"), "🟡")

        name = f"{rec['rank']}위. [{code}] {rec['name']} {mark}"
        value = (
            f"현재가 **{price:,.0f}원** ({sign}{abs(chg):.1f}%)\n"
            f"섹터: {rec.get('sector', '-')} · 목표 +{rec.get('target_return', 0):.1f}%\n"
            f"💡 {rec.get('key_catalyst', '')}\n"
            f"_{rec.get('reason', '')}_"
        )
        fields.append({"name": name[:256], "value": value[:1024], "inline": False})

    recs_embed = {
        "title": f"📊 추천 종목 TOP {len(recs)}",
        "color": COLOR_GREEN,
        "fields": fields[:25],  # Discord 임베드 필드 최대 25개
    }

    if accuracy_text:
        recs_embed["footer"] = {"text": accuracy_text[:2048]}

    # Discord는 한 메시지에 최대 10개 임베드
    return _post({"embeds": [main_embed, recs_embed]})
