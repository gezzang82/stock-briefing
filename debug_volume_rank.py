"""
KIS volume-rank API 단독 디버그 — 시간대별 응답 패턴 확인.

목적: 2026-06-02 18:00 EOD에서 거래량 후보 0개 사고 원인 분석.
  - 5/27 EOD엔 15개 정상 추출
  - 6/2 EOD엔 0개
  → 다음 변수 중 무엇이 원인인지 확인:
    a) 시간대 (마감 직후 vs 마감 2.5h 후)
    b) 정렬 기준 (BLNG_CLS_CODE)
    c) 시장 구분 (ALL/KOSPI/KOSDAQ)
    d) ETF 필터 영향

⚠️ 금지 (안전 격리):
  - DB 저장 X
  - 카톡 발송 X
  - 추천 로직 호출 X
  - kis_api 인증부만 import (토큰 발급용)
"""
import sys

import requests

from kis_api import kis
from config import KIS_BASE_URL


URL = f"{KIS_BASE_URL}/uapi/domestic-stock/v1/quotations/volume-rank"
TR_ID = "FHPST01710000"

# 한국 ETF/ETN 필터 (technical_screener와 동일 — 실제 동작 비교용)
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


def call(market: str, blng: str) -> dict:
    """KIS volume-rank 호출 → raw 응답 + 필터링 통계."""
    params = {
        "FID_COND_MRKT_DIV_CODE": "J",
        "FID_COND_SCR_DIV_CODE": "20171",
        "FID_INPUT_ISCD": market,
        "FID_DIV_CLS_CODE": "0",
        "FID_BLNG_CLS_CODE": blng,
        "FID_TRGT_CLS_CODE": "111111111",
        "FID_TRGT_EXLS_CLS_CODE": "0000000000",
        "FID_INPUT_PRICE_1": "",
        "FID_INPUT_PRICE_2": "",
        "FID_VOL_CNT": "",
        "FID_INPUT_DATE_1": "",
    }
    try:
        resp = requests.get(URL, headers=kis._headers(TR_ID),
                            params=params, timeout=10)
        body = resp.json() if resp.headers.get("content-type", "").startswith("application/json") else {"_text": resp.text[:300]}
        return {"http": resp.status_code, "body": body, "params": params}
    except Exception as e:
        return {"error": str(e)[:200], "params": params}


def summarize(result: dict, label: str):
    print("=" * 70)
    print(f"  [{label}]")
    print("=" * 70)
    if "error" in result:
        print(f"  ERROR: {result['error']}")
        return
    body = result["body"]
    print(f"  HTTP {result['http']} | rt_cd={body.get('rt_cd')} | "
          f"msg_cd={body.get('msg_cd')} | msg1={body.get('msg1')!r}")

    output = body.get("output") or []
    print(f"  raw 결과 건수: {len(output)}")

    if not output:
        print(f"  ⚠️  빈 결과 — 응답 응답 key: {sorted(body.keys())}")
        return

    # 필터링 시뮬레이션 (실제 technical_screener와 동일 로직)
    after_filter = 0
    etf_dropped = 0
    pref_dropped = 0
    for r in output:
        name = (r.get("hts_kor_isnm") or "").strip()
        code = (r.get("mksc_shrn_iscd") or "").strip()
        if not (len(code) == 6 and code.isdigit()):
            continue
        if any(name.startswith(p) for p in ETF_BRAND_PREFIXES):
            etf_dropped += 1
            continue
        if any(kw in name for kw in ETF_NAME_KEYWORDS):
            etf_dropped += 1
            continue
        if name.endswith("우") and len(name) >= 3:
            pref_dropped += 1
            continue
        after_filter += 1

    print(f"  ETF 필터 후: {after_filter}개 (ETF dropped {etf_dropped}, 우선주 {pref_dropped})")
    print()
    print("  상위 5개 (raw):")
    for r in output[:5]:
        code = r.get("mksc_shrn_iscd", "??")
        name = r.get("hts_kor_isnm", "??")
        change = r.get("prdy_ctrt", "?")
        vol = r.get("acml_vol", "?")
        vol_inrt = r.get("vol_inrt", "?")
        print(f"    [{code}] {name:20s} change={change}% vol={vol} vol_inrt={vol_inrt}")


def main():
    print()
    try:
        token = kis._get_token()
        print(f"✅ KIS 토큰 OK (prefix={token[:12]}...)")
    except Exception as e:
        print(f"❌ KIS 토큰 실패: {e}")
        sys.exit(1)
    print()

    # 6/2 사고와 동일 호출 — 우리 코드의 기본 (BLNG=1 거래증가율, ALL)
    cases = [
        ("[기본] BLNG=1 (거래증가율) market=ALL — 우리 코드와 동일", "0000", "1"),
        ("BLNG=0 (평균거래량) ALL",     "0000", "0"),
        ("BLNG=2 (평균거래대금) ALL",   "0000", "2"),
        ("BLNG=3 (거래량회전율) ALL",   "0000", "3"),
        ("BLNG=4 (거래대금회전율) ALL", "0000", "4"),
        ("BLNG=1 KOSPI",                "0001", "1"),
        ("BLNG=1 KOSDAQ",               "1001", "1"),
    ]
    for label, market, blng in cases:
        r = call(market, blng)
        summarize(r, label)
        print()


if __name__ == "__main__":
    main()
