"""
추천 종목 적중률 계산 모듈
- 매일 추천 종목의 현재가를 DB에 저장
- 2주 후 수익률 계산 및 적중률 집계
"""
import logging
from datetime import date, timedelta

from config import HIT_THRESHOLD_PCT, TRACKING_DAYS
from database import (
    get_mature_recs, get_recommendations_for_tracking,
    get_tracking_data, save_accuracy, save_price_tracking, update_entry_price,
)
from kis_api import kis

logger = logging.getLogger(__name__)


def record_today_prices(rec_date: str):
    """추천일 당일 종가를 price_tracking에 기록"""
    recs = get_recommendations_for_tracking(rec_date)
    if not recs:
        return

    today = date.today().isoformat()
    for r in recs:
        price_data = kis.get_stock_price(r["stock_code"])
        if not price_data:
            continue
        price = price_data["current_price"]
        if not price:
            continue

        # 추천 당일이면 entry_price도 업데이트
        if r["rec_date"] == today:
            update_entry_price(rec_date, r["stock_code"], price)

        save_price_tracking(
            rec_id=r["id"],
            stock_code=r["stock_code"],
            rec_date=rec_date,
            track_date=today,
            price=price,
            entry_price=r["entry_price"] or price,
        )
        logger.debug("[%s] %s: %.0f원", today, r["stock_code"], price)

    logger.info("%s 종목 가격 기록 완료 (%d개)", today, len(recs))


def record_prices_for_active_recs():
    """현재 추적 중인 모든 추천일(2주 미만)에 대해 오늘 가격 기록"""
    today = date.today()
    cutoff = (today - timedelta(days=TRACKING_DAYS)).isoformat()

    from database import get_conn
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT DISTINCT rec_date FROM recommendations WHERE rec_date >= ?",
            (cutoff,),
        ).fetchall()

    for row in rows:
        record_today_prices(row["rec_date"])

    # 가격 기록 후 모든 추천의 MFE/MAE/Final 재계산
    update_all_outcomes()


def _compute_outcome(rec_id: int) -> dict | None:
    """
    특정 추천에 대해 price_tracking 전체 행을 훑어 MFE/MAE/Final 산출.
    Returns: {mfe_pct, mae_pct, final_return_pct, mfe_date, mae_date}
    """
    from database import get_conn
    with get_conn() as conn:
        rows = conn.execute(
            """SELECT track_date, return_pct
               FROM price_tracking
               WHERE recommendation_id = ? AND return_pct IS NOT NULL
               ORDER BY track_date""",
            (rec_id,),
        ).fetchall()
    if not rows:
        return None

    mfe_row = max(rows, key=lambda r: r["return_pct"])
    mae_row = min(rows, key=lambda r: r["return_pct"])
    final_row = rows[-1]
    return {
        "mfe_pct": mfe_row["return_pct"],
        "mae_pct": mae_row["return_pct"],
        "final_return_pct": final_row["return_pct"],
        "mfe_date": mfe_row["track_date"],
        "mae_date": mae_row["track_date"],
    }


def update_all_outcomes():
    """
    추적 데이터가 있는 모든 추천에 대해 MFE/MAE/Final 재계산 후 DB 저장.
    매일 가격 기록 후 호출 (만기 미도달 종목도 진행 중 값을 반영).
    """
    from database import get_conn
    with get_conn() as conn:
        rec_ids = [r["recommendation_id"] for r in conn.execute(
            "SELECT DISTINCT recommendation_id FROM price_tracking"
        ).fetchall()]

    if not rec_ids:
        return

    updated = 0
    with get_conn() as conn:
        for rec_id in rec_ids:
            outcome = _compute_outcome(rec_id)
            if not outcome:
                continue
            conn.execute(
                """UPDATE recommendations
                   SET mfe_pct=?, mae_pct=?, final_return_pct=?,
                       mfe_date=?, mae_date=?,
                       outcome_updated_at=datetime('now','localtime')
                   WHERE id=?""",
                (outcome["mfe_pct"], outcome["mae_pct"], outcome["final_return_pct"],
                 outcome["mfe_date"], outcome["mae_date"], rec_id),
            )
            updated += 1
    logger.info("MFE/MAE/Final 갱신: %d개 추천", updated)


