# 주식 AI 브리핑 시스템

## 하네스: 주식 브리핑

**목표:** 매 평일 KST 18:00 EOD(시간외 단일가 종료 후) 자동 실행되는 AI 주식 브리핑 파이프라인을 유지·개선·디버깅한다.

**트리거:** 브리핑 시스템 관련 모든 작업 요청 시 `briefing-system` 스킬을 사용하라.
"브리핑", "추천", "적중률", "KIS", "카카오", "스크리닝", "레짐", "뉴스 수집", "대시보드",
"워크플로우 실패", "재실행", "다시 분석", "개선", "수정" 등 키워드가 포함된 요청이면
반드시 스킬 트리거. 단순 질문은 직접 응답 가능.

**변경 이력:**
| 날짜 | 변경 내용 | 대상 | 사유 |
|------|----------|------|------|
| 2026-05-27 | 초기 구성 | 전체 | 신규 하네스 구축 |
| 2026-05-27 | 시그널 피드백 루프 추가 (참고용) | signal_performance.py, signal_performance_cache 테이블 | snapshot ⋈ recommendations 시그널 버킷 통계 → AI 프롬프트 참고 데이터 |
| 2026-05-27 | KST timezone fix | market_calendar.py, briefing.py, accuracy_tracker.py | GitHub Actions UTC 러너에서 KST 06:30(UTC 21:30 전날) 잘못된 날짜 인식 사고 |
| 2026-05-27 | 백업 cron + 멱등성 가드 (snapshot_logs 기준) | briefing.yml, database.py:has_briefing_run_for_date | GitHub Actions schedule 14h 지연 + 빈 추천 시 가드 우회 사고 |
| 2026-05-27 | 카톡 포맷 변경 (TOP 3 + key_catalyst) | briefing.py:_send_kakao | "왜 이 종목인지" 설명 복원 요청 |
| 2026-05-28 | Pages를 main:/docs Branch 모드로 전환 | .gitignore, briefing.yml, weekly_report.yml | GitHub codeload 장애 회피 + actions/configure-pages 의존성 제거 |
| 2026-05-28 | 외부 cron(cron-job.org) 메인 트리거 + GitHub schedule 1개 백업 | briefing.yml, docs/cron-job-org-setup.md | GitHub Actions schedule 지연/누락 영구 회피 |
| 2026-05-28 | 트리거 시각 06:30 → 09:05 KST (장 개장 후 5분) | cron-job.org, briefing.yml | KIS API의 inquire-price는 장 운영시간(09:00~15:30)에 정상 응답 |
| 2026-05-28 | KIS `_BAD_STATUS_CODES` {51-59} → {51,52,53} | briefing.py:_check_tradeable | stat 54~59가 거래정지가 아니라 시간/가격 상태 코드임을 raw 로깅으로 확인 |
| 2026-05-28 | KIS raw 응답 디버그 로깅 추가 | briefing.py:_log_kis_status_debug | 거래상태 검증 false 시 stat/temp/sltr/warn 필드 + _check_tradeable 결과 WARNING 출력 |
| 2026-06-01 | 트리거 시각 09:05 → 16:00 KST (EOD) | cron-job.org, briefing.yml, config.py, briefing.py 카톡 메시지 | 09:05는 장 개장 직후라 KIS 거래량/외국인기관 데이터 누적 전 → 후보 2개 + 0개 시그널. EOD는 하루치 데이터 확정으로 점수 시그널 최고 품질. 스윙 트레이딩 전략에 본질적으로 적합. |
| 2026-06-01 | 트리거 시각 16:00 → 18:00 KST (EOD 정착) | cron-job.org, briefing.yml, config.py | 시간외 단일가(16:00~18:00) 종료 후 → 데이터 최완전 + 사용자 퇴근 직후 카톡 확인 가능. |
| 2026-06-04 | GitHub Actions `schedule` 백업 제거 (cron-job.org 단독) | briefing.yml, README.md, docs/cron-job-org-setup.md | 5/5건 schedule 발화가 11~14h 지연 → 다음날 새벽 backup이 cron-job.org 18:00 메인보다 먼저 snapshot 선점 → 메인 무력화 사고 (6/2~6/4 3건 검증). cron-job.org 4일 100% 정시 발화 검증 후 단독 운영 결정. workflow_dispatch는 수동 fallback용으로 유지. 추천/점수/BLNG/threshold/REGIME_WEIGHTS/DB/카카오/health_check 로직 무변경. |
| 2026-06-08 | weekly_report `schedule` 제거 + 시각 일요일 19:00 KST로 재설정 | weekly_report.yml, README.md, docs/cron-job-org-setup.md | briefing.yml과 동일 schedule 지연 문제 — 의도 일요일 09:00 KST가 실제 19:13 KST 발화 (10h 지연). 사용자가 실제 도착 시각(19:00 KST)을 정시 의도로 채택. cron-job.org에 weekly 잡 추가하면 정시 발화 보장. 추천/점수 로직 무변경. |
