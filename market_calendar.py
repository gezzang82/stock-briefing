"""
한국 주식시장 영업일 판단
- 주말 (토/일)
- 한국 공휴일 (holidays 패키지)
- KRX 특수 휴장: 12월 31일 (연말 폐장일)
- 임시 휴장: HARD_CODED_HOLIDAYS에 직접 추가

⚠️ Timezone 주의:
  GitHub Actions 러너는 UTC 기본. date.today()를 그대로 쓰면
  KST 06:30(=UTC 21:30 전날)에 실행될 때 어제 날짜를 보게 됨.
  → 반드시 KST로 변환해서 사용 (_today_kst).
"""
from datetime import date, datetime
from zoneinfo import ZoneInfo

import holidays

KST = ZoneInfo("Asia/Seoul")

# KRX 추가 휴장일 (라이브러리에 없는 임시공휴일/선거일 등 발생 시 직접 추가)
# holidays 패키지에 이미 포함된 날짜라도 안전망 차원에서 같이 두어도 무해.
HARD_CODED_HOLIDAYS: set[str] = {
    "2026-06-03",  # 제8회 전국동시지방선거 (holidays 패키지에도 등록 — 이중 안전망)
}

# 한국 공휴일 캐시 (몇 년치 미리 로딩)
_KR_HOLIDAYS = holidays.country_holidays("KR", years=range(2024, 2031))


def _today_kst() -> date:
    """KST 기준 오늘 날짜. GitHub Actions(UTC) 러너에서 정확히 동작하기 위함."""
    return datetime.now(KST).date()


def today_kst() -> date:
    """
    KST 기준 오늘 날짜 (public).

    ⚠️ 한국 시장 데이터를 다루는 모든 모듈은 date.today() 대신 이 함수를 써야 한다.
    GitHub Actions 러너는 UTC 기본이라 새벽~아침에 date.today() 호출 시
    하루 어긋난 날짜를 반환 (예: KST 06:30 = UTC 21:30 전날).

    데이터 범위 조회, 캐시 키, 로그 날짜, AI 프롬프트 내 "오늘" 표기 등
    모든 날짜 컨텍스트에 일관되게 사용.
    """
    return datetime.now(KST).date()


def is_krx_closed(d: date | None = None) -> tuple[bool, str]:
    """KRX 휴장 여부와 사유 반환. (closed, reason)"""
    d = d or _today_kst()

    if d.weekday() >= 5:  # 5=토, 6=일
        return True, "주말"

    if d in _KR_HOLIDAYS:
        return True, f"공휴일 ({_KR_HOLIDAYS.get(d)})"

    if d.isoformat() in HARD_CODED_HOLIDAYS:
        return True, "임시 휴장 (수동 등록)"

    # KRX 연말 폐장일 (12월 31일은 평일이라도 휴장)
    if d.month == 12 and d.day == 31:
        return True, "KRX 연말 폐장"

    return False, ""


if __name__ == "__main__":
    today = _today_kst()
    closed, reason = is_krx_closed()
    print(f"오늘 KST({today}): {'휴장 - ' + reason if closed else '영업일'}")