def calculate_accuracy(rec_date: str):
    """특정 추천일의 2주 후 적중률 계산"""
    final_date = (
        date.fromisoformat(rec_date) + timedelta(days=TRACKING_DAYS)
    ).isoformat()

    rows = get_tracking_data(rec_date, final_date)
    if not rows:
        logger.warning("%s: %s 기준 가격 데이터 없음", rec_date, final_date)
        return None

    returns = []
    hits = 0
    for row in rows:
        ret = row["return_pct"]
        if ret is None:
            continue
        returns.append(ret)
        if ret >= HIT_THRESHOLD_PCT:
            hits += 1

    if not returns:
        return None

    hit_rate = hits / len(returns) * 100
    avg_ret = sum(returns) / len(returns)
    save_accuracy(rec_date, len(returns), hits, returns)

    logger.info(
        "%s 적중률: %d/%d (%.1f%%) | 평균수익률: %.2f%%",
        rec_date, hits, len(returns), hit_rate, avg_ret,
    )
    return {
        "rec_date": rec_date,
        "total": len(returns),
        "hits": hits,
        "hit_rate": hit_rate,
        "avg_return": avg_ret,
    }


def update_accuracy():
    """만기(2주 경과)된 추천일에 대해 적중률 계산 실행"""
    today = date.today()
    maturity_cutoff = (today - timedelta(days=TRACKING_DAYS)).isoformat()

    rec_dates = get_mature_recs(maturity_cutoff)
    if not rec_dates:
        logger.info("적중률 계산 대상 없음")
        return

    results = []
    for rec_date in rec_dates:
        result = calculate_accuracy(rec_date)
        if result:
            results.append(result)

    logger.info("적중률 계산 완료: %d건", len(results))
    return results


# ============== 티어 기반 평가 ==============

TIER_LABELS = [
    "+30% 이상",
    "+10~+30%",
    "+5~+10%",
    "0~+5%",
    "-5~0%",
    "-10~-5%",
    "-30~-10%",
    "-30% 이하",
]

# matplotlib 차트용 ASCII 라벨 (Linux 러너 한글 폰트 없음)
TIER_LABELS_EN = [
    "≥ +30%",
    "+10 ~ +30%",
    "+5 ~ +10%",
    "0 ~ +5%",
    "-5 ~ 0%",
    "-10 ~ -5%",
    "-30 ~ -10%",
    "≤ -30%",
]

# 좋음(초록) → 나쁨(빨강) 그라디언트
TIER_COLORS = [
    "#1A9850", "#66BD63", "#A6D96A", "#D9EF8B",
    "#FEE08B", "#FDAE61", "#F46D43", "#A50026",
]


def classify_tier_index(ret: float) -> int:
    """수익률 → 티어 인덱스 (0=최고, 7=최악)"""
    from config import TIER_BOUNDARIES
    for i, boundary in enumerate(TIER_BOUNDARIES):
        if ret >= boundary:
            return i
    return len(TIER_BOUNDARIES)  # < 마지막 경계


