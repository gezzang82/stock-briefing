"""
시장 상태(regime) 판단 모듈.

지표:
  - KOSPI 종가가 MA20 위/아래 (얼마나 떨어져 있는지)
  - 일일 수익률의 20일 변동성 (표준편차)
  - 오늘 거래대금이 5일 평균 대비 어떤지
  - 거래량 상위 종목 중 상승 비율 (A/D ratio 근사)
  - 5일 추세 (모멘텀)

각 지표를 -2~+2 점수로 환산, 합산해서:
  score ≥ +3 → bullish
  score ≤ -3 → bearish
  그 외      → sideways

regime에 따라 technical_screener 가중치를 조정:
  - Bullish:  거래대금 ↑ (추세 가속 따라가기)
  - Sideways: 기본 균형
  - Bearish:  외국인 ↑ (스마트 머니만 신뢰), 거래대금 ↓
"""
import logging

from kis_api import kis

logger = logging.getLogger(__name__)


# regime별 가중치 (외국인 / 기관 / 거래대금) — 합은 100
REGIME_WEIGHTS = {
    "bullish":  {"foreign": 35, "instit": 25, "value": 40},
    "sideways": {"foreign": 40, "instit": 30, "value": 30},
    "bearish":  {"foreign": 50, "instit": 30, "value": 20},
}

REGIME_LABEL = {
    "bullish":  ("🟢 Bullish",  "강세장 — 추세 추종"),
    "sideways": ("🟡 Sideways", "혼조세 — 균형 가중"),
    "bearish":  ("🔴 Bearish",  "약세장 — 외국인 중심"),
}


def get_weights_for_regime(regime: str) -> dict:
    return REGIME_WEIGHTS.get(regime, REGIME_WEIGHTS["sideways"])


def _stdev(values: list[float]) -> float:
    if len(values) < 2:
        return 0.0
    avg = sum(values) / len(values)
    return (sum((v - avg) ** 2 for v in values) / len(values)) ** 0.5


