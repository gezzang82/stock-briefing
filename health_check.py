"""
시스템 헬스 체크 — 두 가지 모드.

1) 시스템 진단 (기본) — 외부 API 연결 + DB + dry-run 등 7가지 검사
   사용: python health_check.py
   목적: 사용자가 수동으로 전체 시스템 상태 점검

2) Brief 후 자체 검증 (--post-brief)
   사용: python health_check.py --post-brief [--skip-on-force]
   목적: 09:05 brief 자동 실행 직후 snapshot 기반 결과 진단,
         이상 시 경고 카톡 발송

⚠️ 두 모드 모두:
   - DB 저장 X (read-only)
   - 카톡 발송 X (단, --post-brief 모드에서 이상 발견 시는 발송)
"""
from __future__ import annotations

import json
import logging
import os
import sys
from collections import Counter
from dataclasses import dataclass, field
from typing import Callable

from database import get_conn
from market_calendar import today_kst

logger = logging.getLogger(__name__)


# ============================================================
#  Mode 1: 시스템 진단 (기본 모드)
# ============================================================

@dataclass
class CheckResult:
    name: str
    status: str  # "PASS" / "FAIL" / "WARN"
    detail: str = ""
    cause: str = ""
    fix: str = ""

    def is_ok(self) -> bool:
        return self.status == "PASS"


# ----- 개별 검사 함수 -----

def check_kis_api() -> CheckResult:
    """1. KIS API 연결 상태 — 토큰 발급 + 삼성전자 시세 조회."""
    try:
        from kis_api import kis
        token = kis._get_token()
        if not token:
            return CheckResult(
                "KIS API 연결", "FAIL",
                cause="토큰 발급 결과가 비어있음",
                fix="KIS_APP_KEY/KIS_APP_SECRET 환경변수 확인",
            )
        # 검증: 삼성전자(005930) 시세 조회
        data = kis.get_stock_price("005930")
        if not data:
            return CheckResult(
                "KIS API 연결", "WARN",
                detail=f"토큰 발급 OK (prefix={token[:10]}...) 하지만 시세 조회 빈 응답",
                cause="장 운영 외 시간 또는 종목별 응답 이상",
                fix="장 운영 시간(09:00~15:30 KST)에 다시 실행",
            )
        return CheckResult(
            "KIS API 연결", "PASS",
            detail=f"토큰 OK (prefix={token[:10]}...) | 005930 현재가={data.get('current_price')}",
        )
    except Exception as e:
        msg = str(e)
        fix = "KIS 등록 IP 확인 (https://apiportal.koreainvestment.com)"
        if "403" in msg:
            fix = "현재 IP가 KIS 등록 IP가 아님 — 포털에서 IP 추가 (curl ifconfig.me)"
        elif "401" in msg:
            fix = "KIS_APP_KEY/SECRET 만료 또는 잘못됨"
        return CheckResult(
            "KIS API 연결", "FAIL",
            cause=msg[:120],
            fix=fix,
        )


def check_kakao_token() -> CheckResult:
    """2. 카카오 토큰 유효성 — 비파괴 API로 토큰 정보 조회."""
    try:
        import requests
        from config import KAKAO_ACCESS_TOKEN
        if not KAKAO_ACCESS_TOKEN:
            return CheckResult(
                "카카오 토큰", "FAIL",
                cause="KAKAO_ACCESS_TOKEN 환경변수 비어있음",
                fix=".env에 KAKAO_ACCESS_TOKEN 설정 또는 kakao_auth.py로 재발급",
            )
        # 비파괴 endpoint: access token info (GET) — 메시지 발송 안 함
        resp = requests.get(
            "https://kapi.kakao.com/v1/user/access_token_info",
            headers={"Authorization": f"Bearer {KAKAO_ACCESS_TOKEN}"},
            timeout=10,
        )
        if resp.status_code == 200:
            body = resp.json()
            expires = body.get("expires_in", "?")
            return CheckResult(
                "카카오 토큰", "PASS",
                detail=f"유효 토큰 (남은 만료 {expires}초)",
            )
        if resp.status_code == 401:
            return CheckResult(
                "카카오 토큰", "FAIL",
                cause=f"401 토큰 만료 또는 무효 ({resp.text[:80]})",
                fix="kakao_auth.py로 refresh 또는 kakao_auth.py로 신규 발급",
            )
        return CheckResult(
            "카카오 토큰", "WARN",
            detail=f"HTTP {resp.status_code}: {resp.text[:80]}",
            cause="예상치 못한 응답 코드",
            fix="카카오 API 상태 확인",
        )
    except Exception as e:
        return CheckResult(
            "카카오 토큰", "FAIL",
            cause=str(e)[:120],
            fix="네트워크 또는 환경변수 확인",
        )


