# Claude API 크레딧 레인 설계 (2026-10-23)

## 왜 바꾸나
사장님(10/23):
> "Anthropic 에서 20X max 구독자에게는 매월 $200 무료 API 크레딧을 주는 것으로 알고 있어서 이 무료 크레딧을 어떻게 잘
> 활용 할 수 있을까 해서리,,,"

제안(평소엔 구독, 크레딧은 정해 둔 작은 작업과 장애 대비에 쓰기, 월 $190에서 멈춤)에 대한 답:
> "응 나는 다 준비되었어."

사장님 쪽 준비(크레딧 받기, Console 조직 연결, API 키 만들기, Console 지출 한도 설정)는 사장님 말로는 끝났다.

### 지금 상태
- `claude-runtime` 컨테이너가 Claude Code CLI 2.1.280(`claude -p`)을 사장님 Max 구독 로그인으로 부른다.
  - 도구, MCP, 세션 저장은 모두 끈다. 결과는 JSON 스키마 하나로만 받는다(`providers/claude_runner.py`).
- 실행 칸은 하나다(`.runtime.lock`). 다른 요청이 돌고 있으면 `busy`가 되어, 호출한 쪽이 다시 묻는다.
  - 저녁 메일은 마감 영상과 이 한 칸을 나눠 쓴다(`newsletter_timeout_seconds` 주석).
- 구독 한도에 걸리면(`quota`) 900초 미뤄진다(`deferred`).
- 로그인이 만료되면(`auth`) 사장님이 다시 로그인할 때까지 멈춘다. 다음 만료는 2026-11-15 15:35 KST다.
- API 키 환경변수는 아예 거부한다(`FORBIDDEN_AUTH`). CLI 실행 환경에는 PATH·HOME 등 화이트리스트 값만 넘긴다(`environment()`).

### 최근 7일 사용량
10/16~10/23, 서버 영수증 합산(읽기만 함). 공개 단가 기준 API 환산 금액이다.
- 단가: Opus 5.5 입력 $4, 출력 $20, 캐시 저장 $5, 캐시 읽기 $0.20 (모두 100만 토큰당).
- 월 환산은 7일 값에 3.8을 곱했다(장 열린 날 약 5.5일 → 21일).

| 계약 | 작업 | 호출 | 7일 환산 | 월 환산 |
|---|---|---|---|---|
| `video_episode_v1` | 본편 대본 작성 (xhigh) | 24 | $48 | 약 $185 |
| `video_review_v1` | 본편 각색 검토 (max) | 22 | $44 | 약 $170 |
| `video_visual_review_v1` | 완성 화면·사진 검수 (max) | 44 | $49 | 약 $185 |
| `video_screen_fix_v1` | 화면 수정 (xhigh) | 22 | $9 | 약 $35 |
| `video_shorts_v1` | Shorts 대본 (high) | 19 | $5 | 약 $18 |
| `video_performance_v1`·`video_weekly_v1`·`video_answer_v1` | 성과 카드·전략 리포트·스레드 답 | 13 | $5 | 약 $30 |
| `newsletter_v1` | 브리핑 메일 (high) | 10 | $3 | 약 $11 |
| `agent_decision` | 브리핑 독립 설명 검토 (`claude-opus-5`, max) | 8 | $2 | 약 $6 |
| | **합계** | 161 | 약 $165 | 약 $640 |

크레딧은 Console의 '프로모션 크레딧'으로 들어오고, 주기마다 사라진다. Claude Code 구독 사용에는 쓰이지 않는다. 그래서 지금 방식 그대로면 크레딧은 한 푼도 쓰이지 않는다.

## 목표
1. **정해 둔 작업은 평소 API로 보낸다.** 대상은 Shorts, 메일, 성과 분석 3종, 브리핑 독립 검토, 화면 수정이고, 월 약 $100 규모다. 사장님이 승인한 목록이다.
2. **나머지(본편 대본·각색 검토·화면 검수)는 구독을 먼저 쓴다.** 구독이 확실히 실행되지 않은 경우, 즉 로그인 만료나 한도 거절일 때만 API로 대신한다.
3. **API 지출은 최근 31일 합계 $190을 넘지 않는다.** 넘을 것 같으면 API를 쓰지 않는다. 목표 1의 작업은 구독으로 돌아가고, 목표 2의 대체는 지금처럼 기다린다.
4. **바깥에서 보면 거의 바뀌지 않는다.** 요청 ID, 요청 바이트, 계약, 모델, effort, 결과 검증은 그대로다. 호출하는 쪽에서 바뀌는 곳은 메일의 "영상에 칸 양보" 규칙 하나뿐이다(아래 9-1).
5. **API 쪽이 고장 나도 영상과 메일은 지금처럼 나간다.** 키 오류, 크레딧 소진, API 장애 모두 구독 경로로 돌아간다.