def detect_regime() -> dict:
    """
    KOSPI 일봉 + 거래량 순위 데이터로 시장 상태 판단.
    Returns:
      {
        regime: 'bullish'|'sideways'|'bearish',
        score: int (-7 ~ +7 근처),
        signals: dict (지표별 설명),
        fallback: bool,
      }
    """
    bars = kis.get_index_daily("KOSPI", days=30)
    if not bars or len(bars) < 20:
        logger.warning("KOSPI 일봉 데이터 부족 — sideways 폴백")
        return {
            "regime": "sideways", "score": 0,
            "signals": {"error": "지수 데이터 조회 실패"},
            "fallback": True,
        }

    closes = [b["close"] for b in bars]
    volumes = [b["volume"] for b in bars]
    cur = closes[-1]
    score = 0
    signals: dict[str, str] = {}

    # ────── 1. MA20 위치 ──────
    ma20 = sum(closes[-20:]) / 20
    ma_dev_pct = (cur - ma20) / ma20 * 100
    if ma_dev_pct > 2.0:
        score += 2
        signals["ma20"] = f"KOSPI MA20 위 +{ma_dev_pct:.2f}% (강세)"
    elif ma_dev_pct > 0:
        score += 1
        signals["ma20"] = f"KOSPI MA20 위 +{ma_dev_pct:.2f}%"
    elif ma_dev_pct > -2.0:
        signals["ma20"] = f"KOSPI MA20 {ma_dev_pct:+.2f}% (보합)"
    else:
        score -= 2
        signals["ma20"] = f"KOSPI MA20 아래 {ma_dev_pct:.2f}% (약세)"

    # ────── 2. 20일 변동성 (일일 수익률 stdev) ──────
    daily_returns = [
        (closes[i] / closes[i - 1] - 1) * 100
        for i in range(1, len(closes))
    ]
    vol = _stdev(daily_returns[-20:])
    if vol > 1.5:
        score -= 1
        signals["volatility"] = f"변동성 {vol:.2f}% (높음 — 혼란)"
    elif vol < 0.7:
        signals["volatility"] = f"변동성 {vol:.2f}% (낮음)"
    else:
        signals["volatility"] = f"변동성 {vol:.2f}%"

    # ────── 3. 거래대금 (오늘 vs 5일 평균) ──────
    if len(volumes) >= 6 and all(volumes[-6:]):
        today_vol = volumes[-1]
        prev_5d_avg = sum(volumes[-6:-1]) / 5
        vol_ratio = today_vol / prev_5d_avg if prev_5d_avg else 1.0
        is_up_today = closes[-1] > closes[-2]
        if vol_ratio > 1.3 and is_up_today:
            score += 1
            signals["volume"] = f"거래대금 {vol_ratio:.2f}x (상승 + 거래 증가, 확신)"
        elif vol_ratio > 1.3 and not is_up_today:
            score -= 1
            signals["volume"] = f"거래대금 {vol_ratio:.2f}x (하락 + 거래 증가, 패닉)"
        elif vol_ratio < 0.7:
            signals["volume"] = f"거래대금 {vol_ratio:.2f}x (저조)"
        else:
            signals["volume"] = f"거래대금 {vol_ratio:.2f}x"

    # ────── 4. 상승 종목 비율 (거래량 순위 Top N 기반 근사) ──────
    try:
        from technical_screener import get_volume_ranking
        leaders = get_volume_ranking(count=50)
        if leaders:
            up_count = sum(1 for s in leaders if s.get("change_pct", 0) > 0)
            total = len(leaders)
            ad_pct = up_count / total * 100
            if ad_pct >= 60:
                score += 2
                signals["advance_decline"] = f"상승 종목 {ad_pct:.0f}% ({up_count}/{total}) — 강세"
            elif ad_pct >= 45:
                signals["advance_decline"] = f"상승 종목 {ad_pct:.0f}% ({up_count}/{total}) — 혼조"
            else:
                score -= 2
                signals["advance_decline"] = f"상승 종목 {ad_pct:.0f}% ({up_count}/{total}) — 약세"
    except Exception as e:
        logger.debug("A/D ratio 계산 건너뜀: %s", e)

    # ────── 5. 5일 모멘텀 ──────
    if len(closes) >= 6:
        change_5d = (closes[-1] / closes[-6] - 1) * 100
        if change_5d > 2.0:
            score += 1
            signals["trend_5d"] = f"5일 추세 {change_5d:+.2f}% (상승)"
        elif change_5d < -2.0:
            score -= 1
            signals["trend_5d"] = f"5일 추세 {change_5d:+.2f}% (하락)"
        else:
            signals["trend_5d"] = f"5일 추세 {change_5d:+.2f}%"

    # ────── 분류 ──────
    if score >= 3:
        regime = "bullish"
    elif score <= -3:
        regime = "bearish"
    else:
        regime = "sideways"

    logger.info(
        "시장 상태: %s (score %+d) | %s",
        REGIME_LABEL[regime][0], score,
        " · ".join(signals.values()),
    )

    return {
        "regime": regime,
        "score": score,
        "signals": signals,
        "fallback": False,
    }


def format_regime_for_prompt(info: dict) -> str:
    """AI 프롬프트에 삽입할 시장 상태 텍스트"""
    label, desc = REGIME_LABEL[info["regime"]]
    weights = get_weights_for_regime(info["regime"])
    lines = [
        f"시장 상태: {label} (점수 {info['score']:+d}) — {desc}",
        f"  적용 가중치: 외국인 {weights['foreign']}% / 기관 {weights['instit']}% / 거래대금 {weights['value']}%",
    ]
    for key, sig in info["signals"].items():
        lines.append(f"  · {sig}")
    return "\n".join(lines)


def format_regime_for_discord(info: dict) -> str:
    """Discord 임베드용 짧은 요약"""
    label, desc = REGIME_LABEL[info["regime"]]
    return f"{label} ({desc}, 점수 {info['score']:+d})"


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    info = detect_regime()
    print()
    print(format_regime_for_prompt(info))
