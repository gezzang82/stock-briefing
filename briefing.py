"""
주식 브리핑 메인 오케스트레이터
"""
import logging
import os
import sys
from datetime import date, datetime

from accuracy_tracker import format_accuracy_summary, record_prices_for_active_recs
from ai_analyzer import analyze_and_recommend
from database import (
    get_recent_recommended_stocks, has_briefing_run_for_date,
    init_db, save_recommendations,
)
from kis_api import kis
from market_calendar import KST, today_kst
from news_fetcher import fetch_financial_news

logger = logging.getLogger(__name__)


def _format_index(name: str, data: dict | None) -> str:
    if not data:
        return f"{name}: 조회 불가"
    sign = "▲" if data["change_pct"] >= 0 else "▼"
    return f"{name} {data['current']:,.2f} ({sign}{abs(data['change_pct']):.2f}%)"


# ============== 검증 헬퍼 ==============

# KIS iscd_stat_cls_code 의 진짜 거래정지 코드 (보수적 정의).
#
# ⚠️ 변경 이력 (2026-05-28):
#   기존: 51~59 전체를 거래정지로 처리 → 영업일에도 정상 종목 광범위 차단.
#   조사: raw 응답 로깅으로 확인한 결과 (briefing.py: _log_kis_status_debug)
#     - stat=55: 휴장/시간외 시간대의 모든 정상 종목 (삼성전자, 하이닉스 등 정상 시세)
#     - stat=57: 상한가 도달 종목 (change_pct +29% 근처)
#     - 휴장일(5/24)에도 정상 종목들이 stat=55, 57로 반환됨
#     - 다른 거래정지 필드(temp_stop_yn, sltr_yn 등)는 N으로 정상
#   결론: stat 54~59 는 거래정지가 아니라 시간/가격 상태 코드.
#
# 진짜 거래정지/관리종목 판정은 아래 명시적 필드로 수행:
#   - temp_stop_yn  (임시 거래정지)
#   - sltr_yn       (정리매매)
#   - mang_issu_cls_code (관리종목)
#   - mrkt_warn_cls_code (시장경고)
# stat 코드는 51, 52, 53만 보조 차단 (위 필드와 중복일 수 있지만 안전망).
_BAD_STATUS_CODES = {"51", "52", "53"}

ETF_BRAND_PREFIXES = (
    "KODEX ", "TIGER ", "ARIRANG ", "KBSTAR ", "HANARO ", "KOSEF ",
    "KINDEX ", "KIWOOM ", "ACE ", "SOL ", "RISE ", "WOORI ", "TREX ",
    "FOCUS ", "PLUS ", "FN ", "MASTER ", "SMART ", "TIMEFOLIO ",
    "WON ", "BIG ",
)
ETF_NAME_KEYWORDS = ("ETF", "ETN", "SPAC", "리츠", "액티브",
                     "선물지수", "인버스", "레버리지")


def _is_etf_like(name: str) -> bool:
    return (any(name.startswith(p) for p in ETF_BRAND_PREFIXES)
            or any(kw in name for kw in ETF_NAME_KEYWORDS))


def _is_force_on_closed_day() -> bool:
    """
    --force + 휴장일 동시 만족 여부.
    KIS가 휴장일에 상태 코드를 비정상으로 반환하는 경우가 있어,
    테스트 목적 강제 실행 시에만 trade-status 체크를 우회하기 위함.
    """
    import sys
    if "--force" not in sys.argv:
        return False
    try:
        from market_calendar import is_krx_closed
        closed, _ = is_krx_closed()
        return closed
    except Exception:
        return False


