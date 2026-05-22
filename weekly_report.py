"""
주간 백테스트 리포트
- 월별 티어 분포 (8개 버킷: +30%↑ ~ -30%↓) - 월 기준 리셋
- 이번 주 TOP/BOTTOM 5
- 섹터별 성과
- Discord 임베드 + 차트 이미지 첨부
"""
import logging
import os
import tempfile
from datetime import date, datetime, timedelta

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt

from accuracy_tracker import (
    TIER_COLORS, TIER_LABELS, TIER_LABELS_EN,
    get_all_monthly_stats, get_monthly_tier_stats,
)
from config import DB_PATH
from database import get_conn
from discord_sender import post_with_file

logger = logging.getLogger(__name__)


# ============== 데이터 집계 ==============

def fetch_period_stats(days: int = 7) -> dict:
    """지난 N일간 추천된 종목의 현재 시점 성과"""
    today = date.today()
    start = (today - timedelta(days=days)).isoformat()

    with get_conn() as conn:
        rows = conn.execute(
            """SELECT r.id, r.rec_date, r.rank, r.stock_code, r.stock_name,
                      r.sector, r.entry_price, r.target_return_pct, r.risk_level,
                      (SELECT pt.return_pct FROM price_tracking pt
                       WHERE pt.recommendation_id = r.id
                       ORDER BY pt.track_date DESC LIMIT 1) AS latest_return
               FROM recommendations r
               WHERE r.rec_date >= ?
               ORDER BY r.rec_date DESC, r.rank ASC""",
            (start,),
        ).fetchall()

    items = [dict(r) for r in rows]
    valid = [x for x in items if x["latest_return"] is not None]

    return {
        "period_days": days,
        "start_date": start,
        "end_date": today.isoformat(),
        "total": len(items),
        "tracked": len(valid),
        "items": items,
    }


def fetch_sector_performance(days: int = 30) -> list[dict]:
    """섹터별 평균 수익률"""
    today = date.today()
    start = (today - timedelta(days=days)).isoformat()

    with get_conn() as conn:
        rows = conn.execute(
            """SELECT r.sector,
                      COUNT(*) AS total,
                      AVG(
                          (SELECT pt.return_pct FROM price_tracking pt
                           WHERE pt.recommendation_id = r.id
                           ORDER BY pt.track_date DESC LIMIT 1)
                      ) AS avg_return
               FROM recommendations r
               WHERE r.rec_date >= ? AND r.sector IS NOT NULL AND r.sector != ''
               GROUP BY r.sector
               HAVING avg_return IS NOT NULL
               ORDER BY avg_return DESC""",
            (start,),
        ).fetchall()

    return [dict(r) for r in rows]


# ============== 차트 (월별 티어 분포 - 수량/비율 2단) ==============

def render_monthly_tier_chart(monthly_stats: list[dict], output_path: str):
    months_with_data = [m for m in monthly_stats if m["matured_count"] > 0]

    if not months_with_data:
        # 만기 데이터 없으면 진행 중 상황 안내
        fig, ax = plt.subplots(figsize=(8, 3.5))
        in_progress = sum(m["in_progress_count"] for m in monthly_stats)
        msg = (
            f"No matured recommendations yet\n"
            f"(currently tracking {in_progress} stocks)\n"
            f"First evaluation in 14 days from recommendation date"
        )
        ax.text(0.5, 0.5, msg, ha="center", va="center", fontsize=12)
        ax.axis("off")
        fig.savefig(output_path, dpi=120, bbox_inches="tight")
        plt.close(fig)
        return

    months = [m["month"] for m in months_with_data]
    n_tiers = len(TIER_LABELS)

    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(11, 9))

    bottoms_count = [0] * len(months)
    bottoms_pct = [0] * len(months)

    for tier_idx in range(n_tiers):
        counts = [m["tier_counts"][tier_idx] for m in months_with_data]
        totals = [m["matured_count"] for m in months_with_data]
        pcts = [c / t * 100 if t else 0 for c, t in zip(counts, totals)]

        ax1.bar(months, counts, bottom=bottoms_count,
                color=TIER_COLORS[tier_idx], label=TIER_LABELS_EN[tier_idx],
                edgecolor="white", linewidth=0.5, width=0.6)
        ax2.bar(months, pcts, bottom=bottoms_pct,
                color=TIER_COLORS[tier_idx], label=TIER_LABELS_EN[tier_idx],
                edgecolor="white", linewidth=0.5, width=0.6)

        bottoms_count = [b + c for b, c in zip(bottoms_count, counts)]
        bottoms_pct = [b + p for b, p in zip(bottoms_pct, pcts)]

    # 상단: 절대 수량
    ax1.set_ylabel("Stocks (count)")
    ax1.set_title("Monthly tier distribution (matured stocks)")
    ax1.legend(loc="center left", bbox_to_anchor=(1.02, 0.5), fontsize=9, frameon=False)
    ax1.grid(axis="y", alpha=0.3)

    # 평균 수익률 텍스트 (각 막대 위)
    for i, m in enumerate(months_with_data):
        ax1.text(i, m["matured_count"] + 0.3,
                 f"avg {m['avg_return']:+.1f}%",
                 ha="center", fontsize=8, color="#333")

    # 하단: 비율
    ax2.set_ylabel("Stocks (%)")
    ax2.set_xlabel("Month")
    ax2.set_title("Monthly tier distribution (normalized)")
    ax2.set_ylim(0, 100)
    ax2.legend(loc="center left", bbox_to_anchor=(1.02, 0.5), fontsize=9, frameon=False)
    ax2.grid(axis="y", alpha=0.3)

    fig.tight_layout()
    fig.savefig(output_path, dpi=120, bbox_inches="tight")
    plt.close(fig)


