# Claude 구독 독립 검토 실행기

공식 Pro/Max 로그인으로 Opus 5를 호출하는 선택 기능이다. 기본값은 비활성화이며, 회사의
기존 직원 개발 루프가 `staff_independent_reviews`에 보관한 답안만 검토한다. 결제 API 키가
필요하지 않다. 현재 배포 이미지는 검증한 Linux x86_64 바이너리의 SHA-256까지 고정한다.

## 최초 연결

1. 계정 소유자가 Claude 설정에서 usage credits/extra usage를 끈다. 이는 계정 설정이므로
   실행기가 자동 확인/변경할 수 없다. 별도 API 환경변수나 OAuth token 문자열을 전달하지 않는다.
2. `deploy/prepare-state.py --apply`로 전용 호스트의 `/var/lib/quant-company/claude/auth`와
   `claude/jobs`를 UID 10001, mode 0700으로 준비한다. 승인된 exact commit을 빌드한다.
3. 호스트의 대화형 터미널에서 아래 로그인 명령을 실행한다. 출력된 공식 URL에서 계정을
   승인하고 필요한 코드를 그 터미널에만 입력한다. 로그인 출력/인증 파일을 Git·채팅에 복사하지 않는다.

```bash
sudo docker compose --profile claude --env-file /var/lib/quant-company/config/runtime.env \
  -f deploy/compose.yaml run --rm --no-deps claude-runtime claude auth login
```

같은 환경에서 `claude auth status`의 loggedIn/authMethod/subscriptionType만 확인한다.
`claude.ai` 및 `pro`/`max`여야 한다. 일회성 로그인 컨테이너가 쓴 인증 디렉터리를 실행기에도
동일하게 마운트한다. `--bare`와 `--console` 인증은 사용하지 않는다.

## 활성화와 운영

계정 소유자의 확인 후 비밀 값 없는 `runtime.env`에 다음 설정을 저장한다.

```dotenv
COMPANY_STAFF_REVIEW_ENABLED=true
STAFF_REVIEW_DAILY_LIMIT=2
CLAUDE_USAGE_CREDITS_DISABLED_CONFIRMED=true
CLAUDE_MEMORY_LIMIT=512m
```

`--profile maintenance --profile claude`로 claude-runtime 및 새 app 이미지를 시작한다.
worker의 기존 비공개 bearer token으로 호출하고 Claude 계정 인증은 Codex와 분리한다.
Claude에는 DB·Slack·AWS·GitHub key와 Docker socket이 없다. 모델 자식 프로세스에도
bearer token을 넘기지 않는다. `/healthz`는 HTTP 생존 확인으로 인증/구독 확인을 대신하지 않는다.

`staff_status`의 independent_review에 결과와 오류가 보인다. 아직 보정되지 않은 평가이므로
직원 등급/절차 자동 승격에는 사용하지 않는다. quota/auth 장애는 업무 능력의 실패가 아니다.
과제 원문/답안/권한이 달라졌거나 허위 인용/다른 모델/도구 실행이 감지되면 결과 저장을 막는다.
Claude가 세션에 없는 도구를 부르면 CLI가 `No such tool available` 오류 결과로 거절하고, 실행되는 것은 없다.
다음 두 조건이 모두 맞을 때만 결과를 받아들이고, 거절된 도구 이름을 `usage.refused_tool_attempts`에 남긴다.
- 세션 초기화 기록의 도구 목록에 StructuredOutput만 있다.
- 그 호출에 CLI의 거절 결과가 붙어 있다.

그 밖의 도구 호출은 지금처럼 `uncertain`이다. 거절 뒤 실행이 실패해도 `uncertain`이다.
10/09~10/22 실패 13건 중 7건은 StructuredOutput이 아닌 도구 호출 때문에 버려졌다. 원 출력이 남아 있지 않아서
CLI가 거절했는지는 확인하지 못했다. 확정되지 않은 실행의 영수증에는 종료 코드, 결과 종류, 오류 코드만 남기고
모델 출력 문장은 남기지 않는다.