def _log_kis_status_debug(code: str, name: str, pdata: dict,
                          ok: bool, reason: str) -> None:
    """
    KIS get_stock_price() raw 응답의 거래상태 핵심 필드를 WARNING으로 출력.

    목적:
      - 정상 종목이 매매중단/거래정지로 잘못 판정되는 원인 추적
      - _check_tradeable() 결과 + KIS 응답 필드 동시 노출
      - 응답 필드 의미 변경/누락 여부 확인

    Notes:
      - 민감정보(token 등) 일절 출력 안 함 — 시세/상태 필드만 추출
      - 실패해도 호출자에게 예외 전파 안 함 (try/except 감쌀 것)
      - 로그 레벨은 WARNING (운영 환경에서도 노출되도록)
    """
    keys = sorted(pdata.keys()) if isinstance(pdata, dict) else []
    logger.warning(
        "[DEBUG-KIS-STATUS] code=%s name=%s "
        "stat=%s temp=%s mang=%s sltr=%s warn=%s "
        "price=%s change=%s "
        "_check_tradeable: ok=%s reason=%r "
        "keys=%s",
        code, name,
        pdata.get("iscd_stat_cls_code"),
        pdata.get("temp_stop_yn"),
        pdata.get("mang_issu_cls_code"),
        pdata.get("sltr_yn"),
        pdata.get("mrkt_warn_cls_code"),
        pdata.get("current_price"),
        pdata.get("change_pct"),
        ok, reason,
        keys,
    )


def _check_tradeable(pdata: dict) -> tuple[bool, str]:
    """
    KIS inquire-price 응답 필드로 거래 가능 여부 판정.
    Returns (ok, reason). reason은 실패 시에만 채워짐.
    """
    stat = (pdata.get("iscd_stat_cls_code") or "00").strip()
    if stat in _BAD_STATUS_CODES:
        # 51~53만 안전망 차단. 54~59는 시간/가격 상태로 추정되어 제외 (2026-05-28 확인).
        labels = {
            "51": "관리종목",
            "52": "거래정지",
            "53": "정리매매",
        }
        return False, f"{labels.get(stat, '상태이상')} (코드 {stat})"

    if (pdata.get("temp_stop_yn") or "N").strip().upper() == "Y":
        return False, "임시 거래정지"
    if (pdata.get("sltr_yn") or "N").strip().upper() == "Y":
        return False, "정리매매 진행"
    if (pdata.get("mang_issu_cls_code") or "N").strip().upper() == "Y":
        return False, "관리종목"

    warn = (pdata.get("mrkt_warn_cls_code") or "00").strip()
    # 00=정상, 01=주의, 02=경고, 03=위험 — 경고 이상은 거부
    if warn not in ("00", "01"):
        warn_labels = {"02": "시장경고", "03": "위험"}
        return False, f"{warn_labels.get(warn, '시장경고')} (코드 {warn})"

    # 거래량 0 = 사실상 거래 안 됨 (장 시작 전 제외 — 다른 검증으로 충분)
    return True, ""


