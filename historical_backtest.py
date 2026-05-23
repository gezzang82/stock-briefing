"""
historical_backtest.py — 과거 일자 기준 추천 시스템 시뮬레이션

특정 날짜를 추천일로 가정하고, 그 시점에서 사용 가능했던 데이터만 사용하여
technical_screener의 점수 계산 → 상위 종목 선정 → 14일 후 성과 측정.

제약:
  - KIS inquire-investor가 ~30일치만 반환 → 백테스트 기간 30일 한도
  - Naver 뉴스는 날짜 쿼리 불가 → 뉴스 입력 제외 (수급 + 기술 지표만)
  - 유니버스가 현재 거래량 상위 N개 → 생존 편향 (실제로는 그 시점의 거래량
    상위가 달랐을 수 있음)

사용:
  python historical_backtest.py --start 2026-04-22 --end 2026-05-15
  python historical_backtest.py --start 2026-05-08 --end 2026-05-22 \
        --top-n 10 --min-score 50 --universe 100

출력:
  - 일자별 추천 종목 + MFE/MAE/Final (콘솔)
  - 누적 지표: 승률 / 평균 수익률 / 샤프비율 / MDD (콘솔)
  - 상세 JSON: backtest_result.json
"""
import argparse
import json
import logging
import time
from datetime import date, datetime, timedelta
from pathlib import Path

from config import MIN_SCORE_THRESHOLD, TOP_N_STOCKS, TRACKING_DAYS
from kis_api import kis
from market_calendar import is_krx_closed
from technical_screener import (
    compute_indicators, get_volume_ranking, score_candidate, get_daily_ohlcv,
)

logger = logging.getLogger(__name__)


# ============== 유니버스 + 히스토리 수집 ==============

def get_universe(size: int = 100) -> list[str]:
    """현재 거래량 상위 N개 종목 코드 (ETF/SPAC 필터링 후)"""
    candidates = get_volume_ranking(count=size)
    codes = [c["code"] for c in candidates if c.get("code")]
    logger.info("유니버스 정의: %d개 종목 (현재 거래량 상위)", len(codes))
    return codes


def fetch_history(codes: list[str], lookback_days: int = 90) -> dict:
    """
    각 종목의 OHLCV + 투자자 매매동향을 한 번에 캐싱.
    백테스트 루프에서는 KIS 호출 없이 이 cache만 사용.

    Returns: {code: {"ohlcv": [...], "investor": [...]}}
    """
    logger.info(
        "히스토리 수집 시작: %d종목 × 2endpoint (~%d초 소요 예상)",
        len(codes), int(len(codes) * 0.3),
    )
    data = {}
    for i, code in enumerate(codes, 1):
        ohlcv = get_daily_ohlcv(code, days=lookback_days)
        time.sleep(0.1)
        if not ohlcv or len(ohlcv) < 30:
            continue
        investor = kis.get_investor_trend(code, days=30)
        time.sleep(0.1)
        data[code] = {"ohlcv": ohlcv, "investor": investor}
        if i % 20 == 0:
            logger.info("  ...%d/%d 종목 완료", i, len(codes))
    logger.info("히스토리 수집 완료: %d개 종목 (요청 %d개)", len(data), len(codes))
    return data


# ============== 시점 기준 지표 계산 ==============

def _bars_up_to(ohlcv: list[dict], target_yyyymmdd: str) -> list[dict]:
    """target_date(포함) 이전의 bars만"""
    return [b for b in ohlcv if b["date"] <= target_yyyymmdd]


def _bars_after(ohlcv: list[dict], start_yyyymmdd: str, days: int) -> list[dict]:
    """start_date 이후 (start 제외) 최대 days 영업일치"""
    after = [b for b in ohlcv if b["date"] > start_yyyymmdd]
    return after[:days]


def _supply_at(bars: list[dict], inv_rows: list[dict],
               target_yyyymmdd: str) -> dict:
    """과거 시점의 수급 지표 (technical_screener.compute_supply_demand 시점판 버전)"""
    inv_until = [i for i in inv_rows if i["date"] <= target_yyyymmdd]
    last_5_inv = inv_until[-5:] if len(inv_until) >= 5 else inv_until
    foreign_5d = sum(d["foreign_value"] for d in last_5_inv)  # 백만원
    instit_5d = sum(d["instit_value"] for d in last_5_inv)

    foreign_ratio = 0.0
    instit_ratio = 0.0
    value_ratio = 1.0
    if len(bars) >= 6:
        daily_values = [b["close"] * b["volume"] for b in bars[-6:]]
        today_value = daily_values[-1]
        prev_5d_avg = sum(daily_values[:-1]) / 5 if sum(daily_values[:-1]) else 0
        value_ratio = today_value / prev_5d_avg if prev_5d_avg else 1.0
        total_5d_value = sum(daily_values[-6:-1])
        if total_5d_value > 0:
            foreign_ratio = (foreign_5d * 1_000_000) / total_5d_value
            instit_ratio = (instit_5d * 1_000_000) / total_5d_value

    return {
        "foreign_5d_million": foreign_5d,
        "instit_5d_million": instit_5d,
        "foreign_ratio": foreign_ratio,
        "instit_ratio": instit_ratio,
        "value_ratio": value_ratio,
        "program_net_won": 0,  # 백테스트에서는 프로그램 매매 생략
    }