def check_naver_api() -> CheckResult:
    """3. 네이버 뉴스 API 연결 — '주식' 1건 검색 시도."""
    try:
        import requests
        from config import NAVER_CLIENT_ID, NAVER_CLIENT_SECRET
        if not NAVER_CLIENT_ID or not NAVER_CLIENT_SECRET:
            return CheckResult(
                "네이버 API", "FAIL",
                cause="NAVER_CLIENT_ID/SECRET 비어있음",
                fix=".env에 NAVER_CLIENT_ID, NAVER_CLIENT_SECRET 설정",
            )
        resp = requests.get(
            "https://openapi.naver.com/v1/search/news.json",
            headers={
                "X-Naver-Client-Id": NAVER_CLIENT_ID,
                "X-Naver-Client-Secret": NAVER_CLIENT_SECRET,
            },
            params={"query": "주식", "display": 1},
            timeout=10,
        )
        if resp.status_code == 200:
            body = resp.json()
            total = body.get("total", 0)
            return CheckResult(
                "네이버 API", "PASS",
                detail=f"검색 OK (total={total}건 가용)",
            )
        return CheckResult(
            "네이버 API", "FAIL",
            cause=f"HTTP {resp.status_code}: {resp.text[:120]}",
            fix="네이버 개발자센터에서 API 키/quota 확인",
        )
    except Exception as e:
        return CheckResult(
            "네이버 API", "FAIL",
            cause=str(e)[:120],
            fix="네트워크 또는 환경변수 확인",
        )


def check_db_integrity() -> CheckResult:
    """4. DB 무결성 — 주요 테이블 존재 + row count."""
    expected_tables = (
        "recommendations", "price_tracking", "accuracy_results",
        "snapshot_logs", "signal_performance_cache",
    )
    try:
        with get_conn() as conn:
            existing = {
                r[0] for r in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                )
            }
            missing = [t for t in expected_tables if t not in existing]
            if missing:
                return CheckResult(
                    "DB 무결성", "FAIL",
                    cause=f"필수 테이블 누락: {missing}",
                    fix="database.init_db() 실행 또는 stock_briefing.db 재초기화",
                )
            counts = {}
            for t in expected_tables:
                c = conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
                counts[t] = c
        return CheckResult(
            "DB 무결성", "PASS",
            detail=", ".join(f"{k}={v}" for k, v in counts.items()),
        )
    except Exception as e:
        return CheckResult(
            "DB 무결성", "FAIL",
            cause=str(e)[:120],
            fix="stock_briefing.db 파일 권한/경로 확인",
        )


def check_foreign_institution_api() -> CheckResult:
    """5. 외국인/기관 매수 상위 API (FHPTJ04400000) 정상 응답."""
    try:
        from kis_api import kis
        result = kis.get_foreign_buy_ranking(count=5)
        # 응답이 빈 list일 수 있음 (장 운영 외 시간)
        return CheckResult(
            "외국인/기관 매수상위 API", "PASS" if result else "WARN",
            detail=(
                f"응답 OK ({len(result)}건)" if result else
                "응답 정상 (rt_cd=0)이지만 결과 0건 — 장 운영 외 시간 또는 시장 데이터 없음"
            ),
            cause="" if result else "장 운영 시간(09:00~15:30 KST)이 아님",
            fix="" if result else "장 운영 중에 재실행",
        )
    except Exception as e:
        return CheckResult(
            "외국인/기관 매수상위 API", "FAIL",
            cause=str(e)[:120],
            fix="kis_api._get_investor_buy_ranking endpoint/파라미터 검토",
        )