def _validate_and_clean_recommendations(recs: list[dict]) -> tuple[list[dict], dict[str, dict], list[dict]]:
    """
    4중 검증:
    1) 코드 정규화 + 중복 제거
    2) KIS 종목코드 존재 여부 (get_stock_price)
    3) ETF/ETN/SPAC 여부 (종목명 패턴 + KIS market 구분)
    4) 거래 가능 여부 (관리종목/거래정지/정리매매/시장경고)

    Returns: (cleaned, price_map, rejected)
      - rejected: 검증 탈락한 코드 리스트 (재추천 시 exclude용)
    """
    seen_codes: set[str] = set()
    unique = []
    rejected: list[dict] = []

    # 1) 코드 정규화 + 중복
    for r in sorted(recs, key=lambda x: x.get("rank", 999)):
        code = (r.get("code") or "").strip().zfill(6)
        if not code:
            rejected.append({"code": code, "name": r.get("name", ""), "reason": "빈 코드"})
            continue
        if code in seen_codes:
            logger.warning("중복 코드 제거: %s (%s)", code, r.get("name"))
            rejected.append({"code": code, "name": r.get("name", ""), "reason": "중복"})
            continue
        seen_codes.add(code)
        r["code"] = code
        unique.append(r)

    # 2) KIS 일괄 시세 조회 (존재 여부 + 거래상태 한 번에)
    codes = [r["code"] for r in unique]
    logger.info("종목 검증/시세 조회 (%d개)", len(codes))
    price_map = kis.get_multiple_prices(codes)

    cleaned: list[dict] = []
    for r in unique:
        code = r["code"]
        pdata = price_map.get(code)

        # 2-a) 존재 여부
        if not pdata:
            logger.warning("⚠️ 드롭 [%s] %s — KIS 미인식 코드", code, r.get("name"))
            rejected.append({"code": code, "name": r.get("name", ""), "reason": "KIS 미인식"})
            continue

        # 3) 종목명 + ETF/ETN/SPAC 필터
        kis_name = kis.get_stock_name(code)
        ai_name = (r.get("name") or "").strip()
        if not kis_name:
            logger.warning("⚠️ 드롭 [%s] %s — 종목명 조회 실패", code, ai_name)
            rejected.append({"code": code, "name": ai_name, "reason": "종목명 미확보"})
            continue
        if _is_etf_like(kis_name) or kis.is_etf_or_etn(code):
            logger.warning("⚠️ 드롭 [%s] %s — ETF/ETN/SPAC", code, kis_name)
            rejected.append({"code": code, "name": kis_name, "reason": "ETF/ETN/SPAC"})
            continue

        # 4) 거래 가능 여부 (관리종목/거래정지/정리매매 등)
        ok, reason = _check_tradeable(pdata)
        if not ok:
            # ── DEBUG: KIS 응답 raw 필드 노출 (원인 추적용) ──
            # 정상 종목이 매매중단으로 잘못 판정되는 패턴 분석. 실패해도 무시.
            try:
                _log_kis_status_debug(code, kis_name, pdata, ok, reason)
            except Exception as e:
                logger.debug("DEBUG-KIS-STATUS 로깅 실패 (무시): %s", e)

            if _is_force_on_closed_day():
                # 휴장일 강제 실행: KIS 상태 코드가 비정상으로 반환되므로
                # 거래상태 체크는 warning만 남기고 통과시킴
                logger.warning(
                    "⚠️ FORCE 모드 (휴장일): 거래상태 이슈 무시 [%s] %s — %s",
                    code, kis_name, reason,
                )
            else:
                logger.warning("⚠️ 드롭 [%s] %s — %s", code, kis_name, reason)
                rejected.append({"code": code, "name": kis_name, "reason": reason})
                continue

        # 이름 정정 (hallucination 방지)
        if kis_name != ai_name:
            logger.info("종목명 정정 [%s]: %s → %s", code, ai_name, kis_name)
            r["name"] = kis_name
        cleaned.append(r)

    logger.info(
        "검증 결과: AI %d개 → 통과 %d개, 탈락 %d개",
        len(recs), len(cleaned), len(rejected),
    )
    return cleaned, price_map, rejected


