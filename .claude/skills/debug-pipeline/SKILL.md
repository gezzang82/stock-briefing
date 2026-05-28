---
name: debug-pipeline
description: >
  주식 브리핑 파이프라인 오류 디버깅 가이드. pipeline-debugger 에이전트가 사용.
  KIS API 인증, 카카오 토큰, 뉴스 수집, DB 조회, GitHub Actions 워크플로우 오류를
  단계별로 진단하는 방법론을 제공한다.
---

## 조사 순서

파이프라인 실행 순서대로 진단한다. 상위 단계에서 막히면 하위 단계는 건너뛴다.

### 1단계: 로그 확인

```bash
tail -150 /Users/wonu/stock_briefing/logs/briefing.log
```

핵심 패턴 탐색:
- `ERROR` / `Exception` — 명시적 오류
- `⚠️ 드롭` — 종목 검증 탈락 현황
- `추천 종목 0개` — 결과 없음
- `⏭️ 중복 실행 skip` — 멱등성 가드 작동 (정상)
- `KST 기준 오늘 날짜` — 실행 날짜 확인

---

### 2단계: KIS API 진단

**토큰 캐시 확인:**
```bash
ls -la /Users/wonu/stock_briefing/.kis_token_cache 2>/dev/null && \
python3 -c "
import json
data = json.load(open('/Users/wonu/stock_briefing/.kis_token_cache'))
print('만료:', data.get('access_token_expired'))
print('토큰 앞 20자:', data.get('access_token','')[:20])
" 2>/dev/null || echo "캐시 파일 없음"
```

**KIS 토큰 갱신 흐름:** `kis_api.py` → `_get_access_token()` → POST `/oauth2/tokenP`
- 만료 시 자동 갱신 (캐시 파일 기반)
- GitHub Actions에서는 `actions/cache@v4`로 KIS 토큰 캐시 복원

**KIS API 오류 코드:**
| `rt_cd` | 의미 |
|---------|------|
| `0` | 성공 |
| `1` | 실패 (msg1 확인) |
| EUB000001 | 인증 오류 — 토큰 재발급 필요 |

---

### 3단계: 카카오 토큰 진단

```bash
ls -la /Users/wonu/stock_briefing/.kakao_tokens 2>/dev/null && \
python3 -c "
import json
data = json.load(open('/Users/wonu/stock_briefing/.kakao_tokens'))
print('access_token 앞 20자:', data.get('access_token','')[:20])
print('refresh_token 앞 20자:', data.get('refresh_token','')[:20])
" 2>/dev/null || echo "카카오 토큰 파일 없음"
```

**카카오 토큰 갱신:**
- `kakao_sender.py`가 발송 실패 시 `kakao_auth.py`로 refresh 시도
- GitHub Actions에서는 `/tmp/kakao_tokens_new.json`으로 갱신 토큰 저장 후 Secret 업데이트
- 토큰이 완전히 만료되면 `kakao_auth.py`로 수동 재인증 필요

---

### 4단계: 뉴스 수집 진단

`news_fetcher.py` 오류 확인:
```bash
grep -n "뉴스\|news\|Naver\|NAVER" /Users/wonu/stock_briefing/logs/briefing.log | tail -20
```

네이버 API 의존: `NAVER_CLIENT_ID`, `NAVER_CLIENT_SECRET` 환경 변수

---

### 5단계: 시장 레짐 진단

```bash
grep -n "regime\|레짐\|bullish\|bearish\|sideways" /Users/wonu/stock_briefing/logs/briefing.log | tail -10
```

레짐 판단 실패 시 `sideways` 기본값으로 폴백 (정상 동작).

---

### 6단계: 기술적 스크리닝 진단

```bash
grep -n "기술적 후보\|스크리닝\|screen" /Users/wonu/stock_briefing/logs/briefing.log | tail -10
```

후보 0개면 KIS 거래량 순위 API 실패 → `technical_screener.py:get_volume_ranking()` 확인

---

### 7단계: AI 추천 진단

```bash
grep -n "AI\|OpenAI\|GPT\|추천\|파싱" /Users/wonu/stock_briefing/logs/briefing.log | tail -20
```

- 파싱 실패: `ai_analyzer.py:_parse_ai_response()` 확인
- 추천 0개: 프롬프트 이슈 or OpenAI API quota
- 재시도 로그: `AI 추천 시도 2/2`

---

### 8단계: 검증 + 점수 필터 진단

```bash
grep -n "드롭\|검증\|필터\|MIN_SCORE\|자격" /Users/wonu/stock_briefing/logs/briefing.log | tail -20
```

탈락 이유 유형:
- `KIS 미인식` — 잘못된 종목 코드
- `ETF/ETN/SPAC` — ETF 필터
- `관리종목` / `거래정지` / `정리매매` — 거래 불가 종목 (stat 51, 52, 53)
- `기술적 후보 아님` — `MIN_SCORE_THRESHOLD` 기준 탈락

**⚠️ KIS `iscd_stat_cls_code` 의미 정정 (2026-05-28 확인):**
- 51: 관리종목 / 52: 거래정지 / 53: 정리매매 (실제 거래 불가)
- **54~59: 시간/가격 상태 코드** (예: 55=시간외/단일가, 57=상한가 도달) — 거래정지가 아님
- 현재 `_BAD_STATUS_CODES = {"51", "52", "53"}` 만 유지 (briefing.py:24-43)
- 진짜 거래정지 판정은 명시 필드(`temp_stop_yn`, `sltr_yn`, `mang_issu_cls_code`, `mrkt_warn_cls_code`)로 수행