# ============== 단일 일자 백테스트 ==============

def backtest_one_date(target_date: date, history: dict,
                      top_n: int = 10, min_score: float = 50.0,
                      weights: dict | None = None,
                      tracking_days: int = TRACKING_DAYS) -> dict:
    """
    target_date를 추천일로 가정.
      1) 각 유니버스 종목에 대해 target_date까지의 데이터로 지표/점수 계산
      2) 점수 ≥ min_score인 종목을 상위 top_n개 선정
      3) target_date+1 ~ target_date+tracking_days의 가격으로 MFE/MAE/Final
    """
    tgt = target_date.strftime("%Y%m%d")
    selected = []

    for code, d in history.items():
        bars_until = _bars_up_to(d["ohlcv"], tgt)
        if len(bars_until) < 30:
            continue
        ind = compute_indicators(bars_until)
        if not ind:
            continue
        supply = _supply_at(bars_until, d["investor"], tgt)
        score, signals = score_candidate(ind, supply, weights=weights)
        if score < min_score:
            continue
        selected.append({
            "code": code,
            "score": score,
            "signals": signals,
            "entry_price": bars_until[-1]["close"],
            "future_bars": _bars_after(d["ohlcv"], tgt, tracking_days),
        })

    selected.sort(key=lambda x: x["score"], reverse=True)
    picks = selected[:top_n]

    # Forward returns
    results = []
    for p in picks:
        if len(p["future_bars"]) < 3:
            continue
        entry = p["entry_price"]
        if entry <= 0:
            continue
        returns = [(b["close"] / entry - 1) * 100 for b in p["future_bars"]]
        results.append({
            "code": p["code"],
            "score": round(p["score"], 1),
            "entry_price": entry,
            "tracked_days": len(p["future_bars"]),
            "mfe_pct": max(returns),
            "mae_pct": min(returns),
            "final_pct": returns[-1],
            "win": returns[-1] > 0,
        })

    avg = sum(r["final_pct"] for r in results) / len(results) if results else 0
    return {
        "date": target_date.isoformat(),
        "n_qualified": len(selected),
        "n_picks": len(results),
        "avg_final_pct": avg,
        "results": results,
    }


# ============== 누적 지표 ==============

def aggregate_metrics(date_results: list[dict]) -> dict:
    """승률 / 평균 수익률 / 샤프비율 / MDD"""
    all_finals = []
    daily_avgs: list[tuple[str, float]] = []

    for dr in date_results:
        rets = [r["final_pct"] for r in dr["results"]]
        if rets:
            all_finals.extend(rets)
            daily_avgs.append((dr["date"], sum(rets) / len(rets)))

    if not all_finals:
        return {
            "total_picks": 0, "n_dates": 0,
            "win_rate": 0.0, "avg_return": 0.0,
            "sharpe": 0.0, "mdd": 0.0,
            "best_pick": None, "worst_pick": None,
        }

    win_rate = sum(1 for r in all_finals if r > 0) / len(all_finals) * 100
    avg_return = sum(all_finals) / len(all_finals)

    # Sharpe (raw — 14일 수익률 기준, 미연환산)
    n = len(all_finals)
    variance = sum((r - avg_return) ** 2 for r in all_finals) / n
    std = variance ** 0.5
    sharpe = avg_return / std if std > 0 else 0.0

    # MDD — 일자별 평균 수익률 누적 곡선의 최대 낙폭
    daily_avgs.sort()
    cumulative: list[float] = []
    cum = 0.0
    for _, avg in daily_avgs:
        cum += avg
        cumulative.append(cum)
    if cumulative:
        peak = cumulative[0]
        max_dd = 0.0
        for v in cumulative:
            if v > peak:
                peak = v
            max_dd = max(max_dd, peak - v)
    else:
        max_dd = 0.0

    return {
        "total_picks": len(all_finals),
        "n_dates": len(daily_avgs),
        "win_rate": win_rate,
        "avg_return": avg_return,
        "sharpe": sharpe,
        "mdd": max_dd,
        "best_pick": max(all_finals),
        "worst_pick": min(all_finals),
        "equity_curve": [
            {"date": d, "cum_return": cumulative[i]}
            for i, (d, _) in enumerate(daily_avgs)
        ],
    }


# ============== 전체 실행 ==============

