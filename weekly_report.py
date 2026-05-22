"""
주간 백테스트 리포트
- 지난 30일치 추천 종목 성과 분석
- 적중률/평균수익률 차트 생성
- Discord에 임베드 + 차트 이미지로 전송
"""
import logging
import os
import tempfile
from collections import defaultdict
from datetime import date, datetime, timedelta

import matplotlib

matplotlib.use("Agg")  # GUI 없는 환경(CI)에서 필요
import matplotlib.pyplot as plt
from matplotlib import dates as mdates

from config import DB_PATH, HIT_THRESHOLD_PCT
from database import get_conn
from discord_sender import post_with_file

logger = logging.getLogger(__name__)


# ============== 데이터 집계 ==============

def fetch_period_stats(days: int = 7) -> dict:
    """지난 N일간 추천된 종목의 현재 시점 성과"""
    today = date.today()
    start = (today - timedelta(days=days)).isoformat()

    with get_conn() as conn:
        # 종목별 최신 추적 가격 (return_pct)
        rows = conn.execute(
            """SELECT r.id, r.rec_date, r.rank, r.stock_code, r.stock_name,
                      r.sector, r.entry_price, r.target_return_pct, r.risk_level,
                      (SELECT pt.return_pct FROM price_tracking pt
                       WHERE pt.recommendation_id = r.id
                       ORDER BY pt.track_date DESC LIMIT 1) AS latest_return,
                      (SELECT pt.close_price FROM price_tracking pt
                       WHERE pt.recommendation_id = r.id
                       ORDER BY pt.track_date DESC LIMIT 1) AS latest_price,
                      (SELECT pt.track_date FROM price_tracking pt
                       WHERE pt.recommendation_id = r.id
                       ORDER BY pt.track_date DESC LIMIT 1) AS latest_date
               FROM recommendations r
               WHERE r.rec_date >= ?
               ORDER BY r.rec_date DESC, r.rank ASC""",
            (start,),
        ).fetchall()

    items = [dict(r) for r in rows]

    # 전체 통계
    valid = [x for x in items if x["latest_return"] is not None]
    hits = [x for x in valid if x["latest_return"] >= HIT_THRESHOLD_PCT]

    return {
        "period_days": days,
        "start_date": start,
        "end_date": today.isoformat(),
        "total": len(items),
        "tracked": len(valid),
        "hit_count": len(hits),
        "hit_rate": (len(hits) / len(valid) * 100) if valid else 0.0,
        "avg_return": (sum(x["latest_return"] for x in valid) / len(valid)) if valid else 0.0,
        "items": items,
    }


def fetch_daily_aggregates(days: int = 14) -> list[dict]:
    """일자별 평균 수익률 / 적중 종목 수 (차트용)"""
    today = date.today()
    start = (today - timedelta(days=days)).isoformat()

    with get_conn() as conn:
        rows = conn.execute(
            """SELECT r.rec_date,
                      COUNT(*) AS total,
                      SUM(CASE WHEN COALESCE(
                          (SELECT pt.return_pct FROM price_tracking pt
                           WHERE pt.recommendation_id = r.id
                           ORDER BY pt.track_date DESC LIMIT 1), -999) >= ?
                          THEN 1 ELSE 0 END) AS hits,
                      AVG(
                          (SELECT pt.return_pct FROM price_tracking pt
                           WHERE pt.recommendation_id = r.id
                           ORDER BY pt.track_date DESC LIMIT 1)
                      ) AS avg_return
               FROM recommendations r
               WHERE r.rec_date >= ?
               GROUP BY r.rec_date
               ORDER BY r.rec_date ASC""",
            (HIT_THRESHOLD_PCT, start),
        ).fetchall()

    return [dict(r) for r in rows]


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


def fetch_all_time_stats() -> dict:
    """전체 누적 통계"""
    with get_conn() as conn:
        row = conn.execute(
            """SELECT COUNT(*) AS total_recs,
                      COUNT(DISTINCT rec_date) AS rec_days
               FROM recommendations"""
        ).fetchone()
        acc = conn.execute(
            """SELECT SUM(total_stocks) AS total,
                      SUM(hit_count) AS hits,
                      AVG(hit_rate_pct) AS avg_hit_rate,
                      AVG(avg_return_pct) AS avg_return
               FROM accuracy_results"""
        ).fetchone()

    return {
        "total_recs": row["total_recs"] or 0,
        "rec_days": row["rec_days"] or 0,
        "matured_total": acc["total"] or 0,
        "matured_hits": acc["hits"] or 0,
        "all_hit_rate": acc["avg_hit_rate"] or 0.0,
        "all_avg_return": acc["avg_return"] or 0.0,
    }


# ============== 차트 ==============