def check_recommendation_dry_run() -> CheckResult:
    """
    6. 추천 로직 dry-run — KIS 호출 흐름까지만 검증 (저장/AI/카톡 X).
    market_regime 판단 + technical_screener 후보 추출.
    """
    try:
        from market_regime import detect_regime
        from technical_screener import get_volume_ranking

        regime_info = detect_regime()
        regime = regime_info.get("regime", "?")
        score = regime_info.get("score", "?")

        # 거래량 순위로 후보 5개만 빠르게 (전체 스크리닝은 시간 오래 걸려 dry-run엔 부담)
        candidates = get_volume_ranking(count=5)

        return CheckResult(
            "추천 로직 dry-run", "PASS" if candidates else "WARN",
            detail=(
                f"regime={regime}({score}) | 거래량 후보 {len(candidates)}개 "
                f"({', '.join(c['name'] for c in candidates[:3])})"
                if candidates else
                f"regime={regime}({score}) | 거래량 후보 0건 (장 외 시간)"
            ),
            cause="" if candidates else "장 운영 시간이 아니거나 KIS volume-rank 빈 응답",
            fix="" if candidates else "장 운영 중 재실행",
        )
    except Exception as e:
        return CheckResult(
            "추천 로직 dry-run", "FAIL",
            cause=str(e)[:120],
            fix="market_regime / technical_screener 로직 검토",
        )


def check_cron_job_org_trigger() -> CheckResult:
    """
    7. cron-job.org 트리거 응답 확인 — GitHub Actions의 최근
    workflow_dispatch run 시각으로 간접 확인 (gh CLI 사용).
    """
    import subprocess
    try:
        out = subprocess.run(
            ["gh", "run", "list", "--workflow=briefing.yml",
             "--limit", "5", "--event=workflow_dispatch",
             "--json", "createdAt,conclusion"],
            capture_output=True, text=True, timeout=15,
        )
        if out.returncode != 0:
            return CheckResult(
                "cron-job.org 트리거", "WARN",
                cause=f"gh CLI 실행 실패: {out.stderr[:100]}",
                fix="GitHub CLI(gh) 설치 + 'gh auth login' 또는 GitHub Actions UI에서 직접 확인",
            )
        runs = json.loads(out.stdout) if out.stdout.strip() else []
        if not runs:
            return CheckResult(
                "cron-job.org 트리거", "WARN",
                detail="최근 workflow_dispatch run 없음",
                cause="cron-job.org가 호출한 적 없거나 7일 이상 지남",
                fix="cron-job.org 잡 enabled 확인 + TEST RUN 시도",
            )
        latest = runs[0]
        return CheckResult(
            "cron-job.org 트리거", "PASS",
            detail=f"최근 dispatch: {latest['createdAt']} ({latest['conclusion']})",
        )
    except FileNotFoundError:
        return CheckResult(
            "cron-job.org 트리거", "WARN",
            cause="gh CLI 미설치",
            fix="brew install gh && gh auth login",
        )
    except Exception as e:
        return CheckResult(
            "cron-job.org 트리거", "WARN",
            cause=str(e)[:120],
            fix="GitHub Actions UI 직접 확인: https://github.com/gezzang82/stock-briefing/actions/workflows/briefing.yml",
        )


# ----- 시스템 진단 실행기 -----

SYSTEM_CHECKS: list[tuple[str, Callable[[], CheckResult]]] = [
    ("1", check_kis_api),
    ("2", check_kakao_token),
    ("3", check_naver_api),
    ("4", check_db_integrity),
    ("5", check_foreign_institution_api),
    ("6", check_recommendation_dry_run),
    ("7", check_cron_job_org_trigger),
]


