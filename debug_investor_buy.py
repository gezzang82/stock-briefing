"""
KIS 외국인/기관 매매 상위 API (FHPTJ04400000) 단독 디버그 스크립트.

목적:
  briefing 전체를 실행하지 않고 외국인/기관 매수 상위 API의 raw 응답만 확인.
  파라미터 조합에 따른 응답 차이를 비교해 정확한 호출 방법 파악.

⚠️ 금지:
  - DB 저장 안 함 (sqlite 일절 import 안 함)
  - snapshot 저장 안 함
  - 카톡 발송 안 함 (kakao_sender import 안 함)
  - briefing/screener/ai_analyzer 호출 안 함

사용:
  python debug_investor_buy.py
  python debug_investor_buy.py --market KOSPI
  python debug_investor_buy.py --sort 1  # 다른 정렬 기준 테스트
"""
import argparse
import json
import sys
from collections import Counter

import requests

# 토큰 로딩만 위해 kis_api의 인증부 활용 (DB/카톡 등 다른 모듈은 import 안 함)
from kis_api import kis
from config import KIS_BASE_URL


# 확정된 endpoint — 2차 디버그에서 검증됨
# /quotations/foreign-institution-total + FHPTJ04400000 → HTTP 200 응답
ENDPOINTS = [
    ("/uapi/domestic-stock/v1/quotations/foreign-institution-total", "FHPTJ04400000"),
]


def call(url_path: str, tr_id: str, params: dict) -> dict:
    """KIS API 호출 → raw 응답 dict 반환 (실패 시 {error: ...})."""
    url = f"{KIS_BASE_URL}{url_path}"
    headers = kis._headers(tr_id)
    try:
        resp = requests.get(url, headers=headers, params=params, timeout=10)
        try:
            body = resp.json()
        except Exception:
            body = {"_raw_text": resp.text[:500]}
        return {
            "http_status": resp.status_code,
            "url": resp.url,
            "body": body,
        }
    except Exception as e:
        return {"error": str(e), "url": url, "params": params}


def summarize(result: dict, label: str):
    """디버그 출력 — 핵심 필드만 추출."""
    print("=" * 70)
    print(f"  [{label}]")
    print("=" * 70)
    print(f"HTTP status: {result.get('http_status')}")
    print(f"Request URL: {result.get('url', '')[:120]}")

    body = result.get("body") or {}
    rt_cd = body.get("rt_cd")
    msg1 = body.get("msg1")
    msg_cd = body.get("msg_cd")
    print(f"rt_cd: {rt_cd!r} | msg_cd: {msg_cd!r} | msg1: {msg1!r}")

    output = body.get("output") or body.get("output1") or []
    if isinstance(output, dict):
        print(f"output is dict (keys): {sorted(output.keys())}")
        # 다른 output 키 찾아보기
        for k, v in body.items():
            if k.startswith("output") and isinstance(v, list):
                output = v
                print(f"  → 리스트 발견: '{k}' (len={len(v)})")
                break

    if not isinstance(output, list):
        print(f"output 타입 비정상: {type(output).__name__}")
        print(f"전체 응답 키: {sorted(body.keys())}")
        return

    print(f"결과 건수: {len(output)}")

    if not output:
        print("(빈 결과 — TR_ID/파라미터/시간대 점검 필요)")
        return

    # 주요 필드명 목록 — 첫 row의 키
    print()
    print("주요 필드명:")
    keys = sorted(output[0].keys())
    for k in keys:
        sample = output[0].get(k, "")
        sample_str = str(sample)[:30]
        print(f"  {k:30s} = {sample_str!r}")

    # 상위 5개 종목 (이름/현재가/등락률/외국인 순매수금액)
    print()
    print("상위 5개 종목 요약:")
    for i, r in enumerate(output[:5], 1):
        code = r.get("mksc_shrn_iscd") or r.get("stck_shrn_iscd") or "??"
        name = r.get("hts_kor_isnm") or "??"
        price = r.get("stck_prpr") or "??"
        change = r.get("prdy_ctrt") or "??"
        f_net = r.get("frgn_ntby_tr_pbmn") or r.get("frgn_ntby_qty") or "??"
        i_net = r.get("orgn_ntby_tr_pbmn") or r.get("orgn_ntby_qty") or "??"
        print(f"  {i}. [{code}] {name}")
        print(f"     현재가={price} 등락={change}% 외국인순매수={f_net} 기관순매수={i_net}")


def main():
    parser = argparse.ArgumentParser(description="KIS 외국인기관 매매상위 디버그")
    parser.add_argument("--market", default="ALL",
                        choices=["ALL", "KOSPI", "KOSDAQ"], help="시장 구분")
    parser.add_argument("--sort", default="0",
                        help="FID_RANK_SORT_CLS_CODE (정렬 기준 코드)")
    parser.add_argument("--sort2", default="0",
                        help="FID_RANK_SORT_CLS_CODE_2 (보조 정렬)")
    args = parser.parse_args()

    market_code = {"ALL": "0000", "KOSPI": "0001", "KOSDAQ": "1001"}[args.market]

    print()
    print(f"### Target market: {args.market} ({market_code})")
    print(f"### Sort code: {args.sort} / Sort2: {args.sort2}")
    print()

    # KIS 토큰 발급 확인
    try:
        token = kis._get_token()
        print(f"✅ KIS 토큰 발급 OK (prefix: {token[:15]}...)")
    except Exception as e:
        print(f"❌ KIS 토큰 발급 실패: {e}")
        sys.exit(1)
    print()

    # 메인 endpoint 호출
    # KIS가 한 번에 한 필드 누락만 알려줌 → 가능한 모든 일반 FID 필드를 한 번에 채움.
    # 값 의미가 모호한 필드는 빈 문자열 또는 일반적인 기본값으로.
    for url_path, tr_id in ENDPOINTS:
        for div_cls in ["0", "1", "2"]:
            params = {
                "FID_COND_MRKT_DIV_CODE": "J",
                "FID_COND_SCR_DIV_CODE": "16449",
                "FID_INPUT_ISCD": market_code,
                "FID_DIV_CLS_CODE": div_cls,
                "FID_RANK_SORT_CLS_CODE": args.sort,
                "FID_RANK_SORT_CLS_CODE_2": args.sort2,
                "FID_INPUT_DATE_1": "",
                # 일반 KIS 추가 필드들 (KIS API가 종종 요구)
                "FID_ETC_CLS_CODE": "",
                "FID_TRGT_CLS_CODE": "111111111",       # 일반/우선/투자/외국인 등 전부
                "FID_TRGT_EXLS_CLS_CODE": "0000000000",  # 제외 없음
                "FID_INPUT_PRICE_1": "",
                "FID_INPUT_PRICE_2": "",
                "FID_VOL_CNT": "",
                "FID_BLNG_CLS_CODE": "",
            }
            result = call(url_path, tr_id, params)
            summarize(result, f"{tr_id} @ {url_path}  (DIV_CLS={div_cls})")
            print()


if __name__ == "__main__":
    main()
