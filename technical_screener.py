"""
기술적 스크리닝 모듈
- KIS 거래량 순위 API로 후보 추출
- 각 후보의 일봉 OHLCV 가져와서 지표 계산
- 점수화 후 상위 N개 + 시그널 반환

AI 분석에 후보 목록 + 지표를 전달하기 위한 전처리 단계.
"""
import logging
import time
from datetime import date, timedelta

import requests

from config import KIS_BASE_URL
from kis_api import cache_stock_name, kis

logger = logging.getLogger(__name__)


# ============== KIS API 래퍼 ==============

def get_volume_ranking(count: int = 50, market: str = "ALL") -> list[dict]:
    """KIS 거래량 순위 (평균 거래량 대비 급증)"""
    url = f"{KIS_BASE_URL}/uapi/domestic-stock/v1/quotations/volume-rank"
    market_code = {"ALL": "0000", "KOSPI": "0001", "KOSDAQ": "1001"}.get(market, "0000")
    params = {
        "FID_COND_MRKT_DIV_CODE": "J",
        "FID_COND_SCR_DIV_CODE": "20171",
        "FID_INPUT_ISCD": market_code,
        "FID_DIV_CLS_CODE": "0",
        "FID_BLNG_CLS_CODE": "1",  # 1=거래증가율 (vol surge)
        "FID_TRGT_CLS_CODE": "111111111",
        "FID_TRGT_EXLS_CLS_CODE": "0000000000",
        "FID_INPUT_PRICE_1": "",
        "FID_INPUT_PRICE_2": "",
        "FID_VOL_CNT": "",
        "FID_INPUT_DATE_1": "",
    }
    try:
        resp = requests.get(url, headers=kis._headers("FHPST01710000"),
                            params=params, timeout=10)
        resp.raise_for_status()
        data = resp.json()
        if data.get("rt_cd") != "0":
            logger.warning("거래량 순위 실패: %s", data.get("msg1"))
            return []
        # 한국 ETF/ETN/펀드 브랜드 prefix (이름이 이걸로 시작하면 ETF류)
        ETF_BRAND_PREFIXES = (
            "KODEX ", "TIGER ", "ARIRANG ", "KBSTAR ", "HANARO ", "KOSEF ",
            "KINDEX ", "KIWOOM ", "ACE ", "SOL ", "RISE ", "WOORI ", "TREX ",
            "FOCUS ", "PLUS ", "FN ", "MASTER ", "SMART ", "TIMEFOLIO ",
            "WON ", "BIG ", "마이다스 ",
        )
        ETF_NAME_KEYWORDS = (
            "ETF", "ETN", "SPAC", "리츠", "스팩", "우B",
            "액티브", "선물지수", "인버스", "레버리지",
        )

        out = []
        for r in data.get("output", []):
            code = r.get("mksc_shrn_iscd", "").strip()
            name = r.get("hts_kor_isnm", "").strip()
            # 일반 주식만 (ETF/ETN/SPAC/REIT/우선주 등 제외)
            if not (len(code) == 6 and code.isdigit()):
                continue
            if any(name.startswith(p) for p in ETF_BRAND_PREFIXES):
                continue
            if any(kw in name for kw in ETF_NAME_KEYWORDS):
                continue
            if name.endswith("우") and len(name) >= 3:  # 우선주 (~우, ~2우B 등)
                continue
            try:
                out.append({
                    "code": code,
                    "name": name,
                    "current_price": float(r.get("stck_prpr", 0) or 0),
                    "change_pct": float(r.get("prdy_ctrt", 0) or 0),
                    "volume": int(r.get("acml_vol", 0) or 0),
                    "volume_ratio_kis": float(r.get("vol_inrt", 0) or 0),
                })
                cache_stock_name(code, name)  # 후속 검증에서 재호출 방지
            except (ValueError, TypeError) as e:
                logger.debug("거래량 순위 행 파싱 실패: %s", e)
            if len(out) >= count:
                break
        return out
    except Exception as e:
        logger.error("거래량 순위 조회 오류: %s", e)
        return []


