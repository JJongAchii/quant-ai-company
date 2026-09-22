# tech-feed — AI를 사용하지 않는 기술 구독

개발·연구에 유용한 한국어·영어 RSS/Atom을 원문 제목, 링크, 피드에 있는 짧은 설명으로 전달한다.
공식 발표뿐 아니라 실무 기술 블로그와 커뮤니티를 포함해 보안·인프라·데이터·개발 도구의 변화를 다룬다.
수집·선별·발송·댓글에 모델 호출을 사용하지 않는다. 정기 요약, 자동 번역, 본문 크롤링도 없다.
발송 전용 Slack 앱 **Tech Scout**, 기존 회사 서버, PostgreSQL, Temporal 및 발송 영수증을 사용한다.
새 AI/API/피드 구독 비용은 없으며 기존 인프라 사용량은 증가한다.

## 발송 정책

- 전용 Temporal 워크플로가 10분 대기 간격으로 최대 12개 피드를 수집한다. HTTP 처리 시간과 장애에 따라 지연될 수 있다.
- 한국 시간 06:00 이상 24:00 미만에만 자동 발송한다. 밤에는 수집을 계속하며 06시부터 개별 링크를 보낸다.
- 채널 전체에서 실제 HTTP 전송 시도 사이에 최소 60초를 둔다. 재시작 후에도 DB의 발송 간격을 유지한다.
- 처음 성공적으로 연결한 소스의 기존 글은 기준점으로만 등록한다. 이후 처음 본 글 중 최근 72시간 자료만 발송한다.
- 피드 식별자와 정규화한 URL로 중복을 제거한다. 수정된 설명을 재발송하지 않고, 채널에서 이미 게시를 준비한 URL은 다시 준비하지 않는다.
- 미리보기 중 발견한 글은 발송 활성화 후에도 재생하지 않는다. 소스 설정 변경은 그 소스의 새 기준점을 만든다.
- 날짜가 없거나 5분을 넘겨 미래인 글은 발송하지 않는다. 수정 시각만 있는 소스는 `수정`이라고 표시한다.
- 설명은 원문 피드의 HTML을 제거한 최대 160자 발췌다. 설명이 없으면 제목·링크만 보낸다.
- GeekNews 링크는 GeekNews 게시글로 연결한다. 같은 사건의 다른 URL·다른 언어 소개까지 의미상 중복을 제거하는 기능은 없다.
- `@channel`/`@here` 및 링크·이미지 미리보기를 사용하지 않는다. Tech Scout는 이벤트 구독과 대화 기능이
  없는 발송 전용 identity이며, 잘못 연결된 이벤트도 AI 작업을 만들지 않는다.
  다른 직원에게 명시적으로 요청하는 별도 업무는 그 직원의 일반 실행 정책을 따른다.

등록 소스는 `src/quant_company/tech_feed/sources.json`에 있다. OpenAI, Google DeepMind, Hugging Face,
Simon Willison, GitHub Engineering, DuckDB, PostgreSQL 릴리스, Cloudflare, Kubernetes, GeekNews,
NAVER D2, 토스 기술 블로그의 12개다. OpenAI는 기술 관련 분류, NAVER D2는 `/helloworld/` 및
`FE News` 제목만 전달한다. 분야는 소스에 부여한 표시이며 개별 글을 AI로 분류한 결과가 아니다.
원문 소스의 주장·설명을 그대로 소개하는 링크 피드이며 독립적으로 검증한 뉴스라는 표시는 붙이지 않는다.

## 연결