갱신 토큰 만료 시각(`.credentials.json`의 `refreshTokenExpiresAt`)이 지난 뒤 처음 토큰을 갱신할 때 CLI가 로그인을
비우고, 다시 로그인하기 전에는 살아나지 않는다(만료 2026-10-18 01:03, 비움 10-19 00:01 KST). 이때 일일 영상은 중단하지 않고 같은 요청 ID로 5분마다 다시 묻고, 브리핑 스레드에
'영상 제작 대기' 알림을 한 번 남긴다. 위 로그인 명령으로 갱신하면 그 단계부터 이어서 만든다.

운영 감시는 만료 7일 전과 2일 전, 그리고 로그인이 풀렸을 때 운영 채널에 알린다(docs/ops-watch.md). 갱신은 Mac 터미널에서
`ssh -t <회사 서버> sudo bash /opt/quant-company/current/deploy/claude-login.sh` 한 줄로 한다. 위 compose 로그인 명령을
실행한 뒤 loggedIn/authMethod/subscriptionType과 다음 만료 시각만 보여 준다. `status` 인자는 로그인 없이 상태만 본다.

완료 결과는 원래 request ID로 복구한다. `running`/`failed` receipt가 남은 불확실한 호출은
새 ID로 재시도하지 말고 해당 DB 행과 실행기 receipt를 먼저 대사한다. 직원 개발을 끄면
정기 검토도 멈춘다. 검토만 끄려면 `COMPANY_STAFF_REVIEW_ENABLED=false`를 반영하고
진행 중 호출이 끝난 후 claude-runtime을 정지한다. 계정의 추가 사용량 설정을 바꿀 때도 먼저 끈다.

정기 백업은 Claude까지 정지하고 `claude-jobs/` 영수증을 DB와 함께 저장한다. 인증은 제외한다.
복구 시 DB와 두 제공자의 영수증을 함께 대사한 뒤 활성화한다. 이전 Claude 없는 백업도 읽을 수 있다.
유지보수 release executor는 활성화된 Claude 이미지도 갱신하고 동일 commit/상태를 검사한다.

2GB 기본 구성의 1664 MiB 정적 합계에는 이 profile과 maintenance가 들어 있지 않다.
컨테이너 상한은 예약 용량이 아니므로 실행 중 가용 메모리/OOM을 확인한다. 새 서버·상위 요금제로
자동 변경하지 않는다. 구독 한도는 개인 Claude 사용과 공유하며 고정된 일일 호출량을 보장하지 않는다.

## API 크레딧 레인 (ADR 0048)

Max 20x 구독자에게 주는 월 $200 API 크레딧(Console 조직에 들어가며 주기마다 사라지고, 구독 로그인 사용에는 쓰이지 않는다)을
쓰기 위한 두 번째 경로다. 같은 claude-runtime 안에서 요청마다 경로를 고른다. 요청 ID, 요청 바이트, 계약, 모델, effort,
결과 검증은 그대로다. 기본값은 꺼짐이다.

### 경로

| 구분 | 계약 | 동작 |
|---|---|---|
| API 우선 | `video_shorts_v1`, `newsletter_v1`, `video_performance_v1`, `video_weekly_v1`, `video_answer_v1`, `agent_decision`, `video_screen_fix_v1` | 레인이 켜져 있고 예산이 되면 API로 보낸다. 아무것도 실행되지 않은 채 거절되면 같은 요청을 구독(CLI)으로 보낸다. |
| 구독 우선 | `video_episode_v1`, `video_plan_v1`, `video_review_v1`, `video_visual_review_v1` | CLI로 실행한다. CLI가 `auth`(로그인 실패) 또는 `quota`(토큰 없는 `rate_limit`)로 끝나 아무것도 실행되지 않았을 때만 예산이 되면 API로 대신한다. 이미 `deferred`로 기록된 요청을 다시 물어도 같다. |

- CLI가 `uncertain`, `invalid_output`, `timeout`으로 끝난 요청은 API로 넘기지 않는다. 지금과 똑같이 끝난다.
- API가 스트림을 시작한 뒤 끊기거나 오류 이벤트를 보내거나 시간이 지나면 `uncertain`이다. 구독으로 넘기지 않고 다시 실행하지도 않는다.
- 모델이 `PRICES`에 없으면 API로 보내지 않는다(구독 우선 요청은 그대로 기다린다).
- 한 요청 ID는 한 번에 한 곳에서만 묻는다. 다른 경로가 같은 ID를 실행 중이면 `pending`을 돌려준다.
- API 경로는 CLI 칸(`.runtime.lock`)을 쓰지 않는다. 자기 칸에서 최대 2건이 동시에 돈다. 마감 영상이 CLI 칸을 쓰는 동안에도
  메일, Shorts, 성과 분석은 기다리지 않는다.