def get_daily_ohlcv(code: str, days: int = 60) -> list[dict]:
    """일봉 OHLCV (수정주가). 최신이 리스트 마지막."""
    url = f"{KIS_BASE_URL}/uapi/domestic-stock/v1/quotations/inquire-daily-itemchartprice"
    end = date.today().strftime("%Y%m%d")
    # 공휴일/주말 고려해서 days × 1.6 만큼 거슬러 조회
    start = (date.today() - timedelta(days=int(days * 1.6))).strftime("%Y%m%d")
    params = {
        "FID_COND_MRKT_DIV_CODE": "J",
        "FID_INPUT_ISCD": code,
        "FID_INPUT_DATE_1": start,
        "FID_INPUT_DATE_2": end,
        "FID_PERIOD_DIV_CODE": "D",
        "FID_ORG_ADJ_PRC": "0",  # 0=수정주가
    }
    try:
        resp = requests.get(url, headers=kis._headers("FHKST03010100"),
                            params=params, timeout=10)
        resp.raise_for_status()
        data = resp.json()
        if data.get("rt_cd") != "0":
            return []
        rows = data.get("output2", []) or []
        bars = []
        for r in rows:
            if not r.get("stck_clpr"):
                continue
            try:
                bars.append({
                    "date": r["stck_bsop_date"],
                    "open": float(r["stck_oprc"]),
                    "high": float(r["stck_hgpr"]),
                    "low": float(r["stck_lwpr"]),
                    "close": float(r["stck_clpr"]),
                    "volume": int(r["acml_vol"]),
                })
            except (ValueError, KeyError):
                continue
        bars.sort(key=lambda x: x["date"])
        return bars[-days:]
    except Exception as e:
        logger.debug("일봉 조회 오류 [%s]: %s", code, e)
        return []


# ============== 지표 계산 ==============

def _rsi(closes: list[float], period: int = 14) -> float:
    if len(closes) < period + 1:
        return 50.0
    gains, losses = [], []
    for i in range(1, len(closes)):
        d = closes[i] - closes[i - 1]
        gains.append(max(d, 0))
        losses.append(max(-d, 0))
    avg_g = sum(gains[-period:]) / period
    avg_l = sum(losses[-period:]) / period
    if avg_l == 0:
        return 100.0
    rs = avg_g / avg_l
    return 100 - (100 / (1 + rs))


def compute_indicators(ohlcv: list[dict]) -> dict | None:
    if len(ohlcv) < 20:
        return None

    closes = [b["close"] for b in ohlcv]
    volumes = [b["volume"] for b in ohlcv]
    highs = [b["high"] for b in ohlcv]
    cur = closes[-1]

    ma5 = sum(closes[-5:]) / 5
    ma20 = sum(closes[-20:]) / 20
    ma60 = sum(closes[-60:]) / min(60, len(closes))

    vol_5d_avg = sum(volumes[-5:]) / 5
    vol_ratio = volumes[-1] / vol_5d_avg if vol_5d_avg > 0 else 0

    momentum_5d = (cur / closes[-6] - 1) * 100 if len(closes) >= 6 else 0
    momentum_20d = (cur / closes[-21] - 1) * 100 if len(closes) >= 21 else 0

    rsi14 = _rsi(closes, 14)

    high_period = max(highs)
    dist_from_high = (cur / high_period - 1) * 100

    prior_highs = highs[-21:-1] if len(highs) >= 21 else highs[:-1]
    is_breakout = bool(prior_highs) and cur > max(prior_highs)

    return {
        "current": cur,
        "ma5": ma5, "ma20": ma20, "ma60": ma60,
        "price_vs_ma20": (cur / ma20 - 1) * 100 if ma20 else 0,
        "volume_ratio": vol_ratio,
        "momentum_5d": momentum_5d,
        "momentum_20d": momentum_20d,
        "rsi14": rsi14,
        "dist_from_high_pct": dist_from_high,
        "is_breakout": is_breakout,
        "is_uptrend": ma5 > ma20 > ma60,
    }


