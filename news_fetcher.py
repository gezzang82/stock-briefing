"""
네이버 검색 API 기반 금융 뉴스 수집

- 언론사 신뢰도 가중치 (연합 +3 / 한경 +2 / 매경 +2 / 뉴스1 +1 / 블로그·광고 -2)
- 동일 기사 중복 제거 (제목 정규화 키)
- 최근 24시간 vs 이전 24시간 언급량/감성 비교
- AI 프롬프트용 enriched 텍스트 반환
"""
import logging
import re
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime

import requests

from config import NAVER_CLIENT_ID, NAVER_CLIENT_SECRET

logger = logging.getLogger(__name__)

SEARCH_KEYWORDS = [
    "코스피 주식", "코스닥 주식", "증시 전망", "주식 추천 종목",
    "실적 발표 주식", "급등주", "반도체 주식", "2차전지 주식",
    "바이오 주식", "AI 주식",
]

NAVER_SEARCH_URL = "https://openapi.naver.com/v1/search/news.json"
KST = timezone(timedelta(hours=9))

# 언론사 신뢰도 가중치 (도메인 substring → (표시명, weight))
SOURCE_WEIGHTS: list[tuple[str, str, int]] = [
    ("yna.co.kr",       "연합뉴스",   3),
    ("yonhapnews",      "연합뉴스",   3),
    ("hankyung.com",    "한국경제",   2),
    ("hankyung",        "한국경제",   2),
    ("mk.co.kr",        "매일경제",   2),
    ("news1.kr",        "뉴스1",     1),
]

# 블로그/광고/홍보성 (가중치 -2)
BLOG_DOMAINS = (
    "blog.naver.com", "post.naver.com", "tistory.com",
    "blog.daum.net", ".blog.", "/blog/",
    "brunch.co.kr", "velog.io",
)
AD_TITLE_PATTERNS = (
    "[광고]", "(광고)", "광고]", "[홍보]", "(홍보)",
    "이벤트", "프로모션", "할인", "쿠폰",
)

# 한국어 감성 키워드 (간단 룰 기반 — 한 단어당 +1/-1)
POSITIVE_KEYWORDS = (
    "상승", "급등", "호조", "흑자", "성장", "기대감", "낙관", "강세",
    "신고가", "돌파", "회복", "수혜", "호재", "선전", "약진", "반등",
    "최대", "최고", "확대", "증가", "성공", "수주", "흥행", "개선",
)
NEGATIVE_KEYWORDS = (
    "하락", "급락", "부진", "적자", "둔화", "우려", "비관", "약세",
    "신저가", "위기", "리스크", "악재", "충격", "감소", "후퇴", "위축",
    "철수", "손실", "실패", "쇼크", "역성장", "침체",
)


def _clean_html(text: str) -> str:
    text = re.sub(r"<[^>]+>", "", text)
    return (text.replace("&quot;", '"').replace("&amp;", "&")
                .replace("&lt;", "<").replace("&gt;", ">").strip())


def _classify_source(link: str, title: str) -> tuple[str, int]:
    """(출처 표시명, 가중치 weight) 반환. 블로그/광고는 -2, 기타는 0."""
    link_lower = link.lower()
    title_clean = title.strip()

    if any(p in title_clean for p in AD_TITLE_PATTERNS):
        return ("광고/홍보", -2)
    if any(d in link_lower for d in BLOG_DOMAINS):
        return ("블로그", -2)
    for domain, name, weight in SOURCE_WEIGHTS:
        if domain in link_lower:
            return (name, weight)
    return ("기타", 0)


def _parse_pubdate(s: str) -> datetime | None:
    if not s:
        return None
    try:
        dt = parsedate_to_datetime(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=KST)
        return dt.astimezone(KST)
    except Exception:
        return None


def _sentiment_score(text: str) -> int:
    """단순 키워드 카운트 기반 감성 (긍정 - 부정)"""
    pos = sum(text.count(kw) for kw in POSITIVE_KEYWORDS)
    neg = sum(text.count(kw) for kw in NEGATIVE_KEYWORDS)
    return pos - neg


def _normalize_title(title: str) -> str:
    """중복 제거용 정규화 키 (특수문자/공백 제거 후 첫 40자)"""
    t = re.sub(r"[^\wㄱ-힣]", "", title).lower()
    return t[:40]


def _fetch_one(keyword: str, display: int) -> list[dict]:
    if not NAVER_CLIENT_ID or not NAVER_CLIENT_SECRET:
        return []
    headers = {
        "X-Naver-Client-Id": NAVER_CLIENT_ID,
        "X-Naver-Client-Secret": NAVER_CLIENT_SECRET,
    }
    params = {"query": keyword, "display": display, "sort": "date"}
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


def _enrich(art: dict) -> dict:
    source, weight = _classify_source(art["link"], art["title"])
    sentiment = _sentiment_score(art["title"] + " " + art["description"])
    return {
        **art,
        "source": source,
        "weight": weight,
        "sentiment": sentiment,
        "datetime": _parse_pubdate(art["pubDate"]),
        "norm_key": _normalize_title(art["title"]),
    }


def _dedupe(articles: list[dict]) -> list[dict]:
    """norm_key가 같은 기사는 가중치 가장 높은 것 1개만 유지"""
    seen: dict[str, dict] = {}
    for a in articles:
        k = a["norm_key"]
        if not k:
            continue
        if k not in seen or a["weight"] > seen[k]["weight"]:
            seen[k] = a
    return list(seen.values())