전용 [Tech Scout manifest](../slack-apps/tech_scout.json)로 Slack 앱을 만들고
[프로필 이미지](../slack-apps/avatars/tech_scout.png)를 적용한 뒤 실제 채널에 초대한다.
현재 워크스페이스의 실제 채널명은 `#tech-feeds`이며 채널 ID는 `C0C2KPB76KE`다.
권한은 `chat:write` 하나뿐이다. 이벤트 구독·Socket Mode·App-Level Token·signing secret은 필요 없다.
서버 비밀 저장소의 `tech_scout` 항목에는 `app_id`, `bot_user_id`, `bot_token`만 두며
코드·채팅·모델 컨테이너로 복사하지 않는다. Reporter 자격증명과 hot-news 동작은 그대로 유지한다.
다음 설정을 API·worker·dispatcher·slack-socket에 동일하게 전달한다. Compose 공통 환경에 포함되어 있다.

```dotenv
TECH_FEED_ENABLED=true
TECH_FEED_PUBLISH_ENABLED=false
TECH_FEED_CHANNEL_ID=C_ACTUAL_TECH_FEED_ID
TECH_FEED_OWNER_USER=U_ACTUAL_OWNER_ID
```

패키지와 배포 템플릿의 두 활성화 기본값은 false다. 필요할 경우 `TECH_FEED_SOURCES_FILE`로
같은 스키마의 JSON 파일을 지정한다. 컨테이너에서는 동일한 파일을 모든 회사 프로세스에 읽기 전용
마운트하고 환경 변수에 해당 경로를 지정한다. 기본 소스는 패키지에 포함되어 별도 마운트가 필요 없다.

```bash
uv sync --frozen
uv run --frozen quant-company tech-feed probe --output .local/tech-feed-probe.json
uv run --frozen quant-company migrate
uv run --frozen quant-company tech-feed collect
uv run --frozen quant-company tech-feed status
```

`probe`는 실제 공개 피드와 렌더링 예시만 검사한다. DB·Slack·모델에 연결하지 않는다.
`collect`는 설정한 DB에 기준점·새 글·발송 대기를 기록한다. 기본 소스로부터 실제 피드만 읽는다.
`status`는 실제 발송의 pending/delivered/uncertain/blocked/stale 상태와 소스별 최근 성공·오류를 보여준다.
`uv run --frozen`은 이 독립 작업트리에 존재하지 않을 수 있는 선택적 `../quant-data` 소스의 재해석을 피한다.

미리보기와 실제 채널 접근을 확인한 다음 `TECH_FEED_PUBLISH_ENABLED=true`로 바꾸고 기존 배포 절차로
회사 프로세스를 갱신한다. 상시 실행은 `worker`·`dispatch`가 맡고 별도 서버나 cron은 필요 없다.
수집 workflow ID는 `company-tech-feed-collection-v1`, task queue는 회사 queue의 `-tech-feed` 접미사다.
모델 업무 queue를 사용하지 않는다. Tech Scout는 active model role이나 Socket Mode 연결에 포함되지 않는다.
기존 `hot-news` 설정·소스·Reporter identity는 별도다.

## 검증과 복구

```bash
uv run --frozen pytest -q tests/test_tech_feed.py tests/test_tech_feed_temporal.py
uv run --frozen ruff check .
```

DB 테스트는 기존 pytest 규약에 따라 임시 DB를 생성·삭제할 수 있는 전용 PostgreSQL을 사용한다.
Temporal 검사는 실제 로컬 서버를 시작해 worker 재시작·activity 재시도·history 재생을 확인한다.
Slack은 이 자동 검사에서 모의 HTTP이며 실제 Slack 검사로 해석하지 않는다. AI 자격검사도 실행하지 않는다.

HTTP 429는 같은 메시지 ID로 지연 재시도한다. 타임아웃·서버 5xx·성공 응답의 ts 누락·전송 중 프로세스
종료는 `uncertain`으로 보존하고 자동 재발송하지 않는다. 운영자가 Slack 기록과 저장 영수증을 대조한다.
정확히 한 번 전달을 보장하지 않는다. 소스/권한/정책 변경과 72시간 만료는 전송 직전 다시 검사한다.
중지하려면 두 활성화 설정을 false로 바꾼다. 이미 네트워크로 전송 중인 요청은 취소할 수 없다.
DB 테이블과 발송 영수증은 기존 회사 DB 백업에 포함된다.