def compute_supply_demand(code: str, ohlcv: list[dict]) -> dict:
    """
    수급/거래대금 지표 집계.

    Returns:
      - foreign_5d_million / instit_5d_million: 외국인/기관 5일 누적 순매수 (백만원)
      - foreign_ratio / instit_ratio: 5일 거래대금 대비 비중 (decimal, +/-)
      - value_ratio: 오늘 거래대금 / 직전 5일 평균
      - program_net_won: 프로그램 매매 당일 누적 순매수 (원)
    """
    # 5일 외국인/기관 누적
    investor = kis.get_investor_trend(code, days=10)
    time.sleep(0.1)
    last5 = investor[-5:] if len(investor) >= 5 else investor
    foreign_5d = sum(d["foreign_value"] for d in last5)  # 백만원
    instit_5d = sum(d["instit_value"] for d in last5)

    # 프로그램 매매 당일 누적
    prog = kis.get_program_trading(code)
    time.sleep(0.1)
    program_net = prog["net_value_won"] if prog else 0

    # 거래대금 (close × volume) 기반 비중 + 증가율
    # 단위 일치: ohlcv 거래대금 = 원, investor value = 백만원 → ×1e6
    value_ratio = 1.0
    foreign_ratio = 0.0
    instit_ratio = 0.0
    if len(ohlcv) >= 6:
        daily_values = [b["close"] * b["volume"] for b in ohlcv[-6:]]  # 원
        today_value = daily_values[-1]
        prev_5d_avg = sum(daily_values[:-1]) / 5
        value_ratio = today_value / prev_5d_avg if prev_5d_avg > 0 else 1.0

        total_5d_value = sum(daily_values[-6:-1])  # 직전 5일 거래대금 합 (원)
        if total_5d_value > 0:
            foreign_ratio = (foreign_5d * 1_000_000) / total_5d_value
            instit_ratio = (instit_5d * 1_000_000) / total_5d_value

    return {
        "foreign_5d_million": foreign_5d,
        "instit_5d_million": instit_5d,
        "foreign_ratio": foreign_ratio,
        "instit_ratio": instit_ratio,
        "value_ratio": value_ratio,
        "program_net_won": program_net,
    }


def score_candidate(ind: dict, supply: dict) -> tuple[float, list[str]]:
    """
    수급/거래대금 기반 점수 (총점 100점):
      - 외국인 수급 40%
      - 기관 수급 30%
      - 거래대금 증가율 30%

    기존 기술 시그널(MA/RSI/돌파)은 표시 라벨로만 포함, 점수에 미반영.
    """
    score = 0.0
    signals: list[str] = []

    # ========== 외국인 (max 40) ==========
    fr_pct = supply["foreign_ratio"] * 100  # 거래대금 대비 비중 %
    if fr_pct >= 5.0:
        score += 40; signals.append(f"외국인 강매수 {fr_pct:+.1f}%")
    elif fr_pct >= 2.0:
        score += 30; signals.append(f"외국인 순매수 {fr_pct:+.1f}%")
    elif fr_pct >= 0.5:
        score += 20
    elif fr_pct > 0:
        score += 10
    elif fr_pct <= -2.0:
        signals.append(f"외국인 순매도 {fr_pct:+.1f}%")
    # 0% ~ -2%는 0점

    # ========== 기관 (max 30) ==========
    in_pct = supply["instit_ratio"] * 100
    if in_pct >= 5.0:
        score += 30; signals.append(f"기관 강매수 {in_pct:+.1f}%")
    elif in_pct >= 2.0:
        score += 22; signals.append(f"기관 순매수 {in_pct:+.1f}%")
    elif in_pct >= 0.5:
        score += 15
    elif in_pct > 0:
        score += 7
    elif in_pct <= -2.0:
        signals.append(f"기관 순매도 {in_pct:+.1f}%")

    # ========== 거래대금 증가율 (max 30) ==========
    vr = supply["value_ratio"]
    if vr >= 3.0:
        score += 30; signals.append(f"거래대금 폭증 {vr:.1f}x")
    elif vr >= 2.0:
        score += 25; signals.append(f"거래대금 급증 {vr:.1f}x")
    elif vr >= 1.5:
        score += 20; signals.append(f"거래대금 +{(vr-1)*100:.0f}%")
    elif vr >= 1.2:
        score += 15
    elif vr >= 1.0:
        score += 10

    # ========== 프로그램 매매 (참고 라벨, 점수 미반영) ==========
    prog_billion = supply["program_net_won"] / 1e8
    if abs(prog_billion) >= 10:
        sign = "+" if prog_billion > 0 else ""
        signals.append(f"프로그램 {sign}{prog_billion:.0f}억")

    # ========== 기술 시그널 (참고 라벨, 점수 미반영) ==========
    if ind["is_breakout"]:
        signals.append("20일 신고가 돌파")
    if ind["is_uptrend"]:
        signals.append("정배열(5>20>60)")
    rsi = ind["rsi14"]
    if rsi < 30:
        signals.append(f"과매도 RSI {rsi:.0f}")
    elif rsi > 80:
        signals.append(f"과열 RSI {rsi:.0f}")
    m20 = ind["momentum_20d"]
    if m20 > 10:
        signals.append(f"20일 +{m20:.1f}%")

    return score, signals


