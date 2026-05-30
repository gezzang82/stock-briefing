"""
브리핑 자체 검증 — 09:05 brief 실행 후 시스템 헬스 진단.

목표:
  사용자가 회사 출근 후 매번 카톡/대시보드 확인 안 해도, 시스템이 스스로
  이상 감지 → 문제 발견 시에만 별도 경고 카톡 발송. 정상이면 침묵.

원칙:
  - read-only — DB 조회만 함, 추천/저장/수정 일절 안 함
  - graceful — 어떤 예외에도 sys.exit(0) (workflow 멈춤 X)
  - 정상 → 카톡 발송 X (노이즈 회피)
  - 이상 → 카톡 1회 발송 (사용자 즉시 인지)

검사 항목 (A~E):
  A. 오늘 snapshot 없음 → critical (브리핑 자체 미실행)
  B. 오늘 추천 종목 0개 → warning
  C. 외국인/기관 후보 풀 둘 다 0개 → warning
  D. dropped 중 거래상태 관련 비율 ≥ 70% → warning (KIS API 이상 의심)
  E. snapshot 저장됐는데 selected_recommendations 0개 → warning

사용:
  python health_check.py                    # 일반 실행
  python health_check.py --skip-on-force    # force 모드 시 카톡 발송 skip
"""
from __future__ import annotations

import json
import logging
import os
import sys
from collections import Counter

from database import get_conn
from market_calendar import today_kst

logger = logging.getLogger(__name__)

SEVERITY_CRITICAL = "critical"
SEVERITY_WARNING = "warning"

# D 검사 — dropped 중 거래상태 관련 키워드 매칭 비율 임계
DROP_HIGH_RATIO_THRESHOLD = 0.7
TRADE_STATUS_DROP_KEYWORDS = (
    "거래정지", "매매중단", "관리종목", "정리매매", "단기과열", "시장경고",
)


# ============== 검사 ==============

def check_health(target_date: str | None = None) -> tuple[str, list[tuple[str, str]]]:
    """
    헬스 체크 실행 — DB read-only.
    Returns: (target_date, issues) — issues는 [(severity, message)]
    """
    target = target_date or today_kst().isoformat()
    issues: list[tuple[str, str]] = []

    # A. 오늘 snapshot 존재 여부
    try:
        with get_conn() as conn:
            sn = conn.execute(
                "SELECT id, snapshot_json, recommendation_count, created_at "
                "FROM snapshot_logs WHERE snapshot_date = ? "
                "ORDER BY id DESC LIMIT 1",
                (target,),
            ).fetchone()
    except Exception as e:
        issues.append((SEVERITY_CRITICAL, f"DB 조회 실패: {e}"))
        return target, issues

    if not sn:
        issues.append((SEVERITY_CRITICAL, f"{target} snapshot 없음 — 브리핑 미실행"))
        return target, issues

    # B. 오늘 recommendations 수 (DB 기준)
    try:
        with get_conn() as conn:
            actual_recs = conn.execute(
                "SELECT COUNT(*) FROM recommendations WHERE rec_date = ?",
                (target,),
            ).fetchone()[0]
    except Exception as e:
        issues.append((SEVERITY_WARNING, f"recommendations 조회 실패: {e}"))
        actual_recs = 0

    if actual_recs == 0:
        issues.append((SEVERITY_WARNING, "오늘 추천 종목 0개"))

    # snapshot 파싱
    try:
        data = json.loads(sn["snapshot_json"])
    except Exception as e:
        issues.append((SEVERITY_WARNING, f"snapshot_json 파싱 실패: {e}"))
        return target, issues

    candidates = data.get("candidates") or []
    selected = data.get("selected_recommendations") or []
    dropped = data.get("dropped_recommendations") or []

    # C. candidates의 source 분포
    src_counter: Counter = Counter()
    for c in candidates:
        for s in c.get("sources") or ["volume_rank"]:
            src_counter[s] += 1
    foreign_n = src_counter.get("foreign_buy_rank", 0)
    instit_n = src_counter.get("institution_buy_rank", 0)
    volume_n = src_counter.get("volume_rank", 0)
    if candidates and foreign_n == 0 and instit_n == 0:
        issues.append((
            SEVERITY_WARNING,
            f"외국인/기관 후보 풀 둘 다 0개 (volume_rank만 {volume_n}개)",
        ))

    # D. dropped 중 거래상태 관련 비율
    if dropped:
        total = len(dropped)
        trade_drops = sum(
            1 for d in dropped
            if any(kw in (d.get("drop_reason") or "") for kw in TRADE_STATUS_DROP_KEYWORDS)
        )
        ratio = trade_drops / total if total else 0
        if ratio >= DROP_HIGH_RATIO_THRESHOLD:
            issues.append((
                SEVERITY_WARNING,
                f"KIS 거래상태 탈락 비율 높음: {trade_drops}/{total} "
                f"({ratio * 100:.0f}%) — API 응답 이상 의심",
            ))

    # E. snapshot 있는데 selected 0개 — B와 의미 약간 다름
    #    (B는 DB 기준, E는 snapshot 의도 기준 — force 보호로 DB에 안 들어갔을 수도)
    if len(selected) == 0 and not any("추천 종목 0개" in msg for _, msg in issues):
        issues.append((
            SEVERITY_WARNING,
            "snapshot 저장됨 + selected_recommendations 0개",
        ))

    return target, issues


# ============== 출력 ==============

def format_issues(target: str, issues: list[tuple[str, str]]) -> str:
    """콘솔/로그용 — 정상이면 한 줄, 이상이면 목록."""
    if not issues:
        return f"✅ Health check OK ({target})"
    lines = [f"⚠️ Health check warning ({target})"]
    for sev, msg in issues:
        prefix = "🔴" if sev == SEVERITY_CRITICAL else "🟡"
        lines.append(f"  {prefix} {msg}")
    return "\n".join(lines)


def build_kakao_warning(target: str, issues: list[tuple[str, str]]) -> str:
    """카톡 메시지 — 간결 + 핵심만."""
    lines = ["⚠️ 주식 AI 브리핑 검증 경고", f"날짜: {target}", ""]
    for sev, msg in issues:
        prefix = "🔴" if sev == SEVERITY_CRITICAL else "🟡"
        lines.append(f"{prefix} {msg}")
    lines.append("")
    lines.append("GitHub Actions 로그 확인 필요")
    return "\n".join(lines)


def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
    )

    skip_on_force = "--skip-on-force" in sys.argv
    is_force = os.environ.get("BRIEFING_FORCE_FLAG", "").lower() == "true"

    target, issues = check_health()
    output = format_issues(target, issues)
    print(output)

    # 정상이면 카톡 침묵 (노이즈 회피)
    if not issues:
        return 0

    # force 모드 + skip 옵션 → 디버그 trigger의 경고 카톡 발송 방지
    if skip_on_force and is_force:
        logger.info("FORCE 실행 + --skip-on-force — 경고 카톡 발송 skip")
        return 0

    # 카톡 발송 — 어떤 실패에도 workflow 안 멈춤
    try:
        from kakao_sender import send_message
        msg = build_kakao_warning(target, issues)
        ok = send_message(msg)
        logger.info("Health check 경고 카톡 %s", "전송 성공" if ok else "전송 실패")
    except Exception as e:
        logger.warning("Health check 카톡 발송 실패 (무시): %s", e)

    return 0  # 항상 0 — workflow 실패시키지 않음


if __name__ == "__main__":
    sys.exit(main())
