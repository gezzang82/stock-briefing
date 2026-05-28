---
name: briefing-system
description: >
  주식 AI 브리핑 시스템(stock_briefing/) 관련 모든 작업의 진입점.
  브리핑 실행·결과 확인, 데이터 파이프라인 오류 디버깅, 추천 적중률·성능 분석,
  기술적 스크리너·AI 프롬프트·시장 레짐·점수 임계값 개선, KIS·카카오·OpenAI·Naver
  API 오류 대응, 새 기능 추가·기존 기능 수정 등 브리핑 시스템에 관한 모든 요청에
  반드시 이 스킬을 먼저 사용할 것.
  트리거 키워드: 브리핑, 추천, 적중률, KIS, 카카오, 스크리닝, 레짐, 기술적 분석,
  뉴스 수집, GPT, 시장 분석, 점수 필터, 검증, 파이프라인, 대시보드, 워크플로우 실패,
  재실행, 다시 분석, 개선, 수정. 이 키워드 중 하나라도 포함되면 반드시 트리거.
metadata:
  type: orchestrator
---

## 시스템 개요

**위치:** `/Users/wonu/stock_briefing/`
**실행:**
- **메인 트리거** — cron-job.org → GitHub `workflow_dispatch` (평일 KST 09:05, 장 개장 후 5분)
- **백업 트리거** — GitHub Actions schedule `5 1 * * 1-5` (평일 KST 10:05, cron-job.org 장애 시 fallback)
- **수동 트리거** — `gh workflow run briefing.yml [-f force=true]` 또는 로컬 `python main.py --run-now`

**KST 09:05 선택 이유:** KIS API의 `inquire-price`는 장 운영시간(09:00~15:30 KST)에만 정상 응답.
그 외 시간엔 우량주에도 매매중단/거래정지 등 비정상 status code 반환됨.

**외부 의존:** KIS API, Naver 뉴스, OpenAI GPT-4o-mini, KakaoTalk, cron-job.org (cron 트리거)

### 파이프라인 흐름

```
main.py
└── briefing.py (오케스트레이터)
    ① market_calendar.py   KRX 휴장일 체크
    ② kis_api.py           KOSPI/KOSDAQ 지수 + 종목 시세
    ③ news_fetcher.py      뉴스 최대 40개 수집
    ④ market_regime.py     시장 상태 (bullish/sideways/bearish)
    ⑤ technical_screener.py  거래량 급증 후보 → 지표 계산 → 상위 20개 (score 0~100)
    ⑥ signal_performance.py  과거 시그널 성과 (AI 참고용)
    ⑦ ai_analyzer.py      GPT-4o-mini → 추천 TOP_N+3개 JSON
    ⑧ _validate_and_clean()  4중 검증 (코드 중복·KIS 존재·ETF 필터·거래상태)
    ⑨ score filter         MIN_SCORE_THRESHOLD=50 미만 제거
    ⑩ database.py          save_recommendations + save_snapshot
    ⑪ accuracy_tracker.py  entry_price + 활성 추천 가격 기록
    ⑫ html_report.py       GitHub Pages 대시보드 갱신 (docs/)
    ⑬ kakao_sender.py      카카오톡 나에게 보내기
```

### 핵심 설정 (config.py)
| 변수 | 값 | 의미 |
|------|-----|------|
| `TOP_N_STOCKS` | 10 | 최대 추천 종목 수 |
| `MIN_SCORE_THRESHOLD` | 50.0 | 기술적 스크리닝 최소 점수 |
| `TRACKING_DAYS` | 14 | 추천 추적 기간 (일) |
| `HIT_THRESHOLD_PCT` | 5.0 | 적중 기준 수익률 (%) |

### DB 테이블
| 테이블 | 주요 컬럼 |
|--------|----------|
| `recommendations` | rec_date, rank, stock_code, stock_name, entry_price, target_return_pct, final_return_pct, mfe_pct, mae_pct |
| `price_tracking` | recommendation_id, stock_code, rec_date, track_date, close_price, return_pct |
| `accuracy_results` | rec_date, total_stocks, hit_count, hit_rate_pct, avg_return_pct |
| `snapshot_logs` | snapshot_date, market_regime, kospi, kosdaq, news_sentiment, snapshot_json (전체 컨텍스트) |
| `signal_performance_cache` | months, result_json |

---

## Phase 0: 컨텍스트 확인

작업 시작 전:
1. `_workspace/` 폴더 존재 여부 확인 → 있으면 이전 작업 이어가기 가능
2. `tail -50 logs/briefing.log` 로 최근 실행 상태 파악
3. 요청 유형 분류 → Phase 1

---

## Phase 1: 작업 분류 및 실행 모드 결정

| 유형 | 판단 기준 | 실행 모드 |
|------|----------|----------|
| **직접 실행** | "실행해줘", "로그 봐줘", "DB 조회", "현황 알려줘" | Claude 직접 처리 |
| **디버깅** | "오류", "실패", "에러", "왜 안 돼", "워크플로우 실패" | 에이전트 팀 (병렬) |
| **성능 분석** | "적중률", "성능 분석", "어떤 시그널", "통계", "결과 어때" | 단일 에이전트 |
| **기능 개발** | "추가해줘", "만들어줘", "바꿔줘", "개선해줘" + 구체적 스펙 | 순차 에이전트 |
| **복합/대규모** | 다수 모듈 동시 변경, 아키텍처 변경 | 전체 팀 |

