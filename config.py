import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv(Path(__file__).parent / ".env")

# 한국투자증권 API
KIS_APP_KEY = os.getenv("KIS_APP_KEY", "")
KIS_APP_SECRET = os.getenv("KIS_APP_SECRET", "")
KIS_ACCOUNT_NO = os.getenv("KIS_ACCOUNT_NO", "")
KIS_IS_VIRTUAL = os.getenv("KIS_IS_VIRTUAL", "false").lower() == "true"

KIS_BASE_URL = (
    "https://openapivts.koreainvestment.com:29443"
    if KIS_IS_VIRTUAL
    else "https://openapi.koreainvestment.com:9443"
)

# 카카오 API
KAKAO_REST_API_KEY = os.getenv("KAKAO_REST_API_KEY", "")
KAKAO_CLIENT_SECRET = os.getenv("KAKAO_CLIENT_SECRET", "")
KAKAO_ACCESS_TOKEN = os.getenv("KAKAO_ACCESS_TOKEN", "")
KAKAO_REFRESH_TOKEN = os.getenv("KAKAO_REFRESH_TOKEN", "")

# 네이버 API
NAVER_CLIENT_ID = os.getenv("NAVER_CLIENT_ID", "")
NAVER_CLIENT_SECRET = os.getenv("NAVER_CLIENT_SECRET", "")

# OpenAI
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")

# 시스템 설정
BASE_DIR = Path(__file__).parent
DB_PATH = BASE_DIR / "stock_briefing.db"
TOKEN_CACHE_PATH = BASE_DIR / ".kis_token_cache"
KAKAO_TOKEN_PATH = BASE_DIR / ".kakao_tokens"
LOG_DIR = BASE_DIR / "logs"

BRIEFING_HOUR = 6
BRIEFING_MINUTE = 30
TOP_N_STOCKS = 10
TRACKING_DAYS = 14          # 2주 후 평가
HIT_THRESHOLD_PCT = 5.0     # 단순 적중 기준 (티어 +5%~+10% 경계)

# 2주 후 수익률 평가 티어 (% 경계, 내림차순) — 8개 버킷
TIER_BOUNDARIES = [30, 10, 5, 0, -5, -10, -30]
