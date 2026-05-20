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


def format_accuracy_summary() -> str:
    """최근 적중률 요약 (카카오 메시지용)"""
    from database import get_recent_accuracy
    rows = get_recent_accuracy(limit=5)
    if not rows:
        return ""

    lines = ["\n📊 최근 추천 적중률"]
    for row in rows:
        lines.append(
            f"  {row['rec_date']}: {row['hit_count']}/{row['total_stocks']}종목 "
            f"({row['hit_rate_pct']:.0f}%) | 평균 {row['avg_return_pct']:+.1f}%"
        )
    return "\n".join(lines)