def _bucket_24h(articles: list[dict]) -> tuple[list[dict], list[dict]]:
    """현재 시각 기준 (~24h 이내, 24~48h 이내) 두 버킷으로 분리"""
    now = datetime.now(KST)
    c24 = now - timedelta(hours=24)
    c48 = now - timedelta(hours=48)
    last_24h = [a for a in articles if a["datetime"] and a["datetime"] >= c24]
    prev_24h = [a for a in articles if a["datetime"] and c48 <= a["datetime"] < c24]
    return last_24h, prev_24h


def _compute_stats(last_24h: list[dict], prev_24h: list[dict]) -> dict:
    """가중 언급량/감성 통계 (블로그·광고는 음수 가중치로 점수 감점)"""
    score_now = sum(a["weight"] for a in last_24h)
    score_prev = sum(a["weight"] for a in prev_24h)
    growth_pct = None
    if abs(score_prev) > 0:
        growth_pct = (score_now - score_prev) / abs(score_prev) * 100

    # 신뢰 기사만 카운트 (블로그 제외)
    trusted_now = [a for a in last_24h if a["weight"] >= 0]
    trusted_prev = [a for a in prev_24h if a["weight"] >= 0]
    avg_sent_now = (sum(a["sentiment"] for a in trusted_now) / len(trusted_now)) if trusted_now else 0.0
    avg_sent_prev = (sum(a["sentiment"] for a in trusted_prev) / len(trusted_prev)) if trusted_prev else 0.0

    return {
        "weighted_score_24h": score_now,
        "weighted_score_prev": score_prev,
        "growth_pct": growth_pct,
        "avg_sentiment_24h": avg_sent_now,
        "avg_sentiment_prev": avg_sent_prev,
        "sentiment_change": avg_sent_now - avg_sent_prev,
        "trusted_count_24h": len(trusted_now),
        "trusted_count_prev": len(trusted_prev),
    }


def fetch_financial_news(max_articles: int = 50) -> str:
    """
    네이버 뉴스 → 가중치/감성/중복제거/통계 → AI 프롬프트용 텍스트.
    """
    raw = []
    for kw in SEARCH_KEYWORDS:
        raw.extend(_fetch_one(kw, display=15))
    if not raw:
        logger.warning("뉴스를 수집하지 못했습니다.")
        return "뉴스 데이터 없음 — 일반 시장 분석 기반으로 추천해주세요."

    enriched = [_enrich(a) for a in raw]
    deduped = _dedupe(enriched)
    blocked = [a for a in deduped if a["weight"] < 0]
    last_24h, prev_24h = _bucket_24h(deduped)
    stats = _compute_stats(last_24h, prev_24h)

    logger.info(
        "뉴스 raw %d → dedupe %d (24h %d, prev24h %d, 차단 %d) | "
        "점수 %d→%d (%s) | 감성 %+.2f→%+.2f (Δ%+.2f)",
        len(raw), len(deduped), len(last_24h), len(prev_24h), len(blocked),
        stats["weighted_score_prev"], stats["weighted_score_24h"],
        f"{stats['growth_pct']:+.0f}%" if stats["growth_pct"] is not None else "N/A",
        stats["avg_sentiment_prev"], stats["avg_sentiment_24h"], stats["sentiment_change"],
    )

    # 신뢰 기사만 신뢰도+시간순으로 정렬해서 상위 max_articles 선택
    display_pool = [a for a in deduped if a["weight"] >= 0]
    display_pool.sort(
        key=lambda a: (a["weight"], a["datetime"] or datetime.min.replace(tzinfo=KST)),
        reverse=True,
    )
    selected = display_pool[:max_articles]

    # AI용 텍스트 구성
    lines = ["=== 시장 뉴스 분석 (최근 48시간) ==="]
    lines.append("")
    lines.append("📊 종합 지표")
    growth_str = (f"{stats['growth_pct']:+.0f}%"
                  if stats["growth_pct"] is not None else "N/A")
    lines.append(
        f"  · 가중 언급량: 최근 24h {stats['weighted_score_24h']:+d}점 "
        f"(신뢰기사 {stats['trusted_count_24h']}건) "
        f"vs 이전 24h {stats['weighted_score_prev']:+d}점 "
        f"(신뢰기사 {stats['trusted_count_prev']}건) → 변화 {growth_str}"
    )
    lines.append(
        f"  · 평균 감성: 최근 24h {stats['avg_sentiment_24h']:+.2f} "
        f"vs 이전 24h {stats['avg_sentiment_prev']:+.2f} "
        f"(Δ {stats['sentiment_change']:+.2f})"
    )
    if blocked:
        lines.append(f"  · 광고/블로그 제외: {len(blocked)}건")
    lines.append("")
    lines.append("📰 주요 기사 (언론사 신뢰도순)")
    for i, art in enumerate(selected, 1):
        ssign = "▲" if art["sentiment"] > 0 else ("▼" if art["sentiment"] < 0 else "·")
        lines.append(
            f"  [{i}] [{art['source']} W{art['weight']:+d}] "
            f"감성{ssign}{art['sentiment']:+d}  {art['title']}"
        )
        if art["description"]:
            lines.append(f"      {art['description'][:120]}")

    return "\n".join(lines)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    print(fetch_financial_news(max_articles=20))