**거래상태 검증 false 시 raw 응답 디버그 로깅** (briefing.py:`_log_kis_status_debug`):
```bash
grep "DEBUG-KIS-STATUS" /Users/wonu/stock_briefing/logs/briefing.log | tail -20
```
포맷: `[DEBUG-KIS-STATUS] code=... name=... stat=... temp=... mang=... sltr=... warn=... price=... change=... _check_tradeable: ok=... reason=... keys=[...]`

→ 새로운 stat 코드 패턴이 발견되면 `_BAD_STATUS_CODES` 재검토 단서로 활용.

점수 필터 완화 검토: `config.py:MIN_SCORE_THRESHOLD`를 낮추면 추천 증가하지만 품질 저하 위험

---

### 9단계: DB 상태 진단

```python
import sqlite3
conn = sqlite3.connect('/Users/wonu/stock_briefing/stock_briefing.db')

# 최근 snapshot_logs (멱등성 가드 기준)
rows = conn.execute(
    "SELECT snapshot_date, recommendation_count, market_regime FROM snapshot_logs ORDER BY created_at DESC LIMIT 5"
).fetchall()
print("최근 snapshot:", rows)

# 최근 recommendations
rows = conn.execute(
    "SELECT rec_date, COUNT(*) cnt FROM recommendations GROUP BY rec_date ORDER BY rec_date DESC LIMIT 5"
).fetchall()
print("최근 추천:", rows)
```

**멱등성 가드 동작:** `has_briefing_run_for_date(today)` → `snapshot_logs`에 오늘 날짜 존재 여부 확인
강제 재실행: `python main.py --run-now --force`

---

### 10단계: GitHub Actions 진단

`.github/workflows/briefing.yml` 스텝 순서:
1. checkout → 2. Python 설치 → 3. pip install → 4. KIS 토큰 캐시 복원 → 5. 브리핑 실행 → 6. Kakao 토큰 갱신 → 7. DB + docs/ 자동 커밋 → 8. 결과 요약

**트리거 구조 (2026-05-28 이후):**
- 메인: cron-job.org → `workflow_dispatch` (평일 KST 09:05)
- 백업: GitHub Actions schedule `5 1 * * 1-5` (평일 KST 10:05)
- 멱등성: 같은 날 둘 다 떠도 `has_briefing_run_for_date()`가 중복 차단

```bash
# 최근 run 확인 (event 컬럼으로 트리거 출처 구분)
cd /Users/wonu/stock_briefing && gh run list --workflow=briefing.yml --limit 5 \
  --json databaseId,event,status,conclusion,createdAt \
  --jq '.[] | "\(.createdAt) | \(.event) | \(.conclusion // .status)"'
```

`event=workflow_dispatch` → cron-job.org 또는 수동 / `event=schedule` → GitHub 백업.

브리핑 실행 시 첫 로그에 trigger source 표시 (briefing.py:`_log_trigger_source`):
```
🚀 Trigger source: workflow_dispatch (cron-job.org 또는 수동)
🚀 Trigger source: schedule (GitHub Actions 백업 cron)
```

GitHub Secrets 필요: `KIS_APP_KEY`, `KIS_APP_SECRET`, `KIS_ACCOUNT_NO`, `NAVER_CLIENT_ID`, `NAVER_CLIENT_SECRET`, `OPENAI_API_KEY`, `KAKAO_REST_API_KEY`, `KAKAO_CLIENT_SECRET`, `KAKAO_ACCESS_TOKEN`, `KAKAO_REFRESH_TOKEN`

---

### 11단계: cron-job.org 트리거 진단

GitHub Actions에 트리거가 들어오지 않은 경우 cron-job.org 측 확인:

**자주 발생하는 증상:**
| 증상 | 원인 | 해결 |
|------|------|------|
| 401 Unauthorized | Authorization 헤더에 `Bearer ` 접두사 누락 | Value를 `Bearer github_pat_...` 형식으로 |
| 403 Forbidden | PAT 권한 부족 | Fine-grained PAT의 `Actions: Read and write` 확인 |
| 404 Not Found | URL 오타 또는 PAT의 repo access 누락 | URL 재확인 + PAT scope 확인 |
| 트리거 시각 잘못됨 | Timezone 미설정 (UTC 기본) | cron-job.org Schedule 탭의 Timezone = `Asia/Seoul` |

**Crontab 표준 표현:** `5 9 * * 1-5` (Asia/Seoul, 평일 09:05)

상세 설정 가이드: `docs/cron-job-org-setup.md`

---

## 조사 결과 출력 형식

`_workspace/debug_findings.md`에 저장:

```markdown
## 근본 원인
[한 문장 요약]

## 재현 조건
- 발생 일시:
- 증상:
- 관련 로그:

## 원인 상세
[상세 설명]

## 수정 위치
- 파일: [파일명:라인번호]
- 변경 내용: [구체적 수정]

## 수정 후 검증 방법
[검증 명령어 또는 절차]
```