---

## Phase 2: 직접 처리 — 단순 실행/조회

에이전트 스폰 없이 Claude가 직접 처리한다.

**브리핑 즉시 실행 (로컬):**
```bash
cd /Users/wonu/stock_briefing && source venv/bin/activate 2>/dev/null || true
python main.py --run-now --force
```

**최근 로그 확인:**
```bash
tail -100 /Users/wonu/stock_briefing/logs/briefing.log
```

**최근 추천 현황 (DB 직접 조회):**
```python
import sqlite3
conn = sqlite3.connect('/Users/wonu/stock_briefing/stock_briefing.db')
conn.row_factory = sqlite3.Row
rows = conn.execute('''
    SELECT rec_date, COUNT(*) cnt, GROUP_CONCAT(stock_name) stocks
    FROM recommendations GROUP BY rec_date ORDER BY rec_date DESC LIMIT 7
''').fetchall()
for r in rows: print(dict(r))
```

**적중률 요약:**
```bash
cd /Users/wonu/stock_briefing && python3 -c "
from accuracy_tracker import format_accuracy_summary; print(format_accuracy_summary())"
```

**최근 snapshot 확인:**
```python
import sqlite3, json
conn = sqlite3.connect('/Users/wonu/stock_briefing/stock_briefing.db')
row = conn.execute(
    "SELECT snapshot_date, market_regime, recommendation_count FROM snapshot_logs ORDER BY created_at DESC LIMIT 5"
).fetchall()
for r in row: print(r)
```

---

## Phase 3: 디버깅 — 에이전트 팀

**실행 모드:** 에이전트 팀 (병렬)

```
TeamCreate(members: [pipeline-debugger, feature-developer])
├── TaskCreate: pipeline-debugger → logs/API/DB 조사 → _workspace/debug_findings.md
├── TaskCreate: feature-developer → 코드 흐름 분석 → _workspace/code_analysis.md
├── 두 산출물을 교차 검토하여 근본 원인 확정
└── 수정 방안 + 수정 위치 + 테스트 방법 종합 보고
```

### 공통 장애 패턴 (빠른 진단)

| 로그 키워드 | 원인 | 확인 파일/방법 |
|------------|------|--------------|
| `KIS API 오류` / `401` | 토큰 만료 | `.kis_token_cache` mtime + `kis_api.py:_refresh_token()` |
| `카카오 전송 실패` / `401` | 카카오 토큰 만료 | `.kakao_tokens` + `kakao_auth.py` |
| `추천 종목 0개` / `카톡 skip` | 점수 필터 과도 or regime | `MIN_SCORE_THRESHOLD`, `market_regime.py` 로그 |
| `AI 응답 파싱 실패` | OpenAI 응답 형식 오류 | `ai_analyzer.py` 로그 + 프롬프트 이슈 |
| `⏭️ 중복 실행 skip` | 멱등성 가드 정상 동작 | `has_briefing_run_for_date()` in `database.py` |
| `GitHub Actions 실패` | 시크릿 누락/만료 | `.github/workflows/briefing.yml` + GitHub Secrets |
| `ETF/ETN/SPAC 드롭` 과도 | 스크리너 필터 이슈 | `technical_screener.py:get_volume_ranking()` |

---

## Phase 4: 성능 분석 — 단일 에이전트

**실행 모드:** 서브 에이전트 (strategy-optimizer)

```python
Agent(
    subagent_type="general-purpose",
    prompt="analyze-strategy 스킬을 읽고 [사용자 요청]을 분석하라. "
           "DB 경로: /Users/wonu/stock_briefing/stock_briefing.db. "
           "결과를 _workspace/strategy_analysis.md에 저장하고 핵심 인사이트를 보고하라.",
    model="opus"
)
```

---

## Phase 5: 기능 개발 — 순차 에이전트

**실행 모드:** 순차 서브 에이전트 (설계 → 구현)

**Step 1 — 설계 (strategy-optimizer):**
```python
Agent(
    subagent_type="general-purpose",
    prompt="analyze-strategy 스킬을 읽고 [기능 요구사항]의 설계 문서를 작성하라. "
           "영향받는 모듈, 변경 범위, 구현 방법, 위험 요소를 _workspace/design.md에 저장하라.",
    model="opus"
)
```

**Step 2 — 구현 (feature-developer), design.md 저장 후 실행:**
```python
Agent(
    subagent_type="general-purpose",
    prompt="implement-feature 스킬과 _workspace/design.md를 읽고 "
           "[기능 요구사항]을 구현하라. "
           "변경 내역과 테스트 방법을 _workspace/implementation_notes.md에 저장하라.",
    model="opus"
)
```

---

## 테스트 시나리오

**정상 흐름:** "오늘 브리핑 실행해줘" → 직접 `python main.py --run-now --force` → 로그 확인
**디버그 흐름:** "오늘 브리핑 왜 실패했어?" → 로그 확인 → 팀 스폰 → 근본 원인 + 수정 보고
**분석 흐름:** "최근 한 달 적중률 분석해줘" → strategy-optimizer 스폰 → 통계 보고
**개발 흐름:** "시그널 성과 대시보드에 추가해줘" → optimizer 설계 → developer 구현 → 결과 확인
