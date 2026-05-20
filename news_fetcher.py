"""
네이버 검색 API를 이용한 금융 뉴스 수집
https://developers.naver.com/docs/serviceapi/search/news/news.md
"""
import logging
import re
from datetime import datetime, timedelta

import requests

from config import NAVER_CLIENT_ID, NAVER_CLIENT_SECRET

logger = logging.getLogger(__name__)

SEARCH_KEYWORDS = [
    "코스피 주식",
    "코스닥 주식",
    "증시 전망",
    "주식 추천 종목",
    "실적 발표 주식",
    "급등주",
    "반도체 주식",
    "2차전지 주식",
    "바이오 주식",
    "AI 주식",
]

NAVER_SEARCH_URL = "https://openapi.naver.com/v1/search/news.json"


def _clean_html(text: str) -> str:
    text = re.sub(r"<[^>]+>", "", text)
    text = text.replace("&quot;", '"').replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">")
    return text.strip()


def _fetch_news(keyword: str, display: int = 5) -> list[dict]:
    if not NAVER_CLIENT_ID or not NAVER_CLIENT_SECRET:
        return []
    headers = {
        "X-Naver-Client-Id": NAVER_CLIENT_ID,
        "X-Naver-Client-Secret": NAVER_CLIENT_SECRET,
    }
    params = {
        "query": keyword,
        "display": display,
        "sort": "date",
    }
    try:
        resp = requests.get(NAVER_SEARCH_URL, headers=headers, params=params, timeout=8)
        resp.raise_for_status()
        items = resp.json().get("items", [])
        return [
            {
                "title": _clean_html(item["title"]),
                "description": _clean_html(item["description"]),
                "pubDate": item.get("pubDate", ""),
                "link": item.get("originallink") or item.get("link", ""),
            }
            for item in items
        ]
    except Exception as e:
        logger.error("뉴스 수집 오류 [%s]: %s", keyword, e)
        return []


def fetch_financial_news(max_articles: int = 40) -> str:
    """
    여러 키워드로 최신 금융 뉴스를 수집하여 텍스트로 반환.
    AI 분석 프롬프트에 삽입할 형태로 포맷팅.
    """
    all_news = []
    seen_titles = set()

    for keyword in SEARCH_KEYWORDS:
        articles = _fetch_news(keyword, display=5)
        for art in articles:
            title = art["title"]
            if title not in seen_titles:
                seen_titles.add(title)
                all_news.append(art)
        if len(all_news) >= max_articles:
            break

    if not all_news:
        logger.warning("뉴스를 수집하지 못했습니다.")
        return "뉴스 데이터를 수집하지 못했습니다. 일반적인 시장 분석을 기반으로 추천해 주세요."

    lines = []
    for i, art in enumerate(all_news[:max_articles], 1):
        lines.append(f"[{i}] {art['title']}")
        if art["description"]:
            lines.append(f"    {art['description'][:120]}")
        lines.append("")

    logger.info("뉴스 %d건 수집 완료", len(all_news[:max_articles]))
    return "\n".join(lines)