def _save_decision_snapshot(
    today: str,
    kospi_data: dict | None,
    kosdaq_data: dict | None,
    regime_info: dict | None,
    analysis: dict,
    news_stats: dict,
    news_headlines: list[dict],
    tech_candidates: list[dict],
    qualified: list[dict],
    validation_rejected: list[dict],
    score_filtered_out: list[tuple[dict, str]],
):
    """
    추천 시점의 전체 의사결정 컨텍스트를 snapshot_logs에 저장.
    """
    from database import save_snapshot

    # ── dropped 통합 ──
    dropped: list[dict] = []
    for r in validation_rejected:
        dropped.append({
            "code": r.get("code", ""),
            "name": r.get("name", ""),
            "drop_reason": r.get("reason", "validation"),
            "phase": "validation",
        })
    for rec, reason in score_filtered_out:
        dropped.append({
            "code": rec.get("code", ""),
            "name": rec.get("name", ""),
            "drop_reason": reason,
            "phase": "score_filter",
        })

    # ── candidates: 점수와 신호 + 수급/지표 모두 보존 ──
    candidates_dump = []
    for c in tech_candidates:
        ind = c.get("indicators", {})
        sup = c.get("supply", {})
        candidates_dump.append({
            "code": c.get("code"),
            "name": c.get("name"),
            "tech_score": round(c.get("score", 0), 2),
            "signals": c.get("signals", []),
            "current_price": c.get("current_price"),
            "change_pct": c.get("change_pct"),
            "volume": c.get("volume"),
            # supply breakdown
            "foreign_5d_million": sup.get("foreign_5d_million"),
            "instit_5d_million": sup.get("instit_5d_million"),
            "foreign_ratio": sup.get("foreign_ratio"),
            "instit_ratio": sup.get("instit_ratio"),
            "volume_ratio": sup.get("value_ratio"),
            "program_net_won": sup.get("program_net_won"),
            # technical indicators
            "rsi14": ind.get("rsi14"),
            "ma20_dev_pct": ind.get("price_vs_ma20"),
            "momentum_20d": ind.get("momentum_20d"),
            "is_breakout": ind.get("is_breakout"),
            "is_uptrend": ind.get("is_uptrend"),
        })

    # ── selected: AI가 선정 & 검증/필터 통과한 종목 ──
    selected_dump = [
        {
            "rank": r.get("rank"),
            "code": r.get("code"),
            "name": r.get("name"),
            "sector": r.get("sector"),
            "tech_score": r.get("tech_score"),
            "target_return": r.get("target_return"),
            "risk_level": r.get("risk_level"),
            "reason": r.get("reason"),
            "key_catalyst": r.get("key_catalyst"),
        }
        for r in qualified
    ]

    # ── 종합 snapshot dict ──
    snapshot = {
        "date": today,
        "market": {
            "kospi": kospi_data["current"] if kospi_data else None,
            "kospi_change_pct": kospi_data["change_pct"] if kospi_data else None,
            "kosdaq": kosdaq_data["current"] if kosdaq_data else None,
            "kosdaq_change_pct": kosdaq_data["change_pct"] if kosdaq_data else None,
            "market_regime": regime_info["regime"] if regime_info else None,
            "regime_score": regime_info["score"] if regime_info else None,
            "regime_signals": regime_info["signals"] if regime_info else {},
            "market_summary": analysis.get("market_summary", ""),
            "key_themes": analysis.get("key_themes", []),
            "risk_factors": analysis.get("risk_factors", ""),
        },
        "news": {
            "sentiment_score": news_stats.get("avg_sentiment_24h"),
            "sentiment_prev": news_stats.get("avg_sentiment_prev"),
            "sentiment_change": news_stats.get("sentiment_change"),
            "weighted_score_24h": news_stats.get("weighted_score_24h"),
            "weighted_score_prev": news_stats.get("weighted_score_prev"),
            "growth_pct": news_stats.get("growth_pct"),
            "headline_count": news_stats.get("trusted_count_24h"),
            "blocked_count": news_stats.get("blocked_count"),
            "top_headlines": news_headlines,
        },
        "candidates": candidates_dump,
        "selected_recommendations": selected_dump,
        "dropped_recommendations": dropped,
    }

    ok = save_snapshot(
        snapshot_date=today,
        market_regime=(regime_info["regime"] if regime_info else None),
        kospi=(kospi_data["current"] if kospi_data else None),
        kosdaq=(kosdaq_data["current"] if kosdaq_data else None),
        news_sentiment=news_stats.get("avg_sentiment_24h"),
        recommendation_count=len(qualified),
        snapshot_dict=snapshot,
    )
    if ok:
        logger.info(
            "💾 Snapshot saved (candidates=%d, selected=%d, dropped=%d)",
            len(tech_candidates), len(qualified), len(dropped),
        )
    else:
        logger.warning(
            "Snapshot 저장 실패 (candidates=%d, selected=%d)",
            len(tech_candidates), len(qualified),
        )


def _one_recommendation_pass(
    news_text: str, kospi_display: str, kosdaq_display: str,
    recent: list[dict], tech_text: str, regime_text: str,
    excluded_codes: set[str],
    signal_perf_text: str = "",
) -> tuple[dict, list[dict], dict, list[dict]]:
    """
    AI 호출 → 검증 1회. excluded_codes는 회피 힌트로 전달.
    signal_perf_text: 과거 시그널 성과 (참고용, 비면 AI 프롬프트에서 섹션 생략)
    Returns (analysis, cleaned, price_map, rejected)
    """
    extra_exclude = [
        {"stock_code": c, "stock_name": "(직전 시도 탈락)",
         "last_date": today_kst().isoformat(), "times": 1}
        for c in excluded_codes
    ]
    excluded_for_ai = (recent or []) + extra_exclude

    analysis = analyze_and_recommend(
        news_text, kospi_display, kosdaq_display,
        recent_excluded=excluded_for_ai,
        tech_candidates_text=tech_text,
        regime_text=regime_text,
        signal_performance_text=signal_perf_text,
    )
    cleaned, price_map, rejected = _validate_and_clean_recommendations(
        analysis["recommendations"]
    )
    return analysis, cleaned, price_map, rejected


