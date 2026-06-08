# cron-job.org 메인 트리거 설정 가이드

GitHub Actions 기본 `schedule`이 ~14시간씩 지연/누락되는 문제를 회피하기 위해
[cron-job.org](https://cron-job.org/)를 단독 트리거로 사용한다.

평일 **18:00 KST** (시간외 단일가 종료 직후, EOD)에 cron-job.org가 GitHub API를 호출 → `workflow_dispatch` 이벤트 발생 → `briefing.yml` 즉시 실행.

> **2026-06-04 변경**: GitHub Actions `schedule` 백업을 제거했다. 5/5건 모두 11~14시간 지연 발화 → 다음날 새벽 backup이 cron-job.org 18:00 메인보다 먼저 snapshot을 선점해 메인을 무력화하는 사고가 반복됨 (백업이 메인을 무력화). cron-job.org는 4일 100% 정시 발화 검증 후 단독 운영.
>
> cron-job.org 자체 장애 시 fallback: failure notification 이메일을 받은 사용자가 GitHub Actions UI에서 `workflow_dispatch`를 수동 실행.

### 왜 18:00 KST (EOD)?

| 측면 | 이유 |
|---|---|
| **KIS 데이터** | 하루 거래량/외국인기관 매매 완전 확정 — 점수 시그널 최고 품질 |
| **시간외 종가 반영** | 15:30~16:00 시간외 종가 데이터 추가 정보 활용 |
| **사용자 편의** | 다음 영업일 09:00 시가까지 17시간 → 퇴근 후 분석 + 매수 준비 여유 |
| **스윙 트레이딩 적합** | TRACKING_DAYS=14 보유 전략에 갭 위험 < 데이터 품질 |

### 시간 변경 이력

- 2026-05-26 ~ 05-27: 06:30 KST (장 시작 전, KIS 비정상 응답으로 추천 0개)
- 2026-05-28 ~ 06-01: 09:05 KST (장 개장 후, 거래량 데이터 누적 부족으로 후보 2~3개)
- 2026-06-01 (오전): 16:00 KST EOD 시도 → 사용자 편의 위해 18:00으로 재조정
- **2026-06-01 ~ : 18:00 KST EOD** (시간외 단일가 종료 직후, 데이터 최완전)
- **2026-06-04 ~ : cron-job.org 단독 운영** (GitHub Actions `schedule` 백업 제거)

---

## 1. GitHub Personal Access Token (PAT) 생성

### Fine-grained personal access token 권장

1. https://github.com/settings/personal-access-tokens/new 로 이동
2. 설정:
   - **Token name**: `stock-briefing-cron`
   - **Expiration**: 1 year (또는 No expiration)
   - **Repository access**: `Only select repositories` → `gezzang82/stock-briefing` 선택
3. **Repository permissions**:
   - `Actions`: **Read and write** ← 필수 (워크플로우 트리거)
   - `Contents`: **Read-only**
   - 나머지는 기본값
4. **Generate token** 클릭
5. 생성된 토큰(`github_pat_...`)을 복사 — 한 번만 표시되므로 안전한 곳에 보관

---

## 2. cron-job.org 가입 + Cron Job 생성

### 2-1. 가입

- https://cron-job.org/en/signup/ 에서 이메일 가입 (무료)
- 로그인

### 2-2. CREATE CRONJOB 클릭

#### Common 탭

| 필드 | 값 |
|---|---|
| Title | `Stock Briefing Daily 18:00 KST (EOD)` |
| Address (URL) | `https://api.github.com/repos/gezzang82/stock-briefing/actions/workflows/briefing.yml/dispatches` |
| Enabled | ✅ |

#### Schedule 탭

| 필드 | 값 |
|---|---|
| Timezone | `Asia/Seoul (UTC+09:00)` |
| Days of month | every |
| Months | every |
| Days of week | Mon, Tue, Wed, Thu, Fri (월~금만 체크) |
| Hours | `18` |
| Minutes | `0` |

cron 표현식으로 입력 가능하면: `0 18 * * 1-5` (Asia/Seoul 기준)

#### Advanced 탭

| 필드 | 값 |
|---|---|
| Request method | `POST` |
| Request body type | `application/json` |
| Request body (Custom data) | `{"ref":"main"}` |
| Treat redirects as success | ✅ |
| Request timeout | `30` seconds |

#### Headers 탭 — 3개 헤더 추가

| Header Name | Value |
|---|---|
| `Authorization` | `Bearer github_pat_여기에_PAT_붙여넣기` |
| `Accept` | `application/vnd.github+json` |
| `X-GitHub-Api-Version` | `2022-11-28` |

#### Notifications 탭 (강추)

- ✅ **Notify on failure**: 잡 실패 시 이메일 알림 받기

#### Save 클릭

---

## 3. 테스트 방법

### 3-1. 즉시 실행 테스트

1. cron-job.org의 잡 상세 페이지에서 **"Run now"** (또는 "Execute now") 버튼 클릭
2. 응답 코드 확인 → `204 No Content` 가 나오면 성공
3. https://github.com/gezzang82/stock-briefing/actions/workflows/briefing.yml 페이지 새로고침
4. 새 `workflow_dispatch` 실행이 즉시 떠야 정상

### 3-2. 로그 확인

워크플로우 실행 로그에서 첫 줄에:
```
🚀 Trigger source: workflow_dispatch (cron-job.org 또는 수동)
```
이 출력되면 cron-job.org 호출 인식됨.

### 3-3. 실제 정시 동작 확인

- 다음 평일 09:05 KST에 cron-job.org에서 실행 기록 생성
- GitHub Actions에서 같은 시각에 workflow run 시작
- 카카오톡 도착 시각 확인 (보통 09:05~09:10 KST 사이)

---

## 4. 중복 실행 방지 — 멱등성 가드

cron-job.org 단독 운영이지만 멱등성 가드는 유지한다 (수동 `workflow_dispatch` 재실행, cron-job.org 재시도 등으로 같은 날 2회 트리거되어도 실제 브리핑은 1회만 실행).

### 가드 흐름

```
[브리핑 실행]
    ↓
KST 기준 오늘 날짜 계산
    ↓
has_briefing_run_for_date(today) 호출
    ↓
snapshot_logs 테이블에 같은 날짜 row 있나?
    │
    ├─ True  → "⏭️ 오늘 브리핑 이미 실행됨 — 중복 실행 skip" 후 정상 종료
    │
    └─ False → 정상 진행
```

- `--force` 플래그가 있으면 가드 무시 (수동 디버깅/재실행용)
- `snapshot_logs`는 자격 추천 0개여도 항상 저장되므로 견고함

---

## 5. 실제 운영 흐름

```
18:00 KST  cron-job.org → POST GitHub API → workflow_dispatch
                                          ↓
                                briefing.yml 즉시 실행
                                          ↓
                            has_briefing_run_for_date = False
                                          ↓
                              브리핑 진행 → 카카오톡 발송
                                          ↓
                            snapshot_logs에 오늘 row 추가
                                          ↓
                          health_check --post-brief (snapshot 검증)
```

장애 대응:
- cron-job.org 발화 실패 → failure notification 이메일 자동
- 18:05까지 카톡 미수신 → GitHub Actions UI에서 `Run workflow` 수동 트리거
- 멱등성 가드가 같은 날 중복 실행은 자동 차단

---

## 6. 보안 메모

- PAT는 **cron-job.org 서버**에만 저장됨 (HTTPS 전송)
- 우리 레포에는 PAT 흔적 없음 (코드/시크릿/문서 어디에도 X)
- PAT 만료 시 cron-job.org 잡이 실패 → 이메일 알림 → 갱신
- PAT 노출 의심 시 즉시 https://github.com/settings/tokens 에서 revoke

### Fine-grained PAT vs Classic PAT

- **Fine-grained** 권장: 단일 레포 + `Actions: Read and write`만 허용 (최소 권한)
- **Classic** 사용 시: `workflow` scope 필요 (모든 워크플로우 트리거 가능 — 권한 과대)

---

## 7. 주간 리포트 잡 (Weekly Report) 추가

briefing.yml과 동일한 구조로 weekly_report.yml도 cron-job.org 트리거로 운영. cron-job.org에 **두 번째 잡**을 추가한다.

### 7-1. CREATE CRONJOB → Common 탭

| 필드 | 값 |
|---|---|
| Title | `Stock Briefing Weekly Report 19:00 KST (Sun)` |
| Address (URL) | `https://api.github.com/repos/gezzang82/stock-briefing/actions/workflows/weekly_report.yml/dispatches` |
| Enabled | ✅ |

> ⚠️ URL이 일일 브리핑과 다름 — `briefing.yml` 대신 **`weekly_report.yml`**.

### 7-2. Schedule 탭

| 필드 | 값 |
|---|---|
| Timezone | `Asia/Seoul (UTC+09:00)` |
| Days of month | every |
| Months | every |
| Days of week | **Sun만 체크** |
| Hours | `19` |
| Minutes | `0` |

cron 표현식으로 입력 가능하면: `0 19 * * 0` (Asia/Seoul 기준)

### 7-3. Advanced 탭

| 필드 | 값 |
|---|---|
| Request method | `POST` |
| Request body type | `application/json` |
| Request body (Custom data) | `{"ref":"main"}` |
| Treat redirects as success | ✅ |
| Request timeout | `30` seconds |

### 7-4. Headers 탭 (3개 — 일일 브리핑 잡과 동일)

| Header Name | Value |
|---|---|
| `Authorization` | `Bearer github_pat_...` (일일 브리핑 잡과 동일 PAT 재사용 가능) |
| `Accept` | `application/vnd.github+json` |
| `X-GitHub-Api-Version` | `2022-11-28` |

### 7-5. Notifications 탭

- ✅ **Notify on failure**: 잡 실패 시 이메일 알림

### 7-6. 테스트

1. 잡 저장 후 **"Run now"** → `204 No Content` 응답 확인
2. GitHub Actions의 `주간 리포트` 워크플로우에서 `workflow_dispatch` 실행 확인
3. 실제 정시 동작은 다음 일요일 19:00 KST에 카톡 도착 확인

---

## 8. 트러블슈팅

### "Run now"에서 401 Unauthorized

- PAT 권한 부족 → Fine-grained 재발급해서 `Actions: Read and write` 확인
- PAT 만료 → 새로 발급

### "Run now"에서 404 Not Found

- URL 오타 (특히 repo 이름) 확인
- PAT가 해당 레포에 접근 권한 없음 → repository access 재확인

### GitHub Actions에서 workflow_dispatch가 안 보임

- cron-job.org 응답 본문이 빈 문자열이고 코드가 `204` 이면 정상이지만 화면 새로고침 필요
- 다른 코드(예: 422)면 `ref` body 형식 확인 → `{"ref":"main"}`

### 카톡이 09:05가 아니라 09:06~09:08에 옴

- 정상. cron-job.org 트리거 → GitHub API → 워크플로우 큐잉 → KIS API 호출 등 누적 지연 1~3분.
- 1분 이내 정확도 필요하면 다른 cron 서비스(Cloudflare Workers Cron 등) 검토.