## 하지 않는 것
- 새 모델, 새 effort, API 도구(웹 검색 등)는 쓰지 않는다. 출력 계약도 그대로다.
- Codex 런타임과 이미지 런타임은 손대지 않는다.
- 새 Python 라이브러리를 넣지 않는다. `pyproject.toml`·`uv.lock`은 렌더 이미지 입력(`release.RENDER_FILES`)이라 바꾸면 :15 재빌드가 필요하다. 이미 있는 `httpx`로 부른다.
- Batch API는 쓰지 않는다. 결과가 몇 시간 늦을 수 있어 마감이 있는 작업에 맞지 않는다.

## 절대 안 되는 것
- **카드 결제.** 서비스 상한($190)이 1차로 막고, Console 지출 한도($200, 자동 충전 끔)가 마지막 방어선이다.
- **키 노출.** 키가 로그, Slack, Git, 영수증, CLI 실행 환경, 오류 메시지 어디에도 나오면 안 된다.
- **두 번 실행.**
  - 결과가 불확실해진 API 호출은 자동으로 다시 하지 않는다. AGENTS.md: "Every external effect may be ambiguous after a crash. Preserve receipts; do not claim exactly-once delivery or automatically replay an uncertain charge or write."
  - 구독 쪽이 불확실하게 끝난 요청을 API로 다시 돌리지도 않는다.
- **조용한 유료 전환.** AGENTS.md: "Do not silently switch to paid API credentials or unsandboxed execution."
  - 이번 전환은 사장님이 명시적으로 정했다.
  - 상한, 켜고 끄는 스위치, 영수증의 `billing_mode`로 언제 API를 썼는지 늘 드러나야 한다.

## 정한 방식

### 1. 어느 경로로 보낼지 (claude-runtime 안에서, 요청마다)
경로는 요청의 `output_contract`로 정한다.

| 구분 | 계약 |
|---|---|
| **API 우선** | `video_shorts_v1`, `newsletter_v1`, `video_performance_v1`, `video_weekly_v1`, `video_answer_v1`, `agent_decision`, `video_screen_fix_v1` |
| **구독 우선** | `video_episode_v1`, `video_plan_v1`, `video_review_v1`, `video_visual_review_v1` |

**API 우선 요청**
- API가 켜져 있고 예산 예약이 되면 API로 보낸다.
- 다음처럼 API가 확실히 실행되지 않았으면, 같은 요청을 지금의 구독 경로로 보낸다.
  - API 꺼짐, 예산 부족, 키 없음
  - 스트림이 시작되기 전에 받은 거절: 401, 403, 400, 429, 529, 5xx
- 스트림이 시작된 뒤 끊기면 `uncertain`이다. 구독으로 넘기지 않는다.

**구독 우선 요청**
- 지금처럼 CLI로 실행한다.
- CLI가 다음 결과로 끝났을 때만, 예산이 되면 API로 실행한다.
  - `auth`: 사전 점검에서 로그인 실패, 또는 출력 토큰 없는 `authentication_failed`
  - `quota`: 출력 토큰 없는 `rate_limit`
- `uncertain`, `invalid_output`, `timeout`이면 지금과 똑같이 끝낸다. API로 넘기지 않는다.
- 이미 `deferred`(한도)로 기록된 요청을 다시 물어 와도, 예산이 되면 바로 API로 실행한다.

### 2. 영수증 (요청 ID당 파일 하나, 지금 형식 확장)
- 시도마다 `attempts` 목록에 `{backend: "subscription"|"api", fault?}`를 남긴다. 성공한 결과의 `usage`에는 다음이 들어간다.
  - `billing_mode`: `"subscription"` 또는 `"api_credit"`
  - `backend`, `cost_usd` (API일 때, 단가표로 계산)
  - 토큰 네 종류
  - `actual_model`: 응답이 보고한 모델
  - `effort_evidence`: API는 `"requested_api_parameter"`
- `provider`는 `"claude"` 그대로다. 호출하는 쪽은 `provider`와 `actual_model`만 확인하므로 고칠 곳이 없다.
  - 확인한 곳: `video/runner.py` `single_artifact`, `video/analytics.py`, `staff/independent_review.py`
