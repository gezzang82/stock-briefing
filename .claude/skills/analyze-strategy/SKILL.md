---
name: analyze-strategy
description: >
  주식 브리핑 시스템의 추천 성능 분석 및 전략 개선 가이드. strategy-optimizer 에이전트가 사용.
  적중률 통계, 시그널 성과, 시장 레짐별 효과, AI 프롬프트 품질, 기술적 스크리닝 파라미터를
  데이터 기반으로 분석하고 구체적 개선안을 도출한다.
---

## 핵심 분석 도구

### DB 성능 쿼리 모음

**기본 적중률 통계:**
```python
import sqlite3, json
conn = sqlite3.connect('/Users/wonu/stock_briefing/stock_briefing.db')
conn.row_factory = sqlite3.Row

# 월별 적중률 추이
rows = conn.execute("""
    SELECT substr(rec_date,1,7) as month,
           COUNT(*) as days,
           AVG(hit_rate_pct) as avg_hit_rate,
           AVG(avg_return_pct) as avg_return,
           SUM(total_stocks) as total_recs
    FROM accuracy_results
    GROUP BY month ORDER BY month DESC LIMIT 6
""").fetchall()
for r in rows: print(dict(r))
```

**종목별 성과 (최근 N일):**
```python
rows = conn.execute("""
    SELECT r.stock_name, r.sector,
           COUNT(*) as rec_count,
           AVG(r.final_return_pct) as avg_final,
           AVG(r.mfe_pct) as avg_mfe,
           AVG(r.mae_pct) as avg_mae
    FROM recommendations r
    WHERE r.rec_date >= date('now', '-90 days')
      AND r.final_return_pct IS NOT NULL
    GROUP BY r.stock_code
    ORDER BY avg_final DESC LIMIT 20
""").fetchall()
```

**레짐별 성과:**
```python
rows = conn.execute("""
    SELECT s.market_regime,
           COUNT(*) as days,
           AVG(a.hit_rate_pct) as avg_hit_rate,
           AVG(a.avg_return_pct) as avg_return
    FROM snapshot_logs s
    JOIN accuracy_results a ON s.snapshot_date = a.rec_date
    WHERE s.market_regime IS NOT NULL
    GROUP BY s.market_regime
""").fetchall()
```

**섹터별 성과:**
```python
rows = conn.execute("""
    SELECT sector,
           COUNT(*) as cnt,
           AVG(final_return_pct) as avg_return,
           SUM(CASE WHEN final_return_pct >= 5.0 THEN 1 ELSE 0 END) * 100.0 / COUNT(*) as hit_rate
    FROM recommendations
    WHERE final_return_pct IS NOT NULL AND sector IS NOT NULL
    GROUP BY sector
    ORDER BY avg_return DESC
""").fetchall()
```

**점수 구간별 성과 (MIN_SCORE_THRESHOLD 최적화용):**
```python
rows = conn.execute("""
    SELECT CASE
           WHEN r.tech_score IS NULL THEN 'N/A'
           WHEN r.tech_score < 50 THEN '~50'
           WHEN r.tech_score < 60 THEN '50~60'
           WHEN r.tech_score < 70 THEN '60~70'
           WHEN r.tech_score < 80 THEN '70~80'
           ELSE '80~'
           END as score_bucket,
           COUNT(*) cnt,
           AVG(final_return_pct) avg_return,
           AVG(mfe_pct) avg_mfe
    FROM recommendations r
    WHERE final_return_pct IS NOT NULL
    GROUP BY score_bucket ORDER BY score_bucket
""").fetchall()
```

---

## snapshot_logs 분석 (추천 당시 전체 컨텍스트)

```python
# 특정 날짜의 추천 컨텍스트 전체 조회
row = conn.execute(
    "SELECT snapshot_json FROM snapshot_logs WHERE snapshot_date = ?", ('2026-05-27',)
).fetchone()
if row:
    data = json.loads(row['snapshot_json'])
    print("시장:", data['market'])
    print("뉴스 감성:", data['news']['sentiment_score'])
    print("후보 수:", len(data['candidates']))
    print("선정:", [c['name'] for c in data['selected_recommendations']])
    print("탈락:", [(d['name'], d['drop_reason']) for d in data['dropped_recommendations']])
```

---

## 시그널 성과 분석

```python
from signal_performance import load_cached_performance, format_for_prompt
perf = load_cached_performance()
print(format_for_prompt(perf))
```

또는 직접 조회:
```python
row = conn.execute(
    "SELECT result_json FROM signal_performance_cache ORDER BY created_at DESC LIMIT 1"
).fetchone()
if row: print(json.dumps(json.loads(row['result_json']), ensure_ascii=False, indent=2))
```

---

## 분석 관점 체크리스트

분석 요청에 따라 아래 관점 중 적합한 것을 선택한다:

**적중률 분석:**
- 전체 기간 vs 최근 30/60/90일 추이
- 레짐별 성과 차이 (bullish에서 잘 되는지?)
- 섹터별 편향 (특정 섹터에서만 성과?)
- 기술 점수 구간별 성과 (`tech_score` ↔ `final_return_pct`)

**AI 프롬프트 품질:**
- 드롭 비율이 높으면 AI가 잘못된 코드 생성 → 프롬프트 규칙 강화
- 재추천 비율이 높으면 다양성 부족 → 프롬프트의 "최근 추천 회피" 강화
- `ai_analyzer.py:ANALYSIS_PROMPT`에서 개선 포인트 찾기

**기술적 스크리닝 파라미터:**
- `technical_screener.py`의 시그널 가중치 (거래량, 외국인, 기관, 모멘텀)
- `market_regime.py:REGIME_WEIGHTS` — 레짐별 가중치
- `config.py:MIN_SCORE_THRESHOLD` — 현재 50, 올리면 품질↑/수량↓

**추천 다양성:**
- 같은 종목이 반복 추천되는 빈도
- 섹터 분산 현황

---

## 개선안 도출 원칙

1. 데이터 없이 개선 제안하지 않는다 — 항상 쿼리 결과를 먼저 제시한다.
2. 개선안은 파일명:변수명 형태로 구체화한다. 예: `config.py:MIN_SCORE_THRESHOLD = 60`
3. 성능 회귀 가능성을 함께 명시한다.
4. 샘플이 30일 미만이면 "통계적 신뢰도 낮음" 경고를 표시한다.

---

## 출력 형식

`_workspace/strategy_analysis.md`에 저장:

```markdown
## 분석 요약
[핵심 발견 3줄 이내]

## 데이터
[쿼리 결과 테이블]

## 인사이트
[데이터 기반 해석]

## 개선 제안
| 우선순위 | 파일 | 변경 내용 | 예상 효과 | 위험도 |
|---------|------|----------|----------|--------|

## 검증 방법
[개선 후 성과를 어떻게 측정할지]
```
