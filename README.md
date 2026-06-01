# 주식 AI 백테스트 브리핑

한국 주식시장(KOSPI/KOSDAQ)을 매일 아침 자동으로 분석하고 카카오톡으로 추천 종목을 발송하는 시스템.

🌐 **대시보드**: https://gezzang82.github.io/stock-briefing/

---

## 주요 기능

- **기술적 스크리닝**: KIS 거래량 순위 → 외국인/기관 수급 + 거래대금 가중 점수
- **AI 분석**: GPT-4o-mini가 후보 + 뉴스 + 시장 regime 종합해 TOP N 선정
- **4중 검증**: 코드 정규화 → KIS 존재 → ETF/ETN 필터 → 거래 가능 여부
- **카카오톡 알림**: TOP 3 + 핵심 모멘텀 한 줄씩
- **HTML 대시보드**: 월별 티어 분포 + 종목별 MFE/MAE/수익률 추적
- **시그널 피드백 루프**: 과거 추천 outcome → AI 프롬프트에 참고 데이터로 삽입

---

## 운영 구조

### 메인 실행 (EOD — 장 마감 후 추천)

- **트리거**: [cron-job.org](https://cron-job.org/) → GitHub `workflow_dispatch` 호출
- **시각**: 매 평일 (월~금) **16:00 KST** (장 마감 + 30분, 시간외 종가 완료 후)
- **이유 1**: 하루 거래량/외국인기관 매매가 모두 확정 → 점수 시그널 최고 품질
- **이유 2**: 스윙 트레이딩 (14일 보유) 전략에 본질적으로 적합 — 갭 위험 < 데이터 품질
- **이유 3**: 다음 영업일 09:00 시가까지 17시간 → 사용자 분석/매수 준비 여유

### 백업 실행

- **트리거**: GitHub Actions `schedule` (cron `30 7 * * 1-5` = UTC 07:30)
- **시각**: 매 평일 **16:30 KST** (메인의 30분 후)
- **의미**: cron-job.org 자체 장애 시 fallback

### 중복 실행 방지

- **방식**: `snapshot_logs` 테이블 기준 멱등성 가드
- **함수**: `database.has_briefing_run_for_date(today)`
- **동작**:
  - `True` → 오늘 이미 실행됨 → 즉시 정상 종료 (카톡/DB 변경 없음)
  - `False` → 정상 진행
  - `--force` 플래그 시 가드 무시
- **이유**: `snapshot_logs`는 자격 추천 0개여도 항상 row 저장됨 → 견고

---

## 설정 가이드

cron-job.org 메인 트리거 설정 → [docs/cron-job-org-setup.md](docs/cron-job-org-setup.md)

---

## 핵심 파일

| 파일 | 역할 |
|---|---|
| `main.py` | 진입점 (`--run-now`, `--force`) |
| `briefing.py` | 오케스트레이션 (검증 + 점수 필터 + 카톡) |
| `ai_analyzer.py` | OpenAI 호출 + 응답 파싱 |
| `technical_screener.py` | KIS 거래량 순위 + 지표 + 점수화 |
| `market_regime.py` | KOSPI 상태 판단 (bullish/sideways/bearish) |
| `kis_api.py` | 한국투자증권 API 래퍼 |
| `kakao_sender.py` | 카카오톡 "나에게 보내기" + 토큰 자동 갱신 |
| `database.py` | SQLite ORM (recommendations / price_tracking / snapshot_logs) |
| `accuracy_tracker.py` | 가격 추적 + MFE/MAE/Final 계산 |
| `html_report.py` | 대시보드 HTML 생성 (`docs/index.html`) |
| `signal_performance.py` | 과거 추천 outcome → 시그널 버킷 통계 (피드백 루프) |

---

## 워크플로우

- `.github/workflows/briefing.yml` — 매일 평일 브리핑 (cron-job.org 메인 + GitHub 백업)
- `.github/workflows/weekly_report.yml` — 매주 일요일 09:00 KST 주간 리포트 + 시그널 피드백 캐시 갱신

---

## 데이터 보존

- `stock_briefing.db` — git에 직접 커밋 (누적 데이터 보존, 캐시 만료 위험 제거)
- `docs/` — git에 직접 커밋 (GitHub Pages가 `main:/docs` Branch 모드로 자동 서빙)
- 두 폴더 모두 워크플로우 종료 시 자동 commit & push

---

## 시그널 피드백 루프 (참고용)

- `signal_performance.py`가 매주 일요일 실행 → `snapshot_logs ⋈ recommendations` JOIN
- 외국인 비중 / 거래대금 비율 / RSI / regime 4축 버킷별 평균 수익률 산출
- 결과는 `signal_performance_cache` 테이블에 JSON으로 저장
- 매일 브리핑 시 캐시 로드 → AI 프롬프트에 ★/⚠ 마커로 노출 (참고용 — 자동 가중치 변경 X)

---

## 라이선스

개인 프로젝트.