- 다시 묻기 규칙은 지금과 같다.
  - `complete`면 같은 결과를 돌려준다.
  - API가 실행 중이면 `pending`이다.
  - API가 `uncertain`이면 다시 실행하지 않고 운영자 확인을 기다린다.

### 3. 동시 실행
- API 경로는 CLI 칸(`.runtime.lock`)을 쓰지 않는다. 자기 칸을 따로 두고 최대 2건까지 동시에 돈다.
- 그래서 마감 영상이 CLI 칸을 쓰는 동안에도 메일, Shorts, 성과 분석이 기다리지 않는다.
- 컨테이너 한도(메모리 512m, CPU 0.75)는 HTTP 스트리밍 2건에 충분하다. 빌더가 확인한다.

### 4. API 호출 (`httpx`, 스트리밍)
**요청**: `POST https://api.anthropic.com/v1/messages`, `stream: true`
- 헤더: `x-api-key`, `anthropic-version: 2023-06-01`
- 모델: `request.model`
- effort: `output_config.effort = request.reasoning_effort or "high"`
  - 항상 명시한다. 빠뜨리면 Opus 5.5는 `medium`으로 돈다.
  - 생각(adaptive thinking)은 Opus 5.5에서 늘 켜져 있어 따로 보내지 않는다.
- 메시지: 이미지 첨부와 프롬프트 텍스트를 `stdin_payload`와 같은 순서로 보낸다.
- 프롬프트 캐시(`cache_control`)는 쓰지 않는다.
  - 7일 영수증에서 캐시 읽기는 저장의 6%뿐이었다.
  - 캐시 저장 단가가 입력보다 25% 비싸서, 안 쓰는 쪽이 싸다.

**결과 받는 방식**: 도구 하나로 받는다. API의 엄격 JSON 출력 기능(`output_config.format`)은 쓰지 않는다.
- 엄격 출력은 `minLength`/`maxLength`, 숫자 범위, `minItems` 0·1 밖의 배열 제약을 400으로 거절한다. 우리 계약 스키마는 이런 제약을 쓴다.
- 지금 CLI의 `--json-schema`도 `StructuredOutput`이라는 도구 하나로 결과를 받는다. `parse_result`가 `StructuredOutput` 외의 도구를 거부하는 이유가 이것이다.
- 그래서 API도 같은 방식으로 한다.
  - 도구 정의: `StructuredOutput` 하나, `input_schema = output_schema(request)[0].model_json_schema()`
  - `tool_choice`는 `auto`다. 생각이 켜진 모델은 도구 강제 선택을 받지 않는다.
  - 짧은 시스템 문장: "최종 답은 StructuredOutput 도구를 한 번 불러서 낸다."
- 첫 `tool_use`(`StructuredOutput`)의 입력을 결과로 쓴다. 다른 도구나 텍스트만 온 응답은 `invalid_output`이다.
- 빌더는 계약 스키마가 모두 도구 `input_schema`로 받아들여지는지 실제 호출 없이 확인할 방법을 찾는다. 안 되면 첫 실제 호출에서 확인한다.

**토큰 상한**
- `max_tokens`: Opus 5.5 동기 호출 최대값인 128,000을 영상 계약에 쓴다. 본편 대본 실제 출력은 회당 평균 약 9만 토큰이다(7일 영수증: 24회, 224만).
- 메일, 성과 분석, 브리핑 검토는 계약별로 더 작은 값을 둔다.

**사용량**: `message_start`의 입력·캐시 값과, 마지막 `message_delta`의 누적 `output_tokens`로 계산한다.

**실패 처리** (자동 재시도 없음)
- **응답이 200이 아님** (스트림 시작 전) → "실행 안 됨". API를 다시 부르지 않는다.
  - 400·401·403·413·429·529: 예약을 돌려준다.
  - 429 `enforced_spend_limit_reached`(Console 지출 한도 도달): 다음 달 1일까지 API를 쉰다.
  - 500·504: 실행됐는지 알 수 없다. 예약액을 쓴 것으로 친다.
  - 이후 API 우선 요청은 구독으로 가고, 구독 대체 요청은 지금처럼 기다린다.
  - 구독으로 넘기는 것은 같은 요금을 다시 내는 일이 아니다. 다만 결과는 하나만 쓴다.
