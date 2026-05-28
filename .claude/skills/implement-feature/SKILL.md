---
name: implement-feature
description: >
  주식 브리핑 시스템 기능 구현 가이드. feature-developer 에이전트가 사용.
  새 기능 추가, 버그 수정, 모듈 확장, DB 스키마 변경, GitHub Actions 수정 방법을 안내한다.
---

## 코드 컨벤션

- **타입 힌트**: 함수 인자와 반환값에 항상 명시
- **로깅**: `logger = logging.getLogger(__name__)`, 레벨은 info/warning/exception
- **외부 API 호출**: 반드시 `timeout=` 파라미터 + try/except + warning 로깅
- **폴백**: 부분 실패는 빈 결과 반환 + warning, 전체 실패는 exception re-raise
- **날짜**: KST 기준 — `from market_calendar import KST; datetime.now(KST).date()`
- **DB 접근**: 항상 `database.py:get_conn()` 컨텍스트 매니저 사용

---

## 모듈 확장 패턴

### 새 뉴스 소스 추가
`news_fetcher.py` 참조:
1. `_fetch_[source]()` 함수 추가 (동일 dict 구조 반환)
2. `fetch_financial_news()` 내 소스 목록에 추가
3. `stats` 집계에 포함

### 새 기술 지표 추가
`technical_screener.py` 참조:
1. `_calculate_indicators()` 함수 내 지표 계산 추가
2. `_score_candidate()` 함수에 점수 기여분 추가
3. `format_for_prompt()` 출력에 포함
4. `_save_decision_snapshot()` 의 `candidates_dump`에 컬럼 추가

### DB 컬럼 추가
`database.py:SCHEMA` 수정:
```python
# SCHEMA 문자열에 ALTER TABLE 추가 (idempotent)
ALTER TABLE recommendations ADD COLUMN new_col TEXT;
```
주의: SQLite는 `ADD COLUMN`만 지원, 타입 변경 불가

### 새 DB 테이블 추가
`database.py:SCHEMA`의 `CREATE TABLE IF NOT EXISTS` 블록에 추가

---

## 파이프라인 통합 체크리스트

새 모듈을 추가하거나 기존 모듈을 수정할 때 영향 범위 확인:

| 변경 위치 | 연동 확인 필요 |
|----------|--------------|
| `technical_screener.py` | `briefing.py:tech_candidates`, `_save_decision_snapshot():candidates_dump` |
| `market_regime.py` | `briefing.py:weights`, `ai_analyzer.py:regime_text` |
| `ai_analyzer.py` | `ANALYSIS_PROMPT`, `_parse_ai_response()`, 반환 dict 구조 |
| `database.py` | `init_db()`, 관련 함수들, `briefing.py` 호출부 |
| `accuracy_tracker.py` | `briefing.py` Step 8, `html_report.py` 데이터 소스 |
| `html_report.py` | `briefing.py` Step 9, `docs/index.html` 출력 |

---

## 테스트 방법

**단위 테스트 (모듈별):**
```bash
cd /Users/wonu/stock_briefing && source venv/bin/activate 2>/dev/null || true
python3 -c "from [module] import [function]; print([function]([args]))"
```

**파이프라인 통합 테스트 (휴장일 포함):**
```bash
python main.py --run-now --force
tail -50 logs/briefing.log
```

**DB 변경 검증:**
```python
import sqlite3
conn = sqlite3.connect('stock_briefing.db')
# 스키마 확인
print(conn.execute("PRAGMA table_info(recommendations)").fetchall())
```

**GitHub Actions 로컬 시뮬레이션 (act 미설치 시):**
```bash
KIS_APP_KEY=... python main.py --run-now
```

---

## 변경 시 주의사항

1. `briefing.py`의 멱등성 가드는 `snapshot_logs` 기준 — 스키마 변경 시 확인
2. `accuracy_tracker.py`의 `update_accuracy()` 는 `main.py`에서 브리핑 후 자동 호출됨
3. `html_report.py`는 `docs/index.html`을 덮어씀 — 기존 구조 유지
4. `kakao_sender.py`의 메시지는 카카오 API 글자수 제한(~1000자) 확인
5. `config.py`의 `MIN_SCORE_THRESHOLD` 변경은 추천 수에 직접 영향

---

## 출력 형식

`_workspace/implementation_notes.md`에 저장:

```markdown
## 구현 요약
[변경 내용 한 줄 설명]

## 변경 파일
- `파일명:라인번호` — 변경 내용

## 영향 범위
[연동된 모듈 및 영향]

## 테스트 방법
[검증 명령어]

## 검증 필요 항목
[확인이 필요한 부분]
```
