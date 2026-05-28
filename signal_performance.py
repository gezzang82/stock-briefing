"""
과거 추천 결과 기반 시그널 성과 분석.

⚠️  read-only 분석 모듈입니다.
    - 기존 recommendations / snapshot_logs는 절대 변경하지 않음.
    - 가중치/threshold도 변경하지 않음.
    - 결과는 signal_performance_cache 테이블에만 기록.
    - AI 프롬프트에 "참고용"으로만 삽입됨 (실제 로직 변화 없음).

분석 흐름:
    1. 최근 N개월의 recommendations 중 final_return_pct가 채워진 행만 추출
    2. 각 행에 대응하는 snapshot_logs의 candidates[] 에서 시그널값 매칭
       (candidates[].code == recommendations.stock_code, 같은 날짜)
    3. foreign_ratio / volume_ratio / rsi14 / market_regime별 버킷화
    4. 버킷별 avg_return / avg_mfe / avg_mae / win_rate / n 집계
    5. n < 5인 버킷은 표본 부족으로 제외

표본이 전혀 없으면 {}를 반환 (에러 X).
"""
from __future__ import annotations

import json
import logging
from datetime import date, datetime, timedelta

from database import (
    get_conn, init_db,
    save_signal_performance, get_latest_signal_performance,
)
from market_calendar import today_kst

logger = logging.getLogger(__name__)

MIN_SAMPLES_PER_BUCKET = 5
MAX_PROMPT_LINES = 10


# ============== 버킷 정의 ==============

def _bucket_foreign(ratio: float | None) -> str | None:
    """foreign_ratio (decimal, +/-) → 버킷 라벨"""
    if ratio is None:
        return None
    pct = ratio * 100  # snapshot에 decimal로 저장됨 (0.052 → 5.2%)
    if pct >= 5.0:
        return "외국인 비중 ≥5%"
    if pct >= 2.0:
        return "외국인 비중 2~5%"
    if pct >= 0:
        return "외국인 비중 0~2%"
    return "외국인 비중 <0%"


def _bucket_volume(vr: float | None) -> str | None:
    """volume_ratio (당일/직전 5일평균) → 버킷 라벨"""
    if vr is None:
        return None
    if vr >= 3.0:
        return "거래대금 ≥3x"
    if vr >= 2.0:
        return "거래대금 2~3x"
    if vr >= 1.5:
        return "거래대금 1.5~2x"
    return "거래대금 <1.5x"


def _bucket_rsi(rsi: float | None) -> str | None:
    if rsi is None:
        return None
    if rsi < 30:
        return "RSI <30 (과매도)"
    if rsi < 50:
        return "RSI 30~50"
    if rsi < 70:
        return "RSI 50~70"
    return "RSI ≥70 (과열)"


def _bucket_regime(regime: str | None) -> str | None:
    if not regime:
        return None
    labels = {"bullish": "Regime: Bullish",
              "sideways": "Regime: Sideways",
              "bearish": "Regime: Bearish"}
    return labels.get(regime)


BUCKETERS = {
    "foreign_ratio": _bucket_foreign,
    "volume_ratio": _bucket_volume,
    "rsi14": _bucket_rsi,
    "regime": _bucket_regime,
}


# ============== 집계 ==============

def _avg(xs: list[float]) -> float | None:
    if not xs:
        return None
    return sum(xs) / len(xs)


def _aggregate_bucket(rows: list[dict]) -> dict:
    """
    버킷 1개에 속한 추천들의 통계.
    rows: [{"final": x, "mfe": y, "mae": z}, ...]
    """
    finals = [r["final"] for r in rows if r.get("final") is not None]
    mfes = [r["mfe"] for r in rows if r.get("mfe") is not None]
    maes = [r["mae"] for r in rows if r.get("mae") is not None]
    if not finals:
        return {"n": 0}
    wins = sum(1 for f in finals if f > 0)
    return {
        "n": len(finals),
        "avg_return_pct": round(sum(finals) / len(finals), 2),
        "avg_mfe_pct": round(_avg(mfes), 2) if mfes else None,
        "avg_mae_pct": round(_avg(maes), 2) if maes else None,
        "win_rate": round(wins / len(finals) * 100, 1),
    }