- 메일의 "영상에 칸 양보"(`NewsletterWriter.yield_to_video`)는 API로 도는 메일을 취소하지 않는다. 런타임이 `kept`(API로 도는 중)나
  `completed`(이미 끝나 결과가 같은 ID로 저장됨)를 돌려주면 양보하지 않고, 영상이 모델을 다 쓴 뒤 같은 ID로 물어 그 결과를 받는다. 대체 글로 바뀐 메일(`cancel_replaced`)과 오래된 turn의 취소는 API 요청도 끊는다. 이미 시작한 API 호출을
  끊으면 영수증은 `uncertain`이 되고(예약액을 쓴 것으로 친다) 취소 응답도 `uncertain`이다. 이 취소로는 레인을 쉬게 하지 않는다.

### 상한과 스위치

- 상한: 최근 31일(이동 창) API 지출 합계가 `CLAUDE_API_CAP_USD`를 넘게 되는 예약은 거절한다. 기본값 0은 꺼짐이고 운영값은 190이다.
  마지막 방어선은 Console의 월 지출 한도($200, 자동 충전 끔)다.
- 장부는 `claude/jobs/.api/`의 파일이다(파일 잠금). 호출 전에 최악 비용(입력 추정 + `max_tokens` 전부를 출력 단가로)을 예약하고,
  끝나면 실제 사용량으로 정산한다. 시도마다 `{요청 ID}#{n}`으로 따로 기록한다. `uncertain`, `timeout`, 500, 504는 예약액을 쓴 것으로 친다.
- 스위치: `CLAUDE_API_ENABLED=false`면 모든 요청이 지금의 구독 경로로 돌아간다(기본값). 키 파일이 비었거나 없어도 API는 실행되지 않는다.
- 영수증: 성공한 API 결과의 `usage`에 `billing_mode: "api_credit"`, `backend`, `cost_usd`, 토큰 네 종류, `actual_model`이 들어간다.
  구독은 `billing_mode: "subscription"`이다. 시도마다 `attempts`에 backend와 fault가 남는다. `provider`는 `claude` 그대로다.

### 쉬는 경우

| 사건 | 동작 |
|---|---|
| API 쪽 `uncertain` 또는 `timeout` | 30분 쉰다. 호출하는 쪽이 새 ID로 다시 불러도 돈이 연속으로 나가지 않게 하는 제동이다. 사장님이 일부러 한 취소는 쉬게 하지 않는다. |
| 429 속도 제한 | `retry-after`초(없거나 0 이하면 60초) 쉰다. |
| 529 과부하 | 60초 쉰다. |
| 연결 실패(연결 시간 초과, 프록시·TLS 포함) | 60초 쉰다. 아무것도 실행되지 않은 것으로 본다. |
| 401·403 | 런타임을 다시 띄울 때까지 쉰다. 키 파일을 고친 뒤 claude-runtime을 다시 만든다. |
| 429 `enforced_spend_limit_reached` | 다음 달 1일 00:00 UTC까지 쉰다. |
| 400·413 등 그 밖의 거절 | 쉬지 않는다. 그 요청만 실행되지 않은 것으로 구독 경로로 간다. |
| 500·504 | 실행됐는지 모른다. 예약액을 쓴 것으로 치고, 그 요청 ID는 다시 API로 보내지 않는다. |

쉬는 동안 API 우선 요청은 구독으로 가고, 구독 대체 요청은 지금처럼 기다린다(`deferred`). 상한 때문에 거절된 요청도 같다.
구독 로그인이 길게 끊기면 본편 대체가 하루 약 $25씩 상한을 쓸 수 있다. 예산을 따로 떼어 두지 않는다. 대신 알림으로 빨리 다시 로그인하게 한다.

### 호출 방식

