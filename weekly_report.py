"""
주간 백테스트 리포트
- 월별 티어 분포 + 종목/섹터 성과를 HTML로 생성 (docs/index.html)
- GitHub Pages에 자동 배포되어 모바일에서 URL로 접근
- 카카오톡 나에게 보내기로 요약 + 대시보드 링크 전송
"""
import logging
from datetime import date, timedelta
from pathlib import Path

from accuracy_tracker import (
    TIER_LABELS, get_all_monthly_stats, get_monthly_tier_stats,
)
from config import BASE_DIR, DB_PATH
from database import get_conn
from html_report import DASHBOARD_URL, generate_html_report
from kakao_sender import send_message as kakao_send
from market_calendar import today_kst

logger = logging.getLogger(__name__)

# GitHub Pages는 main:/docs 폴더를 직접 서빙 (Settings → Pages → Branch 모드).
# "public"이 아닌 "docs"인 이유: GitHub Pages가 root와 /docs만 폴더 옵션으로 제공.
PUBLIC_DIR = BASE_DIR / "docs"
HTML_OUTPUT = PUBLIC_DIR / "index.html"

COLOR_BLUE = 0x3498DB
COLOR_GREEN = 0x2ECC71
COLOR_RED = 0xE74C3C


# ============== 데이터 ==============

def fetch_period_items(days: int = 14) -> list[dict]:
    today = today_kst()
    start = (today - timedelta(days=days)).isoformat()
    with get_conn() as conn:
        rows = conn.execute(
            """SELECT r.id, r.rec_date, r.rank, r.stock_code, r.stock_name,
                      r.sector, r.market, r.entry_price, r.risk_level,
                      r.mfe_pct, r.mae_pct, r.final_return_pct,
                      (SELECT pt.return_pct FROM price_tracking pt
                       WHERE pt.recommendation_id = r.id
                       ORDER BY pt.track_date DESC LIMIT 1) AS latest_return
               FROM recommendations r
               WHERE r.rec_date >= ?
               ORDER BY r.rec_date DESC, r.rank ASC""",
            (start,),
        ).fetchall()
    return [dict(r) for r in rows]


# 섹터명 정규화 — sector_utils 모듈 사용 (html_report와 공유 매핑)
from sector_utils import normalize_sector as _normalize_sector


def fetch_sector_performance(days: int = 30) -> list[dict]:
    """
    섹터별 평균 수익률 (정규화 후 합산).

    SQL 단에서 GROUP BY를 못 함 — alias 매핑이 Python 측 정의라.
    raw rows 가져온 뒤 normalized sector로 재집계.
    """
    today = today_kst()
    start = (today - timedelta(days=days)).isoformat()
    with get_conn() as conn:
        rows = conn.execute(
            """SELECT r.sector AS raw_sector,
                      (SELECT pt.return_pct FROM price_tracking pt
                       WHERE pt.recommendation_id = r.id
                       ORDER BY pt.track_date DESC LIMIT 1) AS ret
               FROM recommendations r
               WHERE r.rec_date >= ?
                 AND r.sector IS NOT NULL AND r.sector != ''""",
            (start,),
        ).fetchall()

    # Python에서 정규화 + GROUP BY 재집계
    buckets: dict[str, list[float]] = {}
    for r in rows:
        if r["ret"] is None:
            continue
        norm = _normalize_sector(r["raw_sector"])
        if not norm:
            continue
        buckets.setdefault(norm, []).append(r["ret"])

    result = [
        {
            "sector": norm,
            "total": len(returns),
            "avg_return": sum(returns) / len(returns),
        }
        for norm, returns in buckets.items()
    ]
    # 평균 수익률 내림차순
    result.sort(key=lambda x: x["avg_return"], reverse=True)
    return result


# ============== 카카오톡 메시지 빌더 ==============

def _format_month_line(stats: dict) -> str:
    """한 달 통계를 한 줄로 간략화 (카카오 글자수 제약)"""
    month = stats["month"]
    if stats["matured_count"] == 0:
        if stats["total_recs"] == 0:
            return f"{month}: 추천 없음"
        return f"{month}: 진행중 {stats['in_progress_count']}개 (만기 미도달)"
    return (
        f"{month}: 만기 {stats['matured_count']}개 / "
        f"평균 {stats['avg_return']:+.2f}% / "
        f"승률 {stats['win_rate']:.0f}%"
    )


def build_kakao_message(current_month: dict, prev_month: dict | None) -> str:
    lines = ["📊 주간 백테스트 리포트", ""]
    lines.append(_format_month_line(current_month))
    if prev_month:
        lines.append(_format_month_line(prev_month))
    lines.append("")
    lines.append(f"📱 대시보드: {DASHBOARD_URL}")
    return "\n".join(lines)


# ============== 메인 ==============

def _prev_month_str(ym: str) -> str:
    y, m = map(int, ym.split("-"))
    m -= 1
    if m == 0:
        m, y = 12, y - 1
    return f"{y:04d}-{m:02d}"


def run_weekly_report():
    logger.info("=== 주간 리포트 생성 시작 ===")

    if not DB_PATH.exists():
        logger.warning("DB 파일 없음 — 빈 리포트 생성")

    today = today_kst()
    cur_month = today.strftime("%Y-%m")
    prev_ym = _prev_month_str(cur_month)

    current_month = get_monthly_tier_stats(cur_month)
    prev_month_stats = get_monthly_tier_stats(prev_ym)
    if prev_month_stats["total_recs"] == 0:
        prev_month_stats = None

    monthly_all = get_all_monthly_stats(months_limit=12)
    week_items = fetch_period_items(days=14)  # TRACKING_DAYS와 일치 — 추적 만기까지 모든 종목 표시
    sectors = fetch_sector_performance(days=30)

    logger.info(
        "%s — 만기 %d / 진행중 %d / 평균 %+.2f%% / 승률 %.0f%%",
        cur_month, current_month["matured_count"], current_month["in_progress_count"],
        current_month["avg_return"], current_month["win_rate"],
    )

    # 1) HTML 리포트 생성 (Pages에서 서빙됨)
    generate_html_report(monthly_all, week_items, sectors, HTML_OUTPUT)

    # 2) 카카오톡 나에게 보내기 — 요약 + 대시보드 링크
    msg = build_kakao_message(current_month, prev_month_stats)
    ok = kakao_send(msg)
    logger.info("카카오톡 주간 리포트 전송 %s", "성공" if ok else "실패")

    logger.info("=== 주간 리포트 완료 ===")


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    run_weekly_report()