def run_backtest(start_date: date, end_date: date,
                 top_n: int = 10, min_score: float = MIN_SCORE_THRESHOLD,
                 universe_size: int = 100, weights: dict | None = None,
                 tracking_days: int = TRACKING_DAYS) -> dict:
    """
    start_date ~ end_date 범위 영업일마다 backtest_one_date 호출.
    """
    today = date.today()
    # 30일 한도 (KIS investor 제약). end_date가 미래면 today로 cap.
    if end_date > today:
        logger.warning("end_date %s → today %s로 cap", end_date, today)
        end_date = today
    # forward 14일 데이터가 있어야 하므로 end_date는 today-14가 안전한 상한
    safe_end = today - timedelta(days=tracking_days)
    if end_date > safe_end:
        logger.warning(
            "end_date %s → forward %d일 데이터 부족 가능, 일부 결과 짧을 수 있음",
            end_date, tracking_days,
        )
    earliest = today - timedelta(days=30)
    if start_date < earliest:
        logger.warning(
            "start_date %s가 KIS 투자자 데이터 범위 밖 → %s로 조정",
            start_date, earliest,
        )
        start_date = earliest

    universe = get_universe(size=universe_size)
    history = fetch_history(universe, lookback_days=90)
    if not history:
        return {"error": "히스토리 수집 실패", "metrics": aggregate_metrics([])}

    date_results = []
    cur = start_date
    while cur <= end_date:
        closed, reason = is_krx_closed(cur)
        if closed:
            logger.debug("%s: 휴장 (%s) — 스킵", cur, reason)
        else:
            result = backtest_one_date(
                cur, history, top_n=top_n, min_score=min_score,
                weights=weights, tracking_days=tracking_days,
            )
            date_results.append(result)
            logger.info(
                "%s | 자격 %d개 / 추적 %d개 / 평균 %+.2f%%",
                cur.isoformat(), result["n_qualified"],
                result["n_picks"], result["avg_final_pct"],
            )
        cur += timedelta(days=1)

    metrics = aggregate_metrics(date_results)
    return {
        "period": f"{start_date.isoformat()} ~ {end_date.isoformat()}",
        "settings": {
            "top_n": top_n, "min_score": min_score,
            "universe_size": universe_size, "tracking_days": tracking_days,
            "weights": weights or "default",
        },
        "metrics": metrics,
        "daily": date_results,
    }


# ============== CLI ==============

def _print_summary(result: dict):
    m = result["metrics"]
    print()
    print("=" * 60)
    print(f"📊 백테스트 결과 ({result['period']})")
    print("=" * 60)
    s = result["settings"]
    print(f"설정: 유니버스 {s['universe_size']} / Top {s['top_n']} / "
          f"Min Score {s['min_score']} / Track {s['tracking_days']}일")
    print()
    print(f"  거래일:        {m['n_dates']}일")
    print(f"  누적 추천:     {m['total_picks']}건")
    print(f"  ─────────────────────────────────────")
    print(f"  승률:          {m['win_rate']:>6.2f}%")
    print(f"  평균 수익률:   {m['avg_return']:>+6.2f}%")
    print(f"  샤프비율:      {m['sharpe']:>+6.3f}  (14일 수익률 기준, raw)")
    print(f"  MDD:           {m['mdd']:>6.2f}%  (일자별 평균 누적 기준)")
    print(f"  ─────────────────────────────────────")
    if m.get("best_pick") is not None:
        print(f"  최고:          {m['best_pick']:>+6.2f}%")
        print(f"  최저:          {m['worst_pick']:>+6.2f}%")
    print()


def main():
    parser = argparse.ArgumentParser(
        description="historical_backtest — 과거 일자 기준 전략 시뮬레이션"
    )
    parser.add_argument("--start", required=True, help="시작일 YYYY-MM-DD")
    parser.add_argument("--end", required=True, help="종료일 YYYY-MM-DD")
    parser.add_argument("--top-n", type=int, default=TOP_N_STOCKS)
    parser.add_argument("--min-score", type=float, default=MIN_SCORE_THRESHOLD)
    parser.add_argument("--universe", type=int, default=100,
                        help="현재 거래량 상위 N종목으로 유니버스 정의")
    parser.add_argument("--tracking-days", type=int, default=TRACKING_DAYS)
    parser.add_argument("--regime", choices=["bullish", "sideways", "bearish"],
                        help="가중치 강제 적용 (기본: technical_screener 기본)")
    parser.add_argument("--output", default="backtest_result.json")
    args = parser.parse_args()

    start = date.fromisoformat(args.start)
    end = date.fromisoformat(args.end)

    weights = None
    if args.regime:
        from market_regime import get_weights_for_regime
        weights = get_weights_for_regime(args.regime)
        logger.info("가중치 강제 적용: %s", weights)

    result = run_backtest(
        start, end,
        top_n=args.top_n, min_score=args.min_score,
        universe_size=args.universe, weights=weights,
        tracking_days=args.tracking_days,
    )

    _print_summary(result)

    Path(args.output).write_text(
        json.dumps(result, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )
    print(f"📄 상세 JSON: {args.output}")


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    main()
