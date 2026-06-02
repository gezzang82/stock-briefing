"""
관측 보고서 — 최근 N일 EOD 실행의 누적 분석.

목적:
  BLNG 변경 등 전략 결정을 단일 실행이 아닌 누적 데이터 기반으로 함.

사용:
  python report_observations.py              # 최근 5 영업일
  python report_observations.py --days 10    # 최근 10 영업일

CASE 판정:
  CASE A: 3회 이상 연속 거래량 후보 ≤ 2 → BLNG=3 전환 검토
  CASE B: 정상 (10+ 후보) → 현행 유지
  중간 상태: 추가 관측 필요

⚠️ DB read-only. 카톡 발송 X.
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from collections import Counter
from datetime import timedelta

from database import get_conn
from market_calendar import today_kst


def fetch_recent_observations(days: int) -> list[dict]:
    """최근 N일 동안 snapshot이 있는 날짜의 관측 데이터 수집."""
    since = (today_kst() - timedelta(days=days * 2)).isoformat()  # 휴장일 고려 ×2
    with get_conn() as conn:
        rows = conn.execute(
            """SELECT snapshot_date, snapshot_json, recommendation_count, created_at
               FROM snapshot_logs
               WHERE snapshot_date >= ?
               ORDER BY snapshot_date DESC, id DESC""",
            (since,),
        ).fetchall()

    # 같은 날짜 여러 snapshot이면 가장 최근 1개
    seen = set()
    unique = []
    for r in rows:
        if r["snapshot_date"] in seen:
            continue
        seen.add(r["snapshot_date"])
        unique.append(r)
        if len(unique) >= days:
            break

    observations = []
    for r in reversed(unique):  # 과거 → 최신 순
        try:
            data = json.loads(r["snapshot_json"])
        except Exception:
            continue
        candidates = data.get("candidates") or []
        selected = data.get("selected_recommendations") or []
        dropped = data.get("dropped_recommendations") or []

        src = Counter()
        for c in candidates:
            for s in c.get("sources") or ["volume_rank"]:
                src[s] += 1

        observations.append({
            "date": r["snapshot_date"],
            "tech_candidates": len(candidates),
            "volume_n": src.get("volume_rank", 0),
            "foreign_n": src.get("foreign_buy_rank", 0),
            "institution_n": src.get("institution_buy_rank", 0),
            "selected": len(selected),
            "dropped": len(dropped),
            "rec_count": r["recommendation_count"] or 0,
        })
    return observations


def judge_case(observations: list[dict]) -> tuple[str, str]:
    """CASE A/B/중간 판정."""
    if len(observations) < 3:
        return "DATA_INSUFFICIENT", "관측 데이터 부족 — 최소 3 영업일 필요"

    # 최근 N개에서 거래량 후보 ≤ 2 카운트
    low_volume_count = sum(1 for o in observations if o["volume_n"] <= 2)
    normal_count = sum(1 for o in observations if o["volume_n"] >= 10)

    if low_volume_count >= 3:
        return "CASE_A", (
            f"거래량 후보 ≤2인 날이 {low_volume_count}/{len(observations)}일 — "
            f"BLNG=3 전환 검토 필요"
        )
    if normal_count >= len(observations) // 2:
        return "CASE_B", (
            f"거래량 후보 10+ 인 날이 {normal_count}/{len(observations)}일 — "
            f"현행 BLNG=1 유지"
        )
    return "MIXED", (
        f"혼재 (≤2: {low_volume_count}, ≥10: {normal_count}, 중간: "
        f"{len(observations) - low_volume_count - normal_count}) — 추가 관측 권장"
    )


def main():
    parser = argparse.ArgumentParser(description="EOD 실행 누적 관측 보고서")
    parser.add_argument("--days", type=int, default=5, help="최근 N 영업일 (기본 5)")
    args = parser.parse_args()

    observations = fetch_recent_observations(args.days)

    print()
    print("=" * 70)
    print(f"  📊 EOD 실행 누적 관측 보고서 (최근 {args.days} 영업일)")
    print("=" * 70)
    print()

    if not observations:
        print("  ❌ 관측 데이터 없음 — snapshot_logs 비어있음")
        return 0

    # 표 헤더
    print(f"  {'날짜':12s} | {'후보':>4s} | {'거래량':>5s} | {'외인':>4s} | "
          f"{'기관':>4s} | {'선정':>4s} | {'추천':>4s}")
    print("  " + "-" * 65)
    for o in observations:
        print(f"  {o['date']:12s} | {o['tech_candidates']:>4d} | "
              f"{o['volume_n']:>5d} | {o['foreign_n']:>4d} | "
              f"{o['institution_n']:>4d} | {o['selected']:>4d} | {o['rec_count']:>4d}")

    print()

    # 통계
    print("  통계 (관측 기간):")
    vol_vals = [o["volume_n"] for o in observations]
    fgn_vals = [o["foreign_n"] for o in observations]
    ist_vals = [o["institution_n"] for o in observations]
    rec_vals = [o["rec_count"] for o in observations]
    print(f"    거래량 후보 — 평균 {sum(vol_vals)/len(vol_vals):.1f} / "
          f"최소 {min(vol_vals)} / 최대 {max(vol_vals)}")
    print(f"    외국인 후보 — 평균 {sum(fgn_vals)/len(fgn_vals):.1f} / "
          f"최소 {min(fgn_vals)} / 최대 {max(fgn_vals)}")
    print(f"    기관 후보   — 평균 {sum(ist_vals)/len(ist_vals):.1f} / "
          f"최소 {min(ist_vals)} / 최대 {max(ist_vals)}")
    print(f"    최종 추천   — 평균 {sum(rec_vals)/len(rec_vals):.1f} / "
          f"최소 {min(rec_vals)} / 최대 {max(rec_vals)}")
    print()

    # CASE 판정
    case, verdict = judge_case(observations)
    icon = {"CASE_A": "🚨", "CASE_B": "✅", "MIXED": "🟡", "DATA_INSUFFICIENT": "ℹ️"}
    print(f"  {icon.get(case, '?')} 판정 ({case}): {verdict}")
    print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