- **200 뒤 스트림 안의 `error` 이벤트, 끊김, 시간 초과** → `uncertain`. 다시 실행하지 않는다.
- **검증**
  - `stop_reason`이 `tool_use`(정상)여야 한다. `max_tokens`나 `refusal`이면 `invalid_output`이다.
  - 응답 모델이 `request.model`과 같아야 한다.
  - 도구 입력을 계약 스키마(pydantic)로 검증한다. 실패하면 `invalid_output`이고 이유를 `fault_detail`에 남긴다. 기존 `parse_result` 끝부분과 같은 방식이다.

**크기**
- 요청 크기 상한은 16MB 그대로다(`claude_runtime.py`). API 한도 32MB 안이다.
- 이미지가 20장을 넘으면 장마다 2000px 이하여야 한다. 지금 프레임은 1920×1080이라 맞는다.

### 5. 예산
- **단가표**: 모델별 단가를 코드 상수로 두고, 출처 URL을 주석에 남긴다. 표에 없는 모델은 API로 보내지 않는다.

  | 모델 | 입력 | 출력 | 캐시 저장(5분) | 캐시 읽기 |
  |---|---|---|---|---|
  | `claude-opus-5-5` | $4 | $20 | $5 | $0.20 |
  | `claude-opus-5` | $5 | $25 | $6.25 | $0.50 |

  100만 토큰당 가격이다. 긴 문맥 할증은 없다.
- **장부**: claude-runtime 상태 폴더의 장부 파일 하나다(파일 잠금 사용). 런타임은 DB 자격 증명이 없는 격리 컨테이너라 DB에 쓰지 않는다.
  - 호출 전에 최악 비용으로 예약한다: 입력 크기 추정치 + `max_tokens` 전부를 출력 단가로 계산.
  - 끝나면 실제 사용량으로 정산한다.
  - `uncertain`은 예약액 그대로 쓴 것으로 친다.
- **상한**: 최근 31일 합계가 `CLAUDE_API_CAP_USD`를 넘게 되는 예약은 거절한다(= API 실행 안 함).
  - 31일 이동 창이라 크레딧 갱신일을 몰라도 한 주기에 상한을 넘지 않는다.
  - 기본값은 0(꺼짐)이고 운영값은 190이다.
- **보이기**: 런타임의 기존 인증된 상태 확인 경로에 다음을 더한다. 운영자가 상태 명령으로 볼 수 있으면 된다.
  - 31일 사용액, 상한, 남은 예산
  - 경로별 호출 수

### 6. 키와 설정
- 키 파일
  - Docker secret `claude_api_key`로 claude-runtime에만 붙인다(`/run/secrets/claude_api_key`).
  - 요청 때 파일에서 읽어 헤더에만 쓴다. 환경변수로 두지 않는다.
  - `FORBIDDEN_AUTH` 환경변수 검사와 CLI 환경 화이트리스트는 그대로 둔다. 키가 CLI에 넘어가지 않음을 테스트로 묶는다.
- 호스트 파일은 `/var/lib/quant-company/secrets/claude_api_key`(권한 600)다.
  - compose는 secret 파일이 없으면 서비스를 못 만든다. 그래서 **병합 전에** 사장님이 직접 넣는다.
  - 키를 화면에 보이지 않게 입력받는 한 줄 명령을 드린다. 키는 채팅이나 Slack에 붙이지 않는다.
- 설정은 compose 환경 기본값과 운영 담당이 넣는 `runtime.env` 값으로 나뉜다.
  - `CLAUDE_API_ENABLED`: 기본 `false`, 운영 `true`
  - `CLAUDE_API_CAP_USD`: 기본 `0`, 운영 `190`
  - 끄기는 `CLAUDE_API_ENABLED=false`다. 모든 요청이 지금의 구독 경로로 돌아간다.
- 네트워크: claude-runtime은 이미 `model_egress`에 붙어 있다. CLI도 같은 Anthropic 호스트로 나간다.

### 7. 출시
1. 사장님이 키 파일을 넣는다.
2. PR을 병합하면 CI가 통과한 main이 자동 배포된다. 렌더 재빌드는 필요 없다(`contracts.py`·`pyproject.toml` 불변).
3. 운영 담당이 `runtime.env`에 `CLAUDE_API_ENABLED=true`, `CLAUDE_API_CAP_USD=190`을 넣고 claude-runtime을 다시 띄운다. main SHA와 함께 전달한다.
4. 첫 실제 API 호출(다음 Shorts나 메일)의 영수증을 확인한다.
   - `billing_mode: "api_credit"`, 비용, 모델
   - Console 사용 내역에서 크레딧이 줄었는지
   - 시뮬레이션 테스트와 실제 호출 확인을 따로 적는다.