def _log_trigger_source():
    """
    GitHub Actions 환경에서 어떤 이벤트로 호출됐는지 명시 로깅.
    - workflow_dispatch: cron-job.org (메인) 또는 수동 트리거
    - schedule: GitHub Actions 백업 cron (KST 07:30)
    """
    event = os.environ.get("GITHUB_EVENT_NAME")
    if not event:
        return
    label = {
        "workflow_dispatch": "🚀 Trigger source: workflow_dispatch (cron-job.org 또는 수동)",
        "schedule": "🚀 Trigger source: schedule (GitHub Actions 백업 cron)",
    }.get(event, f"🚀 Trigger source: {event}")
    logger.info(label)


def run_daily_briefing():
    """메인 브리핑 실행"""
    _log_trigger_source()
    # ───── 날짜는 KST 기준 (UTC 러너에서도 한국 영업일 기준 동작) ─────
    today = datetime.now(KST).date().isoformat()
    logger.info("🕒 KST 기준 오늘 날짜: %s", today)

    # ───── 멱등성 가드 — 백업 cron 중복 실행 방지 ─────
    # briefing.yml에 백업 cron이 다중 등록되어 있어 같은 날 여러 번 호출될 수 있음.
    # snapshot_logs 기준 판단 (자격 0개여도 snapshot은 항상 저장됨 → 견고).
    # `--force` 플래그가 있으면 무시 (수동 재실행/디버깅용).
    is_force = "--force" in sys.argv
    if is_force:
        logger.warning("⚠️ FORCE 실행 — 멱등성 체크 무시")
    elif has_briefing_run_for_date(today):
        logger.info("⏭️ 오늘 브리핑 이미 실행됨 (snapshot 존재) — 중복 실행 skip")
        return
    else:
        logger.info("오늘 브리핑 미실행 — 정상 진행")

    logger.info("=== %s 주식 브리핑 시작 ===", today)

    # 1. DB 초기화
    init_db()

    # 2. 시장 지수 조회
    kospi_data = kis.get_index("KOSPI")
    kosdaq_data = kis.get_index("KOSDAQ")
    kospi_str = _format_index("KOSPI", kospi_data)
    kosdaq_str = _format_index("KOSDAQ", kosdaq_data)
    kospi_display = f"{kospi_data['current']:,.2f}" if kospi_data else "N/A"
    kosdaq_display = f"{kosdaq_data['current']:,.2f}" if kosdaq_data else "N/A"
    logger.info("시장 지수: %s | %s", kospi_str, kosdaq_str)

    # 3. 뉴스 수집 (dict: text + stats + top_headlines)
    logger.info("뉴스 수집 중...")
    news_data = fetch_financial_news(max_articles=40)
    news_text = news_data["text"]
    news_stats = news_data["stats"]
    news_headlines = news_data["top_headlines"]

    # 4a-pre. 시장 상태 판단 (regime별 가중치 결정)
    regime_text = ""
    weights = None
    try:
        from market_regime import detect_regime, get_weights_for_regime, format_regime_for_prompt
        regime_info = detect_regime()
        weights = get_weights_for_regime(regime_info["regime"])
        regime_text = format_regime_for_prompt(regime_info)
    except Exception as e:
        logger.warning("시장 상태 판단 실패 — 기본 가중치: %s", e)
        regime_info = None

    # 4a. 기술적 스크리닝 (regime 가중치 적용)
    tech_candidates = []
    try:
        from technical_screener import screen_candidates, format_for_prompt
        tech_candidates = screen_candidates(top_n=20, weights=weights)
        tech_text = format_for_prompt(tech_candidates)
        logger.info("기술적 후보 %d개를 AI에 전달", len(tech_candidates))
    except Exception as e:
        logger.warning("기술적 스크리닝 실패 — 뉴스 기반으로 폴백: %s", e)
        tech_text = ""

    # 4a-feedback. 시그널 성과 피드백 로드 (참고용, 빈 결과면 섹션 생략됨)
    # ⚠️ 추천 로직은 그대로 — AI 프롬프트에만 텍스트로 추가됨.
    signal_perf_text = ""
    try:
        from signal_performance import (
            load_cached_performance,
            format_for_prompt as fmt_signal_perf,
        )
        signal_perf_text = fmt_signal_perf(load_cached_performance())
        if signal_perf_text:
            logger.info("시그널 성과 피드백 적용 (AI 참고용)")
    except Exception as e:
        logger.debug("시그널 성과 피드백 로드 실패 (무시): %s", e)
        signal_perf_text = ""

    # 4b. AI 추천 + 검증 (필요 시 재시도)
    recent = get_recent_recommended_stocks(days=7)
    logger.info("최근 7일 추천 이력: %d개 종목 (회피 힌트로 전달)", len(recent))

    MAX_AI_ATTEMPTS = 2
    MIN_VALID_FOR_RETRY = 5

    excluded_codes: set[str] = set()
    cleaned_all: list[dict] = []
    price_map: dict[str, dict] = {}
    analysis = None
    seen_codes: set[str] = set()
    all_rejected: list[dict] = []

    for attempt in range(1, MAX_AI_ATTEMPTS + 1):
        logger.info(
            "── AI 추천 시도 %d/%d (회피 코드 %d개) ──",
            attempt, MAX_AI_ATTEMPTS, len(excluded_codes),
        )
        analysis, pass_cleaned, pass_pmap, pass_rejected = _one_recommendation_pass(
            news_text, kospi_display, kosdaq_display, recent,
            tech_text, regime_text, excluded_codes,
            signal_perf_text=signal_perf_text,
        )
        all_rejected.extend(pass_rejected)

        # 시도된 모든 코드(통과/탈락)를 다음 시도 회피 대상에 추가
        for r in analysis.get("recommendations", []):
            c = (r.get("code") or "").strip().zfill(6)
            if c:
                excluded_codes.add(c)

        # 신규 통과 종목만 누적
        for r in pass_cleaned:
            if r["code"] not in seen_codes:
                cleaned_all.append(r)
                seen_codes.add(r["code"])
        price_map.update(pass_pmap)

        if len(cleaned_all) >= MIN_VALID_FOR_RETRY:
            logger.info("검증 통과 충분 (%d개) — 재시도 불필요", len(cleaned_all))
            break
        if attempt < MAX_AI_ATTEMPTS:
            logger.info(
                "검증 통과 부족 (%d개 < %d) — AI 재추천 시도",
                len(cleaned_all), MIN_VALID_FOR_RETRY,
            )

    cleaned_recs = cleaned_all
    logger.info("최종 검증 통과: %d개 (재시도 %d회 포함)", len(cleaned_recs), attempt)

    if regime_info:
        analysis["regime_info"] = regime_info

    # 6. 점수 필터 — 기술적 스크리닝 score ≥ MIN_SCORE_THRESHOLD인 종목만 유지
    from config import MIN_SCORE_THRESHOLD, TOP_N_STOCKS
    candidate_scores = {c["code"]: c for c in tech_candidates}
    qualified = []
    filtered_out = []
    for rec in cleaned_recs:
        cand = candidate_scores.get(rec["code"])
        if cand is None:
            rec["tech_score"] = None
            filtered_out.append((rec, "기술적 후보 아님 (점수 미산정)"))
            continue
        rec["tech_score"] = cand["score"]
        if cand["score"] < MIN_SCORE_THRESHOLD:
            filtered_out.append((rec, f"점수 {cand['score']:.0f} < {MIN_SCORE_THRESHOLD:.0f}"))
            continue
        qualified.append(rec)

    # 상위 N개 cap + 재정렬
    qualified = qualified[:TOP_N_STOCKS]
    for i, r in enumerate(qualified, 1):
        r["rank"] = i

    logger.info(
        "점수 필터: 검증 %d개 → 자격 %d개 (제외 %d, 기준 ≥ %d)",
        len(cleaned_recs), len(qualified), len(filtered_out), MIN_SCORE_THRESHOLD,
    )
    for rec, reason in filtered_out:
        logger.info("  ↘ 제외 [%s] %s — %s", rec["code"], rec["name"], reason)

    analysis["recommendations"] = qualified

    # 7. 자격 있는 추천 DB 저장 (0개여도 저장 — rec_date 기록용)
    # ⚠️ 안전장치 (2026-05-28 사고 대응):
    #   save_recommendations()는 내부적으로 같은 rec_date의 기존 row를 DELETE 후 INSERT함.
    #   force=true 디버그 실행에서 결과가 0개로 나오면 기존에 저장된 정상 추천이
    #   덮어쓰여 사라지는 사고가 있었음.
    #
    #   - 정상 실행 (force=false): 기존 로직 유지 — 0개여도 DELETE+INSERT 진행
    #     (정상 cron에서 0개면 그 날 정말 추천 없음이라는 의도)
    #   - FORCE + 0개: save_recommendations 스킵하여 기존 row 보존
    #     (디버그 trigger의 오염을 막음)
    #   - snapshot은 force와 무관하게 항상 저장 (디버깅 데이터)
    if is_force and not qualified:
        logger.warning(
            "⚠️ FORCE 실행 결과 추천 0개 — 기존 추천 데이터 보존 (save_recommendations skip)"
        )
    else:
        save_recommendations(today, qualified, analysis.get("market_summary", ""))

    # 7.5. Snapshot — 추천 당시 전체 컨텍스트를 JSON으로 보존
    try:
        _save_decision_snapshot(
            today=today,
            kospi_data=kospi_data, kosdaq_data=kosdaq_data,
            regime_info=regime_info,
            analysis=analysis,
            news_stats=news_stats,
            news_headlines=news_headlines,
            tech_candidates=tech_candidates,
            qualified=qualified,
            validation_rejected=all_rejected,
            score_filtered_out=filtered_out,
        )
    except Exception as e:
        logger.warning("Snapshot 저장 실패 (브리핑은 계속 진행): %s", e)

    # 8. 추천 당일 가격을 entry_price로 업데이트 (자격 있는 종목만)
    from database import update_entry_price
    qualified_codes = {r["code"] for r in qualified}
    for code, pdata in price_map.items():
        if code in qualified_codes:
            update_entry_price(today, code, pdata["current_price"])

    # 8. 활성 추천에 대해 오늘 가격 기록 (적중률 추적용)
    record_prices_for_active_recs()

    # 9. HTML 대시보드 갱신 (GitHub Pages용)
    try:
        from accuracy_tracker import get_all_monthly_stats
        from html_report import generate_html_report
        from weekly_report import fetch_period_items, fetch_sector_performance, HTML_OUTPUT
        generate_html_report(
            get_all_monthly_stats(months_limit=12),
            fetch_period_items(days=7),
            fetch_sector_performance(days=30),
            HTML_OUTPUT,
        )
    except Exception as e:
        logger.warning("HTML 대시보드 생성 실패 (전송은 계속): %s", e)

    # 10. 카카오톡 나에게 보내기
    accuracy_text = format_accuracy_summary()
    try:
        from kakao_sender import send_message as kakao_send
        from html_report import DASHBOARD_URL
        regime_label = ""
        if regime_info:
            from market_regime import REGIME_LABEL
            regime_label = REGIME_LABEL.get(regime_info["regime"], ("", ""))[0]

        if qualified:
            # TOP 3 종목 + 핵심 모멘텀(key_catalyst) 한 줄씩
            # 카카오 메시지 글자수 부풀기 방지: catalyst 60자 초과 시 잘라서 표시
            top_lines = []
            for i, r in enumerate(qualified[:3], 1):
                catalyst = (r.get("key_catalyst") or "").strip()
                if catalyst:
                    if len(catalyst) > 60:
                        catalyst = catalyst[:57] + "…"
                    top_lines.append(f"{i}. {r['name']} — {catalyst}")
                else:
                    top_lines.append(f"{i}. {r['name']}")
            top_block = "\n".join(top_lines)

            kakao_msg = (
                f"📈 주식 AI 브리핑 [{today}]\n"
                f"{kospi_str}\n{kosdaq_str}\n"
                f"{regime_label}\n\n"
                f"📊 추천 {len(qualified)}개\n"
                f"{top_block}\n\n"
                f"📱 대시보드: {DASHBOARD_URL}"
            )
            ok = kakao_send(kakao_msg)
            logger.info("카카오톡 전송 %s", "성공" if ok else "실패/건너뜀")
        else:
            # 자격 통과 추천 0개 — 카톡 발송 자체를 skip (P1).
            # 추천이 없는 날에 알림 보내봐야 노이즈만 됨.
            # 시스템 헬스체크는 워크플로우 success/failure 알림으로 충분.
            logger.info("📭 자격 통과 추천 0개 — 카톡 발송 skip")
    except Exception as e:
        logger.warning("카카오톡 전송 중 예외: %s", e)

    logger.info("=== 브리핑 완료 ===")
    return analysis
