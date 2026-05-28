"""
한국투자증권 Open API 클라이언트
https://apiportal.koreainvestment.com/
"""
import json
import logging
import time
from datetime import datetime
from pathlib import Path

import requests

from config import (
    KIS_APP_KEY, KIS_APP_SECRET, KIS_BASE_URL,
    KIS_IS_VIRTUAL, TOKEN_CACHE_PATH,
)

logger = logging.getLogger(__name__)

# KOSPI/KOSDAQ 지수 코드
INDEX_CODES = {
    "KOSPI": "0001",
    "KOSDAQ": "1001",
}

# 종목코드 → 종목명 인메모리 캐시 (런타임 동안 유지)
_NAME_CACHE: dict[str, str] = {}


def cache_stock_name(code: str, name: str):
    """종목명 캐시 (volume-rank, daily-chart 응답에서 발견 시 호출)"""
    if code and name:
        _NAME_CACHE[code] = name.strip()


class KISClient:
    def __init__(self):
        self._token: str | None = None
        self._token_expires: float = 0

    def _load_cached_token(self):
        if TOKEN_CACHE_PATH.exists():
            try:
                data = json.loads(TOKEN_CACHE_PATH.read_text())
                if data.get("expires_at", 0) > time.time() + 300:
                    self._token = data["access_token"]
                    self._token_expires = data["expires_at"]
                    return True
            except Exception:
                pass
        return False

    def _fetch_token(self):
        url = f"{KIS_BASE_URL}/oauth2/tokenP"
        body = {
            "grant_type": "client_credentials",
            "appkey": KIS_APP_KEY,
            "appsecret": KIS_APP_SECRET,
        }
        resp = requests.post(url, json=body, timeout=10)
        resp.raise_for_status()
        data = resp.json()
        self._token = data["access_token"]
        expires_in = int(data.get("expires_in", 86400))
        self._token_expires = time.time() + expires_in
        TOKEN_CACHE_PATH.write_text(json.dumps({
            "access_token": self._token,
            "expires_at": self._token_expires,
        }))
        logger.info("KIS 토큰 발급 완료 (만료: %ds)", expires_in)

    def _get_token(self) -> str:
        if self._token and time.time() < self._token_expires - 300:
            return self._token
        if not self._load_cached_token():
            self._fetch_token()
        return self._token

    def _headers(self, tr_id: str) -> dict:
        return {
            "content-type": "application/json",
            "authorization": f"Bearer {self._get_token()}",
            "appkey": KIS_APP_KEY,
            "appsecret": KIS_APP_SECRET,
            "tr_id": tr_id,
            "custtype": "P",
        }

    def get_stock_price(self, code: str) -> dict | None:
        """현재가 조회"""
        url = f"{KIS_BASE_URL}/uapi/domestic-stock/v1/quotations/inquire-price"
        tr_id = "VTTC8331R" if KIS_IS_VIRTUAL else "FHKST01010100"
        params = {
            "fid_cond_mrkt_div_code": "J",
            "fid_input_iscd": code,
        }
        try:
            resp = requests.get(url, headers=self._headers(tr_id), params=params, timeout=10)
            resp.raise_for_status()
            data = resp.json()
            if data.get("rt_cd") != "0":
                logger.warning("주가 조회 실패 [%s]: %s", code, data.get("msg1"))
                return None
            output = data["output"]
            return {
                "code": code,
                "name": output.get("hts_kor_isnm", ""),
                "current_price": float(output.get("stck_prpr", 0)),
                "change_pct": float(output.get("prdy_ctrt", 0)),
                "volume": int(output.get("acml_vol", 0)),
                "open": float(output.get("stck_oprc", 0)),
                "high": float(output.get("stck_hgpr", 0)),
                "low": float(output.get("stck_lwpr", 0)),
                "per": output.get("per", ""),
                "pbr": output.get("pbr", ""),
                # 거래 가능 여부 판단용 상태 필드
                "iscd_stat_cls_code": output.get("iscd_stat_cls_code", "00"),  # 00=정상
                "temp_stop_yn": output.get("temp_stop_yn", "N"),
                "mang_issu_cls_code": output.get("mang_issu_cls_code", "N"),
                "sltr_yn": output.get("sltr_yn", "N"),
                "mrkt_warn_cls_code": output.get("mrkt_warn_cls_code", "00"),
                "rprs_mrkt_kor_name": output.get("rprs_mrkt_kor_name", ""),
            }
        except Exception as e:
            logger.error("주가 조회 오류 [%s]: %s", code, e)
            return None

    def get_index(self, market: str = "KOSPI") -> dict | None:
        """KOSPI/KOSDAQ 지수 조회"""
        url = f"{KIS_BASE_URL}/uapi/domestic-stock/v1/quotations/inquire-index-price"
        code = INDEX_CODES.get(market.upper(), "0001")
        params = {
            "fid_cond_mrkt_div_code": "U",
            "fid_input_iscd": code,
        }
        try:
            resp = requests.get(
                url, headers=self._headers("FHPUP02100000"), params=params, timeout=10
            )
            resp.raise_for_status()
            data = resp.json()
            if data.get("rt_cd") != "0":
                return None
            output = data["output"]
            return {
                "name": market,
                "current": float(output.get("bstp_nmix_prpr", 0)),
                "change_pct": float(output.get("bstp_nmix_prdy_ctrt", 0)),
            }
        except Exception as e:
            logger.error("%s 지수 조회 오류: %s", market, e)
            return None

    def get_multiple_prices(self, codes: list[str]) -> dict[str, dict]:
        """여러 종목 현재가 일괄 조회"""
        result = {}
        for code in codes:
            price_data = self.get_stock_price(code)
            if price_data:
                result[code] = price_data
            time.sleep(0.1)  # API rate limit
        return result

    def get_stock_name(self, code: str) -> str | None:
        """
        종목명 조회. inquire-price 응답에 hts_kor_isnm이 없으므로
        inquire-daily-itemchartprice의 output1.hts_kor_isnm을 사용.
        결과는 _NAME_CACHE에 저장하여 재호출 방지.
        """
        if code in _NAME_CACHE:
            return _NAME_CACHE[code]

        from datetime import timedelta
        from market_calendar import today_kst
        url = f"{KIS_BASE_URL}/uapi/domestic-stock/v1/quotations/inquire-daily-itemchartprice"
        end = today_kst().strftime("%Y%m%d")
        start = (today_kst() - timedelta(days=7)).strftime("%Y%m%d")
        params = {
            "FID_COND_MRKT_DIV_CODE": "J",
            "FID_INPUT_ISCD": code,
            "FID_INPUT_DATE_1": start,
            "FID_INPUT_DATE_2": end,
            "FID_PERIOD_DIV_CODE": "D",
            "FID_ORG_ADJ_PRC": "0",
        }
        try:
            resp = requests.get(
                url, headers=self._headers("FHKST03010100"), params=params, timeout=10,
            )
            resp.raise_for_status()
            data = resp.json()
            if data.get("rt_cd") != "0":
                logger.warning("종목명 조회 실패 [%s]: %s", code, data.get("msg1"))
                return None
            output1 = data.get("output1") or {}
            name = (output1.get("hts_kor_isnm") or "").strip()
            prdt_type = (output1.get("rprs_mrkt_kor_name") or "").strip()
            if not name:
                return None
            cache_stock_name(code, name)
            # prdt_type 정보도 같이 캐시 (ETF/ETN 필터링용)
            _NAME_CACHE[f"_type_{code}"] = prdt_type
            return name
        except Exception as e:
            logger.debug("종목명 조회 오류 [%s]: %s", code, e)
            return None

    def get_index_daily(self, market: str = "KOSPI", days: int = 60) -> list[dict]:
        """
        지수 일봉 OHLCV (최신이 리스트 마지막).
        endpoint: /uapi/domestic-stock/v1/quotations/inquire-index-daily-price
        tr_id: FHPUP02120000 (지수 일자별 시세)
        """
        from datetime import timedelta
        from market_calendar import today_kst
        url = f"{KIS_BASE_URL}/uapi/domestic-stock/v1/quotations/inquire-index-daily-price"
        code = INDEX_CODES.get(market.upper(), "0001")
        end = today_kst().strftime("%Y%m%d")
        start = (today_kst() - timedelta(days=int(days * 1.6))).strftime("%Y%m%d")
        params = {
            "FID_PERIOD_DIV_CODE": "D",
            "FID_COND_MRKT_DIV_CODE": "U",
            "FID_INPUT_ISCD": code,
            "FID_INPUT_DATE_1": start,
            "FID_INPUT_DATE_2": end,
        }
        try:
            resp = requests.get(url, headers=self._headers("FHPUP02120000"),
                                params=params, timeout=10)
            resp.raise_for_status()
            data = resp.json()
            if data.get("rt_cd") != "0":
                logger.warning("지수 일봉 실패 [%s]: %s", market, data.get("msg1"))
                return []
            # output2: 일봉 배열
            rows = data.get("output2") or data.get("output") or []
            bars = []
            for r in rows:
                if not r.get("bstp_nmix_prpr") and not r.get("bstp_nmix_clpr"):
                    continue
                try:
                    close = float(r.get("bstp_nmix_prpr") or r.get("bstp_nmix_clpr") or 0)
                    if close <= 0:
                        continue
                    bars.append({
                        "date": r.get("stck_bsop_date", ""),
                        "open": float(r.get("bstp_nmix_oprc") or close),
                        "high": float(r.get("bstp_nmix_hgpr") or close),
                        "low": float(r.get("bstp_nmix_lwpr") or close),
                        "close": close,
                        "volume": int(r.get("acml_vol", 0) or 0),
                        "trade_value": int(r.get("acml_tr_pbmn", 0) or 0),
                    })
                except (ValueError, TypeError):
                    continue
            bars.sort(key=lambda x: x["date"])
            return bars[-days:]
        except Exception as e:
            logger.error("지수 일봉 조회 오류 [%s]: %s", market, e)
            return []

    def get_investor_trend(self, code: str, days: int = 10) -> list[dict]:
        """
        일별 외국인/기관/개인 매매동향 (최근 N일).
        반환: [{date, close, foreign_qty, foreign_value, instit_qty, instit_value, ...}]
        value 단위: 백만원
        """
        url = f"{KIS_BASE_URL}/uapi/domestic-stock/v1/quotations/inquire-investor"
        params = {
            "FID_COND_MRKT_DIV_CODE": "J",
            "FID_INPUT_ISCD": code,
        }
        try:
            resp = requests.get(url, headers=self._headers("FHKST01010900"),
                                params=params, timeout=10)
            resp.raise_for_status()
            data = resp.json()
            if data.get("rt_cd") != "0":
                return []
            rows = data.get("output", []) or []
            out = []
            for r in rows[:days]:
                try:
                    out.append({
                        "date": r["stck_bsop_date"],
                        "close": float(r["stck_clpr"]),
                        "foreign_qty": int(r.get("frgn_ntby_qty", 0) or 0),
                        "foreign_value": int(r.get("frgn_ntby_tr_pbmn", 0) or 0),
                        "instit_qty": int(r.get("orgn_ntby_qty", 0) or 0),
                        "instit_value": int(r.get("orgn_ntby_tr_pbmn", 0) or 0),
                        "personal_qty": int(r.get("prsn_ntby_qty", 0) or 0),
                        "personal_value": int(r.get("prsn_ntby_tr_pbmn", 0) or 0),
                    })
                except (ValueError, KeyError, TypeError):
                    continue
            out.sort(key=lambda x: x["date"])  # 오래된 것이 앞
            return out
        except Exception as e:
            logger.debug("투자자 매매동향 오류 [%s]: %s", code, e)
            return []

    def get_program_trading(self, code: str) -> dict | None:
        """
        프로그램 매매 당일 누적 (분별 시계열에서 최신값 = output[0]).
        반환: {net_qty, net_value_won, buy_value_won, sell_value_won}
        """
        url = f"{KIS_BASE_URL}/uapi/domestic-stock/v1/quotations/program-trade-by-stock"
        params = {
            "FID_COND_MRKT_DIV_CODE": "J",
            "FID_INPUT_ISCD": code,
        }
        try:
            resp = requests.get(url, headers=self._headers("FHPPG04650100"),
                                params=params, timeout=10)
            resp.raise_for_status()
            data = resp.json()
            if data.get("rt_cd") != "0":
                return None
            rows = data.get("output") or []
            if not rows:
                return None
            latest = rows[0]
            return {
                "net_qty": int(latest.get("whol_smtn_ntby_qty", 0) or 0),
                "net_value_won": int(latest.get("whol_smtn_ntby_tr_pbmn", 0) or 0),
                "buy_value_won": int(latest.get("whol_smtn_shnu_tr_pbmn", 0) or 0),
                "sell_value_won": int(latest.get("whol_smtn_seln_tr_pbmn", 0) or 0),
            }
        except Exception as e:
            logger.debug("프로그램 매매 오류 [%s]: %s", code, e)
            return None

    def is_etf_or_etn(self, code: str) -> bool:
        """get_stock_name 호출 후에만 의미 있음. 마켓 구분이 ETF/ETN인지 판단."""
        prdt_type = _NAME_CACHE.get(f"_type_{code}", "")
        return any(kw in prdt_type for kw in ["ETF", "ETN", "ELW"])

    def _get_investor_buy_ranking(
        self, investor_type: str, count: int, market: str = "ALL",
    ) -> list[dict]:
        """
        외국인/기관 순매수 상위 종목 조회 (공통 내부 함수).

        Args:
            investor_type: "foreign" (외국인) | "institution" (기관)
            count: 상위 N개
            market: "ALL" | "KOSPI" | "KOSDAQ"

        Returns:
            [{code, name, current_price, change_pct, volume, net_buy_value_won}, ...]
            net_buy_value_won 단위: 원 (해당 investor의 순매수 금액)

        TR_ID:
            FHPST01740000 — 외국인기관 매매 상위 종목
        Endpoint:
            /uapi/domestic-stock/v1/ranking/foreign-institution-total
        FID_RANK_SORT_CLS_CODE:
            "0" = 순매수수량 / "1" = 순매수금액 (우리는 금액 기준)
        FID_TRGT_CLS_CODE 분리:
            외국인은 frgn_*, 기관은 orgn_* prefix 필드 분리
        """
        url = f"{KIS_BASE_URL}/uapi/domestic-stock/v1/ranking/foreign-institution-total"
        tr_id = "FHPTJ04400000"  # 외국인기관 매매 상위 종합
        market_code = {"ALL": "0000", "KOSPI": "0001", "KOSDAQ": "1001"}.get(market, "0000")

        params = {
            "FID_COND_MRKT_DIV_CODE": "J",
            "FID_COND_SCR_DIV_CODE": "16449",
            "FID_INPUT_ISCD": market_code,
            "FID_RANK_SORT_CLS_CODE": "0",       # 0=순매수금액 상위
            "FID_RANK_SORT_CLS_CODE_2": "0",
            "FID_INPUT_DATE_1": "",
        }

        try:
            resp = requests.get(url, headers=self._headers(tr_id),
                                params=params, timeout=10)
            resp.raise_for_status()
            data = resp.json()
            if data.get("rt_cd") != "0":
                logger.warning(
                    "외국인기관 매매상위 API 실패 (%s): %s",
                    investor_type, data.get("msg1"),
                )
                return []

            # 한국 ETF/ETN 필터 (technical_screener와 동일 패턴)
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

            # investor_type별 사용할 net buy 필드 결정
            # KIS는 응답에서 외국인/기관 net buy를 동시에 반환하기도 함.
            net_field = {
                "foreign":     "frgn_ntby_tr_pbmn",   # 외국인 순매수금액
                "institution": "orgn_ntby_tr_pbmn",   # 기관 순매수금액
            }.get(investor_type, "frgn_ntby_tr_pbmn")

            out = []
            for r in data.get("output", []) or []:
                code = (r.get("mksc_shrn_iscd") or "").strip()
                name = (r.get("hts_kor_isnm") or "").strip()
                if not (len(code) == 6 and code.isdigit()):
                    continue
                if any(name.startswith(p) for p in ETF_BRAND_PREFIXES):
                    continue
                if any(kw in name for kw in ETF_NAME_KEYWORDS):
                    continue
                if name.endswith("우") and len(name) >= 3:  # 우선주
                    continue
                try:
                    out.append({
                        "code": code,
                        "name": name,
                        "current_price": float(r.get("stck_prpr", 0) or 0),
                        "change_pct": float(r.get("prdy_ctrt", 0) or 0),
                        "volume": int(r.get("acml_vol", 0) or 0),
                        "net_buy_value_won": int(r.get(net_field, 0) or 0),
                    })
                    cache_stock_name(code, name)
                except (ValueError, TypeError) as e:
                    logger.debug("매매상위 행 파싱 실패: %s", e)
                if len(out) >= count:
                    break
            return out

        except Exception as e:
            # graceful fallback — 호출자가 거래량 후보만으로 진행 가능
            logger.warning(
                "%s 순매수 상위 조회 실패 (graceful fallback): %s",
                investor_type, e,
            )
            return []

    def get_foreign_buy_ranking(
        self, count: int = 30, market: str = "ALL",
    ) -> list[dict]:
        """외국인 순매수금액 상위 종목."""
        return self._get_investor_buy_ranking("foreign", count, market)

    def get_institution_buy_ranking(
        self, count: int = 30, market: str = "ALL",
    ) -> list[dict]:
        """기관 순매수금액 상위 종목."""
        return self._get_investor_buy_ranking("institution", count, market)


kis = KISClient()
