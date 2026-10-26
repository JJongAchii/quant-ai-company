# Temporal을 걷어내고 각 서비스가 자기 루프를 돌린다

## 배경

- 소유자 질문과 결정(2026-10-26):
  > "지금 우리 돌고 있는 것들 다 temporal 에서 workflow등록후에 하고 있는거였어? 이거 비용 만만치 않지 않아....?"
  > "응 지금 당장 진행하고 완료되면 temporal 모두 사용중지하자. 돈 한푼도 안나가게."
- 측정(2026-10-26, 운영 워크플로 이력과 Temporal Cloud 사용량·청구 화면):
  - 항상 도는 워크플로 22개. 하루 약 15.3만 건의 과금 동작이 생겨 월 약 460만 건, 약 $250(10% 지원료 포함)이다.
  - 10월 1~26일 청구 $190.28, 9월 16~30일 $60.84. $150 체험 크레딧은 이미 다 썼다.
  - [최소 서버안](../project/MINIMUM-SERVER.md)은 월 3만~10만 건($4~10)을 가정했다.
  - 모델 계정 명령 대기 루프 하나(2초 간격)가 하루 약 7.9만 건, 절반을 차지한다. 30초 간격 루프 9개가 약 5.7만 건이다.
- 22개 중 21개는 "작업 1번 → N초 쉬기"를 반복한다. 할 일이 없는 확인에도 작업 1건과 타이머 1건이 과금된다.
  하나 남은 대화 처리 워크플로(`CompanyTurnWorkflow`)도 PostgreSQL의 턴 상태를 다시 읽어 맞추는 방식이다.
- 상태와 장애 복구는 이미 PostgreSQL이 맡고 있었다. 각 tick은 고정 요청 ID·영수증·잠금으로 재시작 뒤 이어서 한다.
  dispatcher는 Temporal 없이 1.1초마다 PostgreSQL을 확인해 왔다.

## 결정

- **Temporal을 쓰지 않는다.** [ADR 0009](0009-persistent-quant-ai-company.md) 6항의 "Temporal은 영속 실행"을 대신한다.
  PostgreSQL이 회사 상태와 실행 상태를 함께 가진다. 루프는 언제 다시 tick할지만 정한다.
- **루프는 각 서비스 안에서 돈다** (`quant_company/loops.py`, `quant_company/runtime.py`).
  - 루프마다 예전 activity의 시간 제한, 재시도 횟수(간격 2배 증가), 결과에 따른 대기 시간을 그대로 옮겼다.
  - 시간 제한에 걸린 tick은 취소한다. 워커를 잃었을 때와 같다. 모델 요청은 고정 ID로 다음 tick에서 이어진다.
  - 실패한 tick은 로그만 남기고 루프는 계속 돈다. 결과가 이상해 대기 시간을 계산하지 못하면 60초 쉰다.
  - 대화 처리와 영상 질문은 레인을 잡은 뒤의 실행에만 시간 제한을 둔다. 예전에도 queue에서 기다린 시간은 제한에 들지 않았다.
- **레인 = 예전 task queue.** 같은 queue를 쓰던 루프는 세마포어 하나를 나눠 써서 동시에 돌지 않는다.
  - worker: 대화 처리와 직원 연습(모델 호출 1개).
  - news-worker: 뉴스 편집·뉴스 검색·브리핑 편집·트렌드 편집(모델 호출 1개), 트렌드 수집·발행(2개).
  - video-worker: 영상 분석과 질문 답변(1개).
- **루프 배치.** 루프는 그 기능이 켜져 있을 때만 생긴다. 꺼진 기능은 더 이상 확인 비용도 내지 않는다.

  | 서비스 | 루프 |
  |---|---|
  | worker | 대화 처리, 직원 연습, 모델 계정 명령, 연구 조정, (`TECH_FEED_DEDICATED_WORKER=false`일 때) 테크피드 |
  | news-worker | 뉴스 수집·편집·검색, 브리핑 수집·편집, 트렌드 수집·발행·편집 |
  | briefing-data-worker / data-watch-worker / housing-feed-worker / quant-feed-worker / tech-feed-worker | 각자 하나 또는 둘 |
  | video-worker | 영상, 저녁 메일 작성, 영상 분석, 영상 질문 |
  | maintenance | 개선 작업, 개선 보고 |
  | dispatch | 브리핑 flush, 메일 발송, 운영 감시, Slack 발신 (워크플로 시작은 없어짐) |

  - 예전에는 worker도 데이터 감시·브리핑 데이터 queue를 함께 받았다. 이제 전용 서비스만 돌린다.
  - 계정 게이트웨이도 계정 명령 queue를 함께 받았다. 이제 worker만 돌린다. 깨워야 할 대화 처리 루프가 worker에 있기 때문이다.
- **대화 처리 루프.** 상태가 `queued`·`running`·`waiting`이고 `due_at`이 지난 턴을 우선순위 순서로 하나씩 실행한다.
  - 판단은 그대로 `prepare_turn`이 한다(실행·대기·종료).
  - 루프는 대기(`defer`)와 실패 뒤에 언제 다시 볼지만 메모리에 둔다. 실패는 2초·4초 뒤, 세 번째 뒤에는 60초다.
  - 재시작하면 같은 턴을 다시 고른다. `running` 턴은 고정 요청으로 이어진다.
  - 모델 계정이 바뀌면 계정 루프가 대기 중인 턴을 바로 깨운다. 예전의 Temporal signal을 대신한다.
  - `turns.workflow_started`는 더 이상 쓰지 않는다. 열은 남겨 둔다.