# ============== 임베드 ==============

COLOR_BLUE = 0x3498DB
COLOR_GREEN = 0x2ECC71
COLOR_RED = 0xE74C3C
COLOR_GRAY = 0x95A5A6


def _format_stock_line(it: dict) -> str:
    ret = it["latest_return"]
    sign = "▲" if ret >= 0 else "▼"
    return (
        f"`{it['stock_code']}` **{it['stock_name']}** "
        f"({it.get('sector') or '-'}) — "
        f"{sign}{abs(ret):.2f}% · 추천일 {it['rec_date']}"
    )


def _format_month_block(stats: dict) -> str:
    if stats["matured_count"] == 0:
        if stats["total_recs"] == 0:
            return f"**{stats['month']}** — 추천 없음"
        return (
            f"**{stats['month']}** — 추천 {stats['total_recs']}개 "
            f"(진행중 {stats['in_progress_count']}개, 만기 미도달)"
        )
    lines = [
        f"**{stats['month']}** — 만기 {stats['matured_count']}개"
        + (f" / 진행중 {stats['in_progress_count']}" if stats["in_progress_count"] else "")
    ]
    for label, count in zip(TIER_LABELS, stats["tier_counts"]):
        if count == 0:
            continue
        pct = count / stats["matured_count"] * 100
        lines.append(f"  · {label:<10} {count}개 ({pct:.1f}%)")
    lines.append(
        f"  📈 평균 **{stats['avg_return']:+.2f}%** · "
        f"승률 {stats['win_rate']:.0f}% · 강승(≥5%) {stats['strong_win_rate']:.0f}%"
    )
    return "\n".join(lines)


def build_embeds(week: dict, current_month: dict, prev_month: dict | None,
                 sectors: list[dict]) -> list[dict]:
    # 색상: 현재 월 평균에 따라
    if current_month["matured_count"] > 0:
        color = COLOR_GREEN if current_month["avg_return"] >= 0 else COLOR_RED
    else:
        color = COLOR_BLUE

    # 1) 월별 적중 분포 (메인)
    parts = [_format_month_block(current_month)]
    if prev_month:
        parts.append("")
        parts.append(_format_month_block(prev_month))

    main_embed = {
        "title": "📊 월별 적중 분포 리포트",
        "color": color,
        "description": "\n".join(parts)[:4096],
        "image": {"url": "attachment://chart.png"},
    }

    # 2) 이번 주 TOP/BOTTOM + 섹터
    valid = [x for x in week["items"] if x["latest_return"] is not None]
    valid_sorted = sorted(valid, key=lambda x: x["latest_return"], reverse=True)
    top5 = valid_sorted[:5]
    bottom5 = list(reversed(valid_sorted[-5:])) if len(valid_sorted) > 5 else []

    perf_fields = []
    if top5:
        perf_fields.append({
            "name": f"🚀 이번 주 TOP 5 (지난 {week['period_days']}일)",
            "value": "\n".join(_format_stock_line(x) for x in top5)[:1024],
            "inline": False,
        })
    if bottom5:
        perf_fields.append({
            "name": "📉 이번 주 BOTTOM 5",
            "value": "\n".join(_format_stock_line(x) for x in bottom5)[:1024],
            "inline": False,
        })
    if sectors:
        sector_lines = []
        for s in sectors[:8]:
            sign = "▲" if s["avg_return"] >= 0 else "▼"
            sector_lines.append(
                f"{sign} **{s['sector']}** {s['avg_return']:+.2f}% ({s['total']}개)"
            )
        perf_fields.append({
            "name": "🏷️ 섹터별 평균 수익률 (30일)",
            "value": "\n".join(sector_lines)[:1024],
            "inline": False,
        })

    perf_embed = {
        "title": "🏆 종목/섹터 상세",
        "color": COLOR_BLUE,
        "fields": perf_fields[:25],
    }

    return [main_embed, perf_embed]


# ============== 메인 ==============

def _prev_month_str(ym: str) -> str:
    y, m = ym.split("-")
    y, m = int(y), int(m)
    m -= 1
    if m == 0:
        m = 12
        y -= 1
    return f"{y:04d}-{m:02d}"


def run_weekly_report():
    logger.info("=== 주간 리포트 생성 시작 ===")

    if not DB_PATH.exists():
        logger.warning("DB 파일 없음 — 리포트 건너뜀")
        return

    today = date.today()
    cur_month = today.strftime("%Y-%m")
    prev_month_ym = _prev_month_str(cur_month)

    current_month = get_monthly_tier_stats(cur_month)
    prev_month = get_monthly_tier_stats(prev_month_ym)
    # 전월 데이터 없으면 표시 생략
    if prev_month["total_recs"] == 0:
        prev_month = None

    monthly_all = get_all_monthly_stats(months_limit=12)
    week = fetch_period_stats(days=7)
    sectors = fetch_sector_performance(days=30)

    logger.info(
        "%s — 만기 %d / 진행중 %d / 평균 %+.2f%% / 승률 %.0f%%",
        cur_month, current_month["matured_count"], current_month["in_progress_count"],
        current_month["avg_return"], current_month["win_rate"],
    )

    tmp = tempfile.NamedTemporaryFile(suffix=".png", delete=False)
    tmp.close()
    try:
        render_monthly_tier_chart(monthly_all, tmp.name)
        embeds = build_embeds(week, current_month, prev_month, sectors)
        ok = post_with_file(embeds, tmp.name, "chart.png")
        logger.info("Discord 주간 리포트 전송 %s", "성공" if ok else "실패")
    finally:
        if os.path.exists(tmp.name):
            os.unlink(tmp.name)

    logger.info("=== 주간 리포트 완료 ===")


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    run_weekly_report()