def render_chart(daily: list[dict], output_path: str):
    """일자별 평균수익률(위) + 적중 종목 수(아래) 2단 차트"""
    if not daily:
        # 데이터 없으면 안내 이미지
        fig, ax = plt.subplots(figsize=(8, 3))
        ax.text(0.5, 0.5, "No tracking data yet", ha="center", va="center", fontsize=14)
        ax.axis("off")
        fig.savefig(output_path, dpi=120, bbox_inches="tight")
        plt.close(fig)
        return

    dates = [datetime.fromisoformat(d["rec_date"]).date() for d in daily]
    avg_returns = [d["avg_return"] or 0 for d in daily]
    hits = [d["hits"] or 0 for d in daily]
    totals = [d["total"] or 0 for d in daily]

    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(10, 6), sharex=True)

    # 패널 1: 평균 수익률
    colors = ["#2ECC71" if r >= 0 else "#E74C3C" for r in avg_returns]
    ax1.bar(dates, avg_returns, color=colors, width=0.7, edgecolor="white")
    ax1.axhline(0, color="black", linewidth=0.6)
    ax1.axhline(HIT_THRESHOLD_PCT, color="#3498DB", linewidth=0.8, linestyle="--",
                label=f"Hit threshold ({HIT_THRESHOLD_PCT:.0f}%)")
    ax1.set_ylabel("Avg return (%)")
    ax1.set_title("Daily avg return")
    ax1.legend(loc="upper left", fontsize=8)
    ax1.grid(axis="y", alpha=0.3)

    # 패널 2: 적중 종목 수
    miss_counts = [t - h for t, h in zip(totals, hits)]
    ax2.bar(dates, hits, color="#2ECC71", width=0.7, label="Hit", edgecolor="white")
    ax2.bar(dates, miss_counts, bottom=hits, color="#BDC3C7", width=0.7,
            label="Miss", edgecolor="white")
    ax2.set_ylabel("Stocks")
    ax2.set_title(f"Daily hit count (≥{HIT_THRESHOLD_PCT:.0f}% gain)")
    ax2.legend(loc="upper left", fontsize=8)
    ax2.grid(axis="y", alpha=0.3)

    # 날짜 포맷
    ax2.xaxis.set_major_formatter(mdates.DateFormatter("%m-%d"))
    fig.autofmt_xdate(rotation=45)
    fig.tight_layout()
    fig.savefig(output_path, dpi=120, bbox_inches="tight")
    plt.close(fig)


# ============== 임베드 생성 ==============

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


def build_embeds(week: dict, all_time: dict, sectors: list[dict]) -> list[dict]:
    color = COLOR_GREEN if week["avg_return"] >= 0 else COLOR_RED

    # 1) 요약 임베드
    summary = (
        f"**기간**: {week['start_date']} ~ {week['end_date']} (지난 {week['period_days']}일)\n"
        f"**추천 종목**: {week['total']}개 (추적 중 {week['tracked']}개)\n"
        f"**적중**: {week['hit_count']}개 / {week['tracked']}개 "
        f"= **{week['hit_rate']:.1f}%** (≥{HIT_THRESHOLD_PCT:.0f}% 수익 기준)\n"
        f"**평균 수익률**: **{week['avg_return']:+.2f}%**\n\n"
        f"━━━ 전체 누적 ━━━\n"
        f"누적 추천 종목: {all_time['total_recs']}개 ({all_time['rec_days']}일치)\n"
    )
    if all_time["matured_total"]:
        summary += (
            f"만기 도달(2주 경과): {all_time['matured_hits']}/{all_time['matured_total']} 적중 "
            f"= 평균 {all_time['all_hit_rate']:.1f}%\n"
            f"평균 수익률: {all_time['all_avg_return']:+.2f}%"
        )
    else:
        summary += "만기 도달 추천 없음 (운영 14일 이후 표시)"

    main_embed = {
        "title": "📊 주간 백테스트 리포트",
        "color": color,
        "description": summary[:4096],
        "image": {"url": "attachment://chart.png"},
    }

    # 2) TOP/BOTTOM
    valid = [x for x in week["items"] if x["latest_return"] is not None]
    valid_sorted = sorted(valid, key=lambda x: x["latest_return"], reverse=True)
    top5 = valid_sorted[:5]
    bottom5 = list(reversed(valid_sorted[-5:])) if len(valid_sorted) > 5 else []

    perf_fields = []
    if top5:
        perf_fields.append({
            "name": "🚀 TOP 5",
            "value": "\n".join(_format_stock_line(x) for x in top5)[:1024],
            "inline": False,
        })
    if bottom5:
        perf_fields.append({
            "name": "📉 BOTTOM 5",
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
            "name": "🏷️ 섹터별 평균 수익률",
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

def run_weekly_report():
    logger.info("=== 주간 리포트 생성 시작 ===")

    if not DB_PATH.exists():
        logger.warning("DB 파일 없음 — 리포트 건너뜀")
        return

    week = fetch_period_stats(days=7)
    daily = fetch_daily_aggregates(days=14)
    sectors = fetch_sector_performance(days=30)
    all_time = fetch_all_time_stats()

    logger.info(
        "지난 7일 — 추천 %d개, 추적 %d개, 적중 %d개 (%.1f%%), 평균 %+.2f%%",
        week["total"], week["tracked"], week["hit_count"],
        week["hit_rate"], week["avg_return"],
    )

    # 차트 생성
    tmp = tempfile.NamedTemporaryFile(suffix=".png", delete=False)
    tmp.close()
    try:
        render_chart(daily, tmp.name)
        embeds = build_embeds(week, all_time, sectors)
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