### 8. 테스트 (가짜 HTTP 전송 `httpx.MockTransport` + 기존 가짜 CLI)
- 경로표: 계약별로 API 우선과 구독 우선이 맞게 갈린다.
- API 성공: SSE로 받은 `StructuredOutput` 도구 입력의 계약 검증, `usage`·`cost_usd`·`billing_mode`, 같은 ID로 다시 물으면 같은 결과.
- 도구를 부르지 않은 응답, `max_tokens`·`refusal` 종료: `invalid_output`.
- 지출 한도 429: 그달 남은 기간 API를 쉬고 구독으로 간다.
- 스트림 전 거절(401·429·529·5xx): API 우선 요청이 구독 경로로 완성된다.
- 스트림 중 끊김: `uncertain`. 다시 물어도 실행하지 않고, 구독으로도 넘기지 않는다.
- 스키마 불일치: `invalid_output`. 다시 물으면 같은 결과다.
- CLI `auth`·`quota`: 구독 우선 요청이 API로 완성된다. CLI `uncertain`·`invalid_output`·`timeout`은 넘기지 않는다.
- 예산
  - 예약이 상한을 넘으면 API를 실행하지 않는다.
  - 정산과 31일 창이 맞게 계산된다.
  - `uncertain`은 예약액으로 친다.
  - 동시 예약 두 건이 함께 상한을 넘지 않는다.
- 동시 실행: CLI 칸이 잡혀 있어도 API 우선 요청은 돈다.
- 키: CLI 실행 환경과 영수증, 오류 문구에 키 문자열이 없다. 키 파일이 없으면 API는 "실행 안 됨"으로 처리된다.

## 9. 검토에서 정한 것 (설계자 판단, 사장님 확인용)

### 9-1. 메일의 "영상에 칸 양보"
- 지금 `NewsletterWriter.yield_to_video`는 영상이 모델을 쓸 차례가 되면 메일 요청을 취소한다. 1분 뒤 새 ID로 다시 묻는다.
- API로 도는 메일을 이렇게 취소하면 낸 돈을 버리고, 같은 메일을 한 번 더 내게 된다. 칸을 나눠 쓰지 않으니 양보할 이유도 없다.
- 그래서 취소 요청에 "공유 칸을 쓸 때만 취소" 옵션을 둔다.
  - 메일의 양보 경로만 이 옵션을 쓴다.
  - API로 도는 요청이면 런타임은 취소하지 않고 `kept`를 돌려준다. 메일은 `kept`면 양보 처리(`yielded`)를 하지 않는다.
  - `providers/client.py`의 cancel 응답 검사에 `kept`를 더한다.
- 대체 글로 바뀐 메일 취소(`cancel_replaced`)는 지금처럼 API 요청도 실제로 끊는다. 결과를 쓰지 않으니 돈을 아끼는 쪽이다.

### 9-2. 결과가 불확실해진 뒤 새 ID로 다시 부를 때
- 영상 쪽은 `uncertain`·`timeout`을 실패한 시도로 보고 새 요청 ID로 다시 부른다(`PLAN_RUNTIME_RETRIES` 등). 메일도 새 ID로 넘어간다.
  - 이것은 호출하는 쪽의 새 결정이다. "같은 요청을 자동으로 다시 실행"하는 것은 아니다.
  - 다만 돈이 걸리므로 런타임이 제동을 건다.
- API에서 `uncertain`이 한 번이라도 나면, **API를 30분 쉰다.** 그동안 API 우선 요청도 구독으로 간다.
- 끊김이 이어져도 30분에 한 번꼴로만 돈이 나가고, 31일 상한이 끝까지 막는다.

### 9-3. 거절된 뒤 언제 다시 API를 쓰나

| 경우 | 정한 동작 |
|---|---|
| 429 속도 제한, 529 과부하 | API 전체를 `retry-after`만큼 쉰다. 값이 없으면 60초. 같은 ID라도 쉼이 끝나면 다시 API를 쓸 수 있다. |
| 500·504 (실행됐는지 모름) | 그 요청 ID는 다시 API로 보내지 않는다(영수증에 기록). 구독 우선 요청이면 지금처럼 기다린다. |
| 401·403 (키 문제) | 운영자가 키 파일을 고치거나 런타임을 다시 띄울 때까지 API를 끈다. 알림을 보낸다. |
| 429 지출 한도 | 다음 달 1일(UTC) 0시까지 API를 끈다. 알림을 보낸다. |
| CLI `quota` 뒤 API도 쉬는 중 | 지금처럼 `deferred`. 다시 물었을 때 API가 쉬는 중이 아니면 바로 API로 간다. |