`POST https://api.anthropic.com/v1/messages`를 `httpx`로 스트리밍한다(새 라이브러리 없음). `x-api-key`는 요청 때 파일
(`/run/secrets/claude_api_key`)에서 읽어 헤더에만 쓰고, 환경변수·로그·영수증·오류 문구에는 남기지 않는다. CLI 실행 환경에는 넘기지 않는다.
effort는 `output_config.effort`로 항상 명시한다(빠뜨리면 Opus 5.5는 `medium`으로 돈다). 프롬프트 캐시(`cache_control`)는 쓰지 않는다.
7일 영수증에서 캐시 읽기는 저장의 6%였고 저장 단가가 입력보다 25% 비싸서 안 쓰는 쪽이 싸다.

결과는 API의 엄격 JSON 출력(`output_config.format`)이 아니라 `StructuredOutput` 도구 하나로 받는다. 엄격 출력은 우리 계약 스키마가 쓰는
길이·숫자 범위·배열 제약을 400으로 거절한다. CLI의 `--json-schema`도 같은 이름의 도구로 받으므로 방식이 같다. `tool_choice`는 `auto`다
(생각이 켜진 모델은 강제 선택을 받지 않는다). 첫 `StructuredOutput` 호출의 입력을 계약 스키마로 검증하고, 다른 도구나 텍스트만 온 응답,
`stop_reason`이 `tool_use`가 아닌 응답(`max_tokens`, `refusal`), 요청과 다른 모델은 `invalid_output`이다.
`max_tokens`는 메일, 성과 분석, 답변, 브리핑 검토가 64,000, 그 밖의 계약이 128,000이다.

단가(100만 토큰당 USD, 긴 문맥 할증 없음). 출처: https://platform.claude.com/docs/en/about-claude/pricing (2026-10-23 확인). 값은 `providers/claude_api.py`의 `PRICES`다.

| 모델 | 입력 | 출력 | 캐시 저장(5분) | 캐시 읽기 |
|---|---|---|---|---|
| `claude-opus-5-5` | 4 | 20 | 5 | 0.20 |
| `claude-opus-5` | 5 | 25 | 6.25 | 0.50 |

### 상태와 알림

런타임이 `/var/lib/quant-company/claude/jobs/.api/status.json`에 켜짐·꺼짐·쉼(이유와 풀리는 시각), 31일 사용액, 상한, 경로별 호출 수,
마지막 구독 대체 시각을 쓴다. 새 HTTP 경로는 없다(`/healthz`는 자격 정보를 읽지 않는다). `deploy/ops_probe.py`가 이 파일을 읽는다.

- `ops status` 한 줄: `🤖 Claude API: 31일 $X / $190 · 켜짐|꺼짐|쉼(이유)`.
- #ops-alerts(`ops/watch.py`): 장부를 읽지 못함(critical), 키 문제·지출 한도로 멈춤(critical), 30분 uncertain 쉼 등 그 밖의 멈춤(warning),
  31일 사용액이 상한의 80% 이상(warning), 최근 30분 안에 구독 우선 작업이 API로 대체됨(warning, 다시 로그인하라는 뜻).
  속도 제한과 과부하는 1분 안에 풀려 알리지 않는다. 레인이 꺼져 있으면(`off`) 장부 읽기 실패 외에는 알리지 않는다.

### 키 파일 (사장님이 직접)

키는 채팅·Slack·Git에 붙이지 않는다. 아래 명령은 키를 화면에 보이지 않게 받고 셸 기록에도 남기지 않는다.
**PR을 병합하기 전에** 실행한다(deployment.md 참고).

```bash
ssh -t -i ~/.ssh/quant-company-lightsail-rsa ubuntu@15.165.137.122 'read -rsp "Claude API key: " k; echo; printf %s "$k" | sudo install -D -m 444 -o root -g root /dev/stdin /var/lib/quant-company/secrets/claude_api_key; unset k; sudo test -s /var/lib/quant-company/secrets/claude_api_key && echo saved'
```

`saved`가 나오면 끝이다. 파일 모드가 444인 것은 컨테이너의 비특권 사용자가 읽게 하기 위해서다(호스트 접근은 root·ubuntu 계정으로 제한된다).
