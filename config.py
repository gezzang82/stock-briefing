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

# 로컬 스케줄러(main.py --scheduler) 시각 — GitHub Actions/cron-job.org이 메인이라 dead code에 가까움.
# 일관성 위해 cron-job.org 메인 트리거 시각(KST 16:00 EOD)과 동기화 유지.
BRIEFING_HOUR = 16
BRIEFING_MINUTE = 0
TOP_N_STOCKS = 10           # 최대 추천 종목 수 (실제는 score 필터로 가변)
TRACKING_DAYS = 14          # 2주 후 평가
HIT_THRESHOLD_PCT = 5.0     # 단순 적중 기준 (티어 +5%~+10% 경계)

# 추천 자격 임계점수 — technical_screener의 0~100 점수 기준
# 50 = 평균(외국인/기관 약매수 + 거래대금 정상)
# 60 = 양호 (수급 시그널 + 거래대금 1.5x↑ 정도)
# 70 = 강한 시그널 (외국인/기관 모두 + 거래대금 급증)
# 이 값 미만의 종목은 신뢰도 부족으로 추천에서 제외
MIN_SCORE_THRESHOLD = 50.0

# 2주 후 수익률 평가 티어 (% 경계, 내림차순) — 8개 버킷
TIER_BOUNDARIES = [30, 10, 5, 0, -5, -10, -30]