# ============== 데이터 적재 ==============

def _fetch_joined_rows(since_date: str) -> list[dict]:
    """
    recommendations ⋈ snapshot_logs (날짜 매칭).
    같은 날짜에 snapshot이 여러 개면 최신(id DESC)을 사용.
    final_return_pct가 NULL인 행은 분석 대상에서 제외.

    Returns: [{
        "rec_date": str, "stock_code": str,
        "final": float, "mfe": float|None, "mae": float|None,
        "regime": str|None,
        "snapshot_json": str,
    }, ...]
    """
    sql = """
        WITH latest_snap AS (
            SELECT snapshot_date, MAX(id) AS max_id
            FROM snapshot_logs
            GROUP BY snapshot_date
        )
        SELECT r.rec_date, r.stock_code,
               r.final_return_pct AS final,
               r.mfe_pct AS mfe,
               r.mae_pct AS mae,
               s.market_regime AS regime,
               s.snapshot_json AS snapshot_json
        FROM recommendations r
        JOIN latest_snap ls ON ls.snapshot_date = r.rec_date
        JOIN snapshot_logs s ON s.id = ls.max_id
        WHERE r.rec_date >= ?
          AND r.final_return_pct IS NOT NULL
    """
    try:
        with get_conn() as conn:
            rows = conn.execute(sql, (since_date,)).fetchall()
    except Exception as e:
        logger.warning("signal_performance JOIN 쿼리 실패: %s", e)
        return []
    return [dict(r) for r in rows]


def _extract_signals_for_stock(snapshot_json: str, stock_code: str) -> dict:
    """
    snapshot_json의 candidates[] 에서 해당 종목의 시그널값 추출.
    매칭 실패 시 빈 dict (분석에서 해당 차원은 자동 스킵).
    """
    try:
        data = json.loads(snapshot_json)
    except Exception:
        return {}
    for cand in data.get("candidates", []) or []:
        if (cand.get("code") or "").strip() == (stock_code or "").strip():
            return {
                "foreign_ratio": cand.get("foreign_ratio"),
                "volume_ratio": cand.get("volume_ratio"),
                "rsi14": cand.get("rsi14"),
            }
    return {}


# ============== 메인 ==============

def analyze_signal_performance(months: int = 3) -> dict:
    """
    최근 N개월 추천의 시그널-결과 매칭 분석.
    표본이 전혀 없으면 {} 반환 (에러 X).
    결과는 signal_performance_cache 테이블에도 저장.
    """
    # 캐시 테이블이 없을 수도 있는 환경(briefing.yml 이전 실행)에서도 안전하게 동작
    try:
        init_db()
    except Exception as e:
        logger.debug("init_db 호출 실패 (무시): %s", e)

    since = (today_kst() - timedelta(days=months * 31)).isoformat()
    joined = _fetch_joined_rows(since)
    if not joined:
        logger.info("signal_performance: 분석 대상 추천 없음 (since=%s)", since)
        return {}

    logger.info(
        "signal_performance: %d개 추천 분석 시작 (since=%s, months=%d)",
        len(joined), since, months,
    )

    # 차원별 버킷 → row 리스트
    bucket_rows: dict[str, dict[str, list[dict]]] = {
        dim: {} for dim in BUCKETERS.keys()
    }

    for row in joined:
        outcome = {
            "final": row.get("final"),
            "mfe": row.get("mfe"),
            "mae": row.get("mae"),
        }
        signals = _extract_signals_for_stock(
            row.get("snapshot_json") or "", row.get("stock_code") or "",
        )

        # 각 차원별로 분류
        # regime은 snapshot_logs 컬럼에서 직접 옴
        signals["regime"] = row.get("regime")

        for dim, bucketer in BUCKETERS.items():
            value = signals.get(dim)
            label = bucketer(value)
            if not label:
                continue
            bucket_rows[dim].setdefault(label, []).append(outcome)

    # 집계 + n < MIN_SAMPLES_PER_BUCKET 제외
    buckets_out: dict[str, list[dict]] = {}
    for dim, by_label in bucket_rows.items():
        items = []
        for label, rows in by_label.items():
            stats = _aggregate_bucket(rows)
            if stats["n"] < MIN_SAMPLES_PER_BUCKET:
                continue
            items.append({"label": label, **stats})
        if items:
            # 평균 수익률 내림차순 정렬 (포맷 시 ★ 우선 노출)
            items.sort(key=lambda x: x["avg_return_pct"], reverse=True)
            buckets_out[dim] = items

    bucket_count = sum(len(v) for v in buckets_out.values())
    result = {
        "months": months,
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "total_samples": len(joined),
        "bucket_count": bucket_count,
        "buckets": buckets_out,
    }

    save_signal_performance(months, result)
    logger.info(
        "signal_performance 완료: 총 표본 %d, 유효 버킷 %d",
        len(joined), bucket_count,
    )
    return result