### 9-4. 상태 보기와 알림
- 런타임이 장부 옆에 상태 파일을 쓴다: `claude/jobs/.api/status.json`. 내용은 켜짐·쉼(이유·풀리는 시각), 31일 사용액, 상한, 최근 호출이다.
  - 기존 `/healthz`는 자격 정보를 읽지 않는 규칙이 있다. 새 HTTP 경로도 만들지 않는다.
- `deploy/ops_probe.py`가 이 파일을 읽는다. claude 로그인 상태를 읽는 것과 같은 방식이다.
  - 운영 상태(`상태`)에 한 줄을 보인다: "Claude API: 31일 $X / $190 · 켜짐".
- watch가 #ops-alerts에 알리는 경우
  - API가 스스로 꺼질 때: 키 문제, 지출 한도, 상한 도달
  - 31일 사용액이 80%를 넘을 때
  - 구독 우선 작업이 API로 대체되기 시작할 때: 로그인 만료·한도라는 뜻이라, 사장님이 다시 로그인하시도록

### 9-5. 상한을 나눠 두지 않는다
- 구독 로그인이 길게 끊기면, 본편 대체가 상한을 먼저 쓸 수 있다. 본편 대체는 하루 약 $25다.
- 그래도 예산을 따로 떼어 두지 않는다. 본편은 절대 멈추지 않는다는 원칙에 맞추고, 대신 9-4 알림으로 빨리 다시 로그인하게 한다.

## 빌더에게 맡기는 것
- 계약별 `max_tokens` 값, 입력 크기 추정 방식
- 장부 파일 형식과 위치(상태 폴더 안)
- 상태 출력 모양
- SSE 파서의 세부 구현
- 단가표 값은 구현 시점에 공식 가격표로 다시 확인한다.

## 확인한 API 사실 (2026-10-23, 공식 문서)
- **effort**: `output_config.effort`이고, Opus 5.5는 `low`·`medium`(기본)·`high`·`xhigh`·`max`를 받는다. 베타 헤더는 필요 없다. https://platform.claude.com/docs/en/build-with-claude/effort
- **생각**: Opus 5.5는 adaptive thinking이 늘 켜져 있다. 생각 토큰은 출력 토큰으로 청구되고 `max_tokens`에 포함된다. https://platform.claude.com/docs/en/build-with-claude/thinking
- **엄격 JSON 출력**: `output_config.format`이다. 길이·범위·`minItems` 2 이상 같은 제약은 400이고, `additionalProperties`는 `false`여야 한다. 그래서 쓰지 않는다. https://platform.claude.com/docs/en/build-with-claude/structured-outputs
- **이미지와 요청 크기**: 이미지 1장 10MB, 요청 전체 32MB다. 20장을 넘으면 장마다 2000px 이하다. https://platform.claude.com/docs/en/build-with-claude/vision
- **스트리밍**: Opus 5.5 동기 출력 최대는 128K 토큰이다. `message_delta`의 usage는 누적값이다. 200 뒤에도 `error` 이벤트가 올 수 있다. https://platform.claude.com/docs/en/build-with-claude/streaming
- **오류**: 429 지출 한도(`enforced_spend_limit_reached`)는 다음 달 1일까지 풀리지 않는다. 429·500·504·529가 청구되는지는 문서에 없다. https://platform.claude.com/docs/en/api/errors
- **가격**: 위 단가표와 같다. https://platform.claude.com/docs/en/about-claude/pricing
- **구독자 크레딧**
  - Max 20x는 월 $200이고, 연결한 Console 조직 하나에 들어간다.
  - 결제 주기가 끝나면 사라지고 이월되지 않는다. Claude Code(구독 로그인) 사용에는 쓰이지 않는다.
  - 크레딧 사용도 조직의 월 지출 한도에 잡히고, 이 한도는 매월 1일에 초기화된다.
  - 잔액을 API로 읽는 방법은 확인하지 못했다.
  - https://platform.claude.com/docs/en/about-claude/api-credits-for-subscribers