def get_monthly_tier_stats(year_month: str | None = None) -> dict:
    """
    특정 월(YYYY-MM)에 추천된 종목들의 만기(2주) 시점 티어 분포.
    None이면 현재 월.
    """
    if year_month is None:
        year_month = date.today().strftime("%Y-%m")

    today = date.today()

    from database import get_conn
    with get_conn() as conn:
        rows = conn.execute(
            """SELECT r.id, r.rec_date, r.mfe_pct, r.mae_pct, r.final_return_pct,
                      (SELECT pt.return_pct FROM price_tracking pt
                       WHERE pt.recommendation_id = r.id
                       ORDER BY pt.track_date DESC LIMIT 1) AS latest_return
               FROM recommendations r
               WHERE substr(r.rec_date, 1, 7) = ?
               ORDER BY r.rec_date, r.rank""",
            (year_month,),
        ).fetchall()

    matured_returns: list[float] = []
    matured_mfe: list[float] = []
    matured_mae: list[float] = []
    in_progress = 0
    pending = 0

    for row in rows:
        rec_date = date.fromisoformat(row["rec_date"])
        days_passed = (today - rec_date).days
        if row["latest_return"] is None:
            pending += 1
            continue
        if days_passed >= TRACKING_DAYS:
            # 만기 도달 — final_return_pct 우선, 없으면 latest
            ret = row["final_return_pct"] if row["final_return_pct"] is not None else row["latest_return"]
            matured_returns.append(ret)
            if row["mfe_pct"] is not None:
                matured_mfe.append(row["mfe_pct"])
            if row["mae_pct"] is not None:
                matured_mae.append(row["mae_pct"])
        else:
            in_progress += 1

    tier_counts = [0] * (len(TIER_LABELS))
    for ret in matured_returns:
        tier_counts[classify_tier_index(ret)] += 1

    n = len(matured_returns)
    return {
        "month": year_month,
        "total_recs": len(rows),
        "matured_count": n,
        "in_progress_count": in_progress,
        "pending_count": pending,
        "tier_counts": tier_counts,
        "tier_labels": TIER_LABELS,
        "avg_return": sum(matured_returns) / n if n else 0.0,
        "win_rate": sum(1 for r in matured_returns if r > 0) / n * 100 if n else 0.0,
        "strong_win_rate": sum(1 for r in matured_returns if r >= 5) / n * 100 if n else 0.0,
        "best": max(matured_returns) if matured_returns else 0.0,
        "worst": min(matured_returns) if matured_returns else 0.0,
        # MFE/MAE 평균 (만기 도달 종목들)
        "avg_mfe": sum(matured_mfe) / len(matured_mfe) if matured_mfe else 0.0,
        "avg_mae": sum(matured_mae) / len(matured_mae) if matured_mae else 0.0,
    }


def get_all_monthly_stats(months_limit: int = 12) -> list[dict]:
    """추천이 존재하는 최근 N개월의 티어 통계 (과거→최신 순)"""
    from database import get_conn
    with get_conn() as conn:
        rows = conn.execute(
            """SELECT DISTINCT substr(rec_date, 1, 7) AS month
               FROM recommendations
               ORDER BY month DESC
               LIMIT ?""",
            (months_limit,),
        ).fetchall()
    months = [r["month"] for r in rows]
    months.reverse()  # 과거 → 최신
    return [get_monthly_tier_stats(m) for m in months]


def format_accuracy_summary() -> str:
    """현재 월 티어 분포 요약 (일일 브리핑 푸터용)"""
    stats = get_monthly_tier_stats()
    month = stats["month"]

    # 아직 만기 종목 없음
    if stats["matured_count"] == 0:
        if stats["total_recs"] == 0:
            return ""
        return (
            f"\n📊 {month} 추적 중\n"
            f"  추천 {stats['total_recs']}개 (만기 미도달, {TRACKING_DAYS}일 후 평가)"
        )

    lines = [
        f"\n📊 {month} 적중 분포 ({stats['matured_count']}개 만기"
    ]
    if stats["in_progress_count"]:
        lines[0] += f", 진행중 {stats['in_progress_count']}개"
    lines[0] += ")"

    total = stats["matured_count"]
    for label, count in zip(TIER_LABELS, stats["tier_counts"]):
        if count == 0:
            continue
        pct = count / total * 100
        bar = "▓" * min(count, 10)
        lines.append(f"  {label:<10} {count:>2}개 ({pct:>4.1f}%) {bar}")

    lines.append(
        f"  평균 {stats['avg_return']:+.2f}% · "
        f"승률(>0%) {stats['win_rate']:.0f}% · "
        f"강승(≥5%) {stats['strong_win_rate']:.0f}%"
    )
    return "\n".join(lines)
