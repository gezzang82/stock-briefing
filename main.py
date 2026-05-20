"""
주식 자동 브리핑 시스템 - 진입점
매일 06:30 실행
"""
import logging
import sys
from pathlib import Path

# 로그 설정
LOG_DIR = Path(__file__).parent / "logs"
LOG_DIR.mkdir(exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler(LOG_DIR / "briefing.log", encoding="utf-8"),
    ],
)
logger = logging.getLogger(__name__)


def run_now():
    from briefing import run_daily_briefing
    from accuracy_tracker import update_accuracy
    try:
        run_daily_briefing()
        update_accuracy()
    except Exception as e:
        logger.exception("브리핑 실행 오류: %s", e)
        sys.exit(1)


def start_scheduler():
    import schedule
    import time

    from config import BRIEFING_HOUR, BRIEFING_MINUTE

    schedule_time = f"{BRIEFING_HOUR:02d}:{BRIEFING_MINUTE:02d}"
    schedule.every().day.at(schedule_time).do(run_now)
    logger.info("스케줄러 시작 - 매일 %s 실행", schedule_time)

    while True:
        schedule.run_pending()
        time.sleep(30)


if __name__ == "__main__":
    if "--run-now" in sys.argv:
        logger.info("즉시 실행 모드")
        run_now()
    elif "--scheduler" in sys.argv:
        start_scheduler()
    else:
        # launchd에서 호출 시 기본 동작: 즉시 실행
        run_now()