- **영상 질문 루프.** `pending` 질문을 5초마다 본다.
  - 기다려야 하면 5분 뒤 다시 묻는다. 7시간 동안 시도하고도 답하지 못하면 안내하고 닫는다(예전 84회 × 5분).
  - 예전처럼 video worker가 내려가 있던 시간은 세지 않는다. 재시작하면 다시 7시간을 센다.
  - 기능이 꺼져 있으면 아무것도 하지 않는다.
  - `video_questions.workflow_started`는 더 이상 쓰지 않는다.
- **개선봇이 고칠 수 없는 파일.** 예전 `account_workflow.py`·`data_watch/workflow.py`·`staff/workflow.py`의 일정은
  이제 `loops.py`·`runtime.py`에 있다. 그래서 두 파일을 개선봇의 보호 목록(`maintenance/policy.py`)에 넣었다.
- **설정과 비밀.** `TEMPORAL_*` 설정, `temporal_api_key` secret, Temporal 클라우드 점검 스크립트, Temporal workflow를 옮기던
  일회성 도구(`deploy/tech_feed_recovery_release.py`, `scripts/accept_research_server.py`)를 없앴다.
  - CI 단계 이름은 "PostgreSQL and service regression tests"로 바꿨다. 개선봇의 CI 확인도 같은 이름을 본다.
  - `temporalio` 패키지는 `pyproject.toml`·`uv.lock`에 남긴다. 두 파일은 렌더 입력이어서, 바꾸면 렌더 이미지를 새로 만들 때까지
    자동 릴리스가 멈춘다. 다음 렌더 이미지 갱신 때 함께 지운다. 코드는 더 이상 이 패키지를 import하지 않는다.

## 잃는 것과 남는 위험

- **Temporal 웹 화면의 실행 이력이 없다.** 무엇이 일어났는지는 DB 기록·영수증·`ops status`·컨테이너 로그로 본다.
  각 서비스는 시작할 때 자기 루프 목록을 로그로 남긴다.
- **멈춘 작업 감지.** 예전에는 heartbeat 시간 제한이 다른 워커로 다시 보냈다. 이제는 루프의 전체 시간 제한이 대신한다.
  프로세스가 죽으면 Docker가 다시 띄우고, 루프는 DB에서 이어서 한다.
- **스레드에서 도는 동기 tick**(연구 조정)은 시간 제한으로 취소해도 스레드가 끝까지 돈다. Temporal에서도 같았다.
- 서비스 하나에 루프가 하나뿐이라는 전제는 배치(서비스당 컨테이너 1개)와 릴리스(한 서비스씩 교체)가 지킨다.

## 전환 순서 (운영자)

릴리스 동안 이전 커밋으로 되돌릴 수 있게, Temporal 워크플로는 **모든 서비스를 새 커밋으로 바꾼 뒤에** 종료한다.

1. `RELEASE_AUTO_ENABLED=paused`로 자동 릴리스를 멈춘다. 영상 제작·브리핑 발행 시간이 아닐 때 한다.
2. 이 커밋을 `release.py`로 모든 서비스에 반영하고 verify·activate 한다. 교체 중의 겹침은 이렇다.
   - 데이터 감시·브리핑 데이터는 새 전용 서비스의 루프와 예전 worker의 activity가 잠깐 함께 돈다.
     두 tick 모두 PostgreSQL 잠금(advisory lock, `SKIP LOCKED` claim)으로 같은 일을 두 번 하지 않는다.
   - 대화 처리는 새 worker로 바뀐 뒤 새 루프만 실행한다. 예전 dispatcher가 그사이 연 턴 워크플로는 받을 worker가 없고,
     그 턴은 새 루프가 DB에서 처리한다.
3. 서비스마다 `Running loops:` 로그와 첫 tick, `ops status`를 확인한다.
4. 그 뒤 Temporal Cloud의 열린 워크플로를 모두 종료하고 사용량이 0으로 멈췄는지 본다. 자동 릴리스를 다시 켠다.
   - **이 단계부터는 앞으로만 고친다.** 예전 코드는 워크플로를 `REJECT_DUPLICATE`로 시작해서, 종료된 ID를 다시 열지 못하고
     열렸다고 여긴 채 아무 일도 하지 않는다. 그래도 되돌려야 하면 이전 이미지로 되돌리기 전에 각 고정 ID를
     `ALLOW_DUPLICATE_FAILED_ONLY`로 다시 시작해야 한다.
5. 하루 이상 문제없이 돈 것을 확인하고, 소유자 확인을 받아 Temporal Cloud의 API 키를 폐기하고 namespace를 삭제한다.
   - 그 뒤 `runtime.env`의 `TEMPORAL_*` 값과 `secrets/temporal_api_key` 파일을 지운다. 이 커밋부터는 아무도 읽지 않는다.
   - 삭제한 뒤로는 청구가 생기지 않는다. 이미 쓴 사용량(10월분)은 청구된다.
