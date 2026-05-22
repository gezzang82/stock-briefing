"""
한국 주식시장 영업일 판단
- 주말 (토/일)
- 한국 공휴일 (holidays 패키지)
- KRX 특수 휴장: 12월 31일 (연말 폐장일)
- 임시 휴장: HARD_CODED_HOLIDAYS에 직접 추가
"""
from datetime import date

import holidays

# KRX 추가 휴장일 (라이브러리에 없는 임시공휴일/선거일 등 발생 시 직접 추가)
HARD_CODED_HOLIDAYS: set[str] = {
    # 예: "2026-06-03",  # 선거일
}

# 한국 공휴일 캐시 (몇 년치 미리 로딩)
_KR_HOLIDAYS = holidays.country_holidays("KR", years=range(2024, 2031))


def is_krx_closed(d: date | None = None) -> tuple[bool, str]:
    """KRX 휴장 여부와 사유 반환. (closed, reason)"""
    d = d or date.today()

    if d.weekday() >= 5:  # 5=토, 6=일
        return True, "주말"

    if d in _KR_HOLIDAYS:
        return True, f"공휴일 ({_KR_HOLIDAYS.get(d)})"

    if d.isoformat() in HARD_CODED_HOLIDAYS:
        return True, "임시 휴장"

    # KRX 연말 폐장일 (12월 31일은 평일이라도 휴장)
    if d.month == 12 and d.day == 31:
        return True, "KRX 연말 폐장"

    return False, ""


if __name__ == "__main__":
    closed, reason = is_krx_closed()
    print(f"오늘({date.today()}): {'휴장 - ' + reason if closed else '영업일'}")