def load_cached_performance() -> dict:
    """signal_performance_cache에서 최신 결과 로드. 없으면 {}."""
    return get_latest_signal_performance()


# ============== 프롬프트 포맷 ==============

def _format_bucket_line(item: dict) -> str:
    """버킷 1줄 — MFE/MAE 없으면 해당 항목 생략."""
    label = item["label"]
    avg = item["avg_return_pct"]
    n = item["n"]
    win = item["win_rate"]
    mfe = item.get("avg_mfe_pct")
    mae = item.get("avg_mae_pct")

    parts = [f"평균 {avg:+.1f}%"]
    if mfe is not None:
        parts.append(f"MFE {mfe:+.1f}%")
    if mae is not None:
        parts.append(f"MAE {mae:+.1f}%")
    parts.append(f"승률 {win:.0f}%")
    parts.append(f"(n={n})")

    # 표시 가이드 — 자동 가중치 변경은 절대 하지 않음, AI 참고용일 뿐
    marker = ""
    if avg >= 3.0:
        marker = " ★"
    elif avg < 0:
        marker = " ⚠"

    return f"- {label}: {', '.join(parts)}{marker}"


def format_for_prompt(perf: dict) -> str:
    """
    AI 프롬프트에 삽입할 문자열 (참고용).
    perf가 비거나 유효 버킷이 0개면 "" 반환 → 호출자가 섹션 자체를 생략.
    최대 MAX_PROMPT_LINES 줄만 노출.
    """
    if not perf:
        return ""
    buckets = perf.get("buckets") or {}
    if not buckets:
        return ""

    # 모든 차원의 항목을 한 풀로 합친 뒤 avg_return 절댓값 기준 정렬
    # (★/⚠ 양극단을 우선 노출 — 중간은 신호가 약함)
    all_items = []
    for dim, items in buckets.items():
        for it in items:
            all_items.append(it)
    if not all_items:
        return ""

    all_items.sort(
        key=lambda x: abs(x.get("avg_return_pct", 0)),
        reverse=True,
    )
    selected = all_items[:MAX_PROMPT_LINES]

    months = perf.get("months", 3)
    lines = [
        f"=== 최근 {months}개월 시그널 성과 (참고용) ===",
    ]
    for it in selected:
        lines.append(_format_bucket_line(it))
    lines.append(
        "(★ 평균≥3% 양호 / ⚠ 평균<0% 부진 — 종목 선정 시 참고만, "
        "최종 판단은 현재 시장 상황 우선)"
    )
    return "\n".join(lines)


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    result = analyze_signal_performance(months=3)
    print(f"분석 완료: {result.get('bucket_count', 0)}개 버킷")
    if result:
        preview = format_for_prompt(result)
        if preview:
            print()
            print(preview)