def run_system_diagnostic() -> int:
    """
    7가지 시스템 진단 실행 → PASS/FAIL 출력 → 실패 시 원인+해결법.
    return code: 0 (전부 PASS/WARN) | 1 (1개 이상 FAIL)
    """
    print("=" * 70)
    print(f"  🔍 stock-briefing 시스템 진단 ({today_kst()})")
    print("=" * 70)
    print()

    results: list[CheckResult] = []
    for num, fn in SYSTEM_CHECKS:
        r = fn()
        results.append(r)

        icon = {"PASS": "✅", "WARN": "🟡", "FAIL": "❌"}.get(r.status, "❓")
        print(f"{icon} [{r.status}] {num}. {r.name}")
        if r.detail:
            print(f"     → {r.detail}")
        if r.status != "PASS":
            if r.cause:
                print(f"     원인: {r.cause}")
            if r.fix:
                print(f"     해결: {r.fix}")
        print()

    n_pass = sum(1 for r in results if r.status == "PASS")
    n_warn = sum(1 for r in results if r.status == "WARN")
    n_fail = sum(1 for r in results if r.status == "FAIL")

    print("=" * 70)
    print(f"  결과: PASS {n_pass} / WARN {n_warn} / FAIL {n_fail}")
    print("=" * 70)

    return 0 if n_fail == 0 else 1


# ============================================================
#  Mode 2: Post-brief 자체 검증 (--post-brief)
#  09:05 brief 후 snapshot 기반 결과 진단.
#  이상 발견 시 경고 카톡 발송 (단, --skip-on-force + force 모드면 skip).
# ============================================================

SEVERITY_CRITICAL = "critical"
SEVERITY_WARNING = "warning"

DROP_HIGH_RATIO_THRESHOLD = 0.7
TRADE_STATUS_DROP_KEYWORDS = (
    "거래정지", "매매중단", "관리종목", "정리매매", "단기과열", "시장경고",
)


def check_health(target_date: str | None = None) -> tuple[str, list[tuple[str, str]]]:
    """기존 함수 — snapshot 기반 검사. issues = [(severity, message)]."""
    target = target_date or today_kst().isoformat()
    issues: list[tuple[str, str]] = []

    # A. snapshot 존재 여부
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

    # B. recommendations 수
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

    # E. snapshot 있는데 selected 0
    if len(selected) == 0 and not any("추천 종목 0개" in msg for _, msg in issues):
        issues.append((
            SEVERITY_WARNING,
            "snapshot 저장됨 + selected_recommendations 0개",
        ))

    return target, issues


def format_issues(target: str, issues: list[tuple[str, str]]) -> str:
    if not issues:
        return f"✅ Health check OK ({target})"
    lines = [f"⚠️ Health check warning ({target})"]
    for sev, msg in issues:
        prefix = "🔴" if sev == SEVERITY_CRITICAL else "🟡"
        lines.append(f"  {prefix} {msg}")
    return "\n".join(lines)


def build_kakao_warning(target: str, issues: list[tuple[str, str]]) -> str:
    lines = ["⚠️ 주식 AI 브리핑 검증 경고", f"날짜: {target}", ""]
    for sev, msg in issues:
        prefix = "🔴" if sev == SEVERITY_CRITICAL else "🟡"
        lines.append(f"{prefix} {msg}")
    lines.append("")
    lines.append("GitHub Actions 로그 확인 필요")
    return "\n".join(lines)


def run_post_brief_diagnostic() -> int:
    """09:05 brief 후 자체 검증 + 이상 시 카톡."""
    skip_on_force = "--skip-on-force" in sys.argv
    is_force = os.environ.get("BRIEFING_FORCE_FLAG", "").lower() == "true"

    target, issues = check_health()
    output = format_issues(target, issues)
    print(output)

    if not issues:
        return 0

    if skip_on_force and is_force:
        logger.info("FORCE 실행 + --skip-on-force — 경고 카톡 발송 skip")
        return 0

    try:
        from kakao_sender import send_message
        msg = build_kakao_warning(target, issues)
        ok = send_message(msg)
        logger.info("Health check 경고 카톡 %s", "전송 성공" if ok else "전송 실패")
    except Exception as e:
        logger.warning("Health check 카톡 발송 실패 (무시): %s", e)

    return 0  # 항상 0 — workflow 실패시키지 않음


# ============================================================
#  Entry point
# ============================================================

def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
    )
    if "--post-brief" in sys.argv:
        return run_post_brief_diagnostic()
    return run_system_diagnostic()


if __name__ == "__main__":
    sys.exit(main())
