# cron-job.org 메인 트리거 설정 가이드

GitHub Actions 기본 `schedule`이 ~14시간씩 지연/누락되는 문제를 회피하기 위해
[cron-job.org](https://cron-job.org/)를 메인 트리거로 사용한다.

평일 **09:05 KST** (장 개장 후 5분)에 cron-job.org가 GitHub API를 호출 → `workflow_dispatch` 이벤트 발생 → `briefing.yml` 즉시 실행.

GitHub Actions `schedule`은 백업으로 KST 10:05에만 유지 (멱등성 가드가 중복 차단).

### 왜 09:05인가? (06:30이 아니라)

KIS Open API의 `inquire-price`는 의도된 사용 시점이 **장 운영 시간(09:00~15:30 KST)** 이라,
그 외 시간에 호출하면 우량주에도 매매중단/거래정지 status code(54, 55, 57)를 비정상 반환함.

→ 장 개장(09:00) 후 5분 = 시가 형성 + 첫 거래량 반영 + 모멘텀 시그널 가장 강한 시간대.

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
| Title | `Stock Briefing Daily 09:05 KST` |
| Address (URL) | `https://api.github.com/repos/gezzang82/stock-briefing/actions/workflows/briefing.yml/dispatches` |
| Enabled | ✅ |

#### Schedule 탭

| 필드 | 값 |
|---|---|
| Timezone | `Asia/Seoul (UTC+09:00)` |
| Days of month | every |
| Months | every |
| Days of week | Mon, Tue, Wed, Thu, Fri (월~금만 체크) |
| Hours | `9` |
| Minutes | `5` |

cron 표현식으로 입력 가능하면: `5 9 * * 1-5` (Asia/Seoul 기준)

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

같은 날 cron-job.org(09:05)와 GitHub 백업 schedule(10:05) 둘 다 트리거되어도
실제 브리핑은 1회만 실행됨.

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
09:05 KST  cron-job.org → POST GitHub API → workflow_dispatch
                                          ↓
                                briefing.yml 즉시 실행
                                          ↓
                            has_briefing_run_for_date = False
                                          ↓
                              브리핑 진행 → 카카오톡 발송
                                          ↓
                            snapshot_logs에 오늘 row 추가

10:05 KST  GitHub schedule (백업)
                                          ↓
                            has_briefing_run_for_date = True
                                          ↓
                              ⏭️ 중복 실행 skip
```

cron-job.org가 정상 동작하는 한 GitHub 백업은 항상 skip된다 (정상).
cron-job.org가 실패한 날만 백업이 의미를 가짐.

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

## 7. 트러블슈팅

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
