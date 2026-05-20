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


kis = KISClient()