# ============== 메인 ==============

def screen_candidates(top_n: int = 20) -> list[dict]:
    """기술적 스크리닝 전체 파이프라인"""
    logger.info("기술적 스크리닝 시작...")

    volume_top = get_volume_ranking(count=30)
    if not volume_top:
        logger.warning("거래량 순위 조회 결과 없음 — 스크리닝 건너뜀")
        return []
    logger.info("거래량 순위 1차 후보: %d개", len(volume_top))

    candidates = []
    for v in volume_top:
        ohlcv = get_daily_ohlcv(v["code"], days=60)
        time.sleep(0.1)
        if not ohlcv:
            continue
        ind = compute_indicators(ohlcv)
        if not ind:
            continue
        supply = compute_supply_demand(v["code"], ohlcv)  # KIS 호출 2건 + sleep 내부
        score, signals = score_candidate(ind, supply)
        candidates.append({
            **v, "indicators": ind, "supply": supply,
            "score": score, "signals": signals,
        })

    candidates.sort(key=lambda x: x["score"], reverse=True)
    top = candidates[:top_n]

    if top:
        logger.info(
            "스크리닝 완료: %d개 → 상위 %d개 (점수 %.0f~%.0f)",
            len(candidates), len(top), top[0]["score"], top[-1]["score"],
        )
    else:
        logger.warning("스크리닝 결과 없음")
    return top


def format_for_prompt(candidates: list[dict]) -> str:
    """AI 프롬프트에 넣을 문자열 포맷 (점수 = 외국인40+기관30+거래대금30)"""
    if not candidates:
        return "(기술적 스크리닝 데이터 없음)"
    lines = []
    for c in candidates:
        ind = c["indicators"]
        sup = c.get("supply", {})
        sigs = ", ".join(c["signals"]) if c["signals"] else "특이 시그널 없음"
        f_pct = sup.get("foreign_ratio", 0) * 100
        i_pct = sup.get("instit_ratio", 0) * 100
        vr = sup.get("value_ratio", 1.0)
        prog_b = sup.get("program_net_won", 0) / 1e8
        lines.append(
            f"  - [{c['code']}] {c['name']} (점수 {c['score']:.0f}): "
            f"현재가 {c['current_price']:,.0f}원 ({c['change_pct']:+.2f}%)\n"
            f"    수급: 외국인 {f_pct:+.2f}%, 기관 {i_pct:+.2f}%, "
            f"거래대금 {vr:.2f}x, 프로그램 {prog_b:+.0f}억\n"
            f"    기술: RSI {ind['rsi14']:.0f}, MA20대비 {ind['price_vs_ma20']:+.1f}%, "
            f"20일모멘텀 {ind['momentum_20d']:+.1f}%\n"
            f"    시그널: {sigs}"
        )
    return "\n".join(lines)


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    result = screen_candidates(top_n=20)
    print(format_for_prompt(result))
