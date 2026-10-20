# Daily brief 영상 자동화

뭐든story의 시장 브리핑 영상은 **Slack에 발송된 브리핑 본문**(daily-brief edition의 rendered 본문)만으로 만든다.
기사 원문·수집 묶음·초안은 대본 작성기와 검사기에 전달하지 않는다. AM·PM 회차가 커밋·발송되면 서버의 video worker가
대본·장면·모션 렌더·Vincent 내레이션·단어 단위 자막·썸네일·업로드 문안을 만들고, 기본 설정에서는 **완성 파일을
그 회차 brief 스레드로 전달**한다. 소유자가 전체 시청 후 YouTube에 직접 올린다. YouTube API 업로드는 구현돼 있지만
`VIDEO_UPLOAD_ENABLED=false`(기본)로 꺼져 있다(Google OAuth 앱이 Testing·미검증 상태라 업로드가 비공개로 잠긴다).
[daily video 제작 기준](DAILY_VIDEO_PRODUCTION_STANDARD.md)과 [디자인 명세](DAILY_BRIEF_DESIGN_SPEC.md)를 따른다.

```mermaid
flowchart LR
    B[AM·PM brief 커밋] --> S[Slack 발송 · root 수신 확인]
    B -->|본문 없는 대체 공지| K[영상 안 만듦 · 스레드 알림]
    S --> P[본문 줄 번호로 대본·장면 작성 · 검토]
    P --> C[Runway 계정·잔액·월/회차 한도 확인]
    C -->|불가| X[중단 · 스레드 알림 · 유료 전환 없음]
    C --> V[Runway Vincent 장면별 음성]
    V --> R[1080p 모션 렌더 · 자막 · 썸네일 · upload.txt · 기술 검사]
    R -->|VIDEO_UPLOAD_ENABLED=false| F[brief 스레드에 MP4·PNG·SRT·upload.txt 전달]
    R -->|true| Y[YouTube 비공개 업로드 → Slack 공개 승인]
```

## 구현과 현재 운영의 경계

이 경로는 구현했고 기본값은 꺼짐이다(`VIDEO_ENABLED=false`). 운영 서버에 배포·활성화한 증거는 아직 없다.
`briefing_publish_enabled=true`인 실제 발송 회차(AM·PM)가 DB에 커밋되는 같은 트랜잭션에서 영상 작업을 만든다.

| 발송본 (`brief_editions.quality`) | 영상 |
| --- | --- |
| `fallback`이 비어 있는 본문(전체·`일부 확인 중` 축소 포함) | 만든다 (일반 회차, 9~16장면) |
| `fallback`이 있어도 `original_facts`가 있는 본문(분석 대신 수집 사실 목록, briefing PR #132 'always body') | 만든다. `issue_count=0`이면 짧은 회차: `cold_open` → `market_board` → 사실 장면 2~4개 → `calendar` → `signals` |
| `fallback`이 있고 `original_facts`도 없는 공지(수집된 것이 없음) | 만들지 않는다. `video_skips`에 기록하고 root 발송 확인 뒤 스레드에 한 번 알린다 |
| 미리보기·미검토 초안·만료 이후 | 만들지 않는다 |

작업은 brief root 메시지의 Slack 수신(`outbox.status=delivered`, `sent_ts`)이 확인된 뒤에만 시작한다. 그 전에는 모델·음성 비용을
쓰지 않는다. 대본 원천은 본문을 줄 단위로 번호(`b001`…) 매긴 목록이며(`video/body.py`), 장면은 줄 ID를 인용하고 화면·자막·
내레이션·티커·제목·썸네일의 모든 숫자가 인용한 줄 안에 있어야 한다. 본문 출처 목록에서 매체 1곳만 인용한 보도 문장은
그 장면에서 "보도에 따르면"을 말해야 하고, `정규장 종가` 값을 화면에 쓰면 "정규장 종가 기준", 순매수·순매도 수급을 쓰면
"장 마감 기준"을 화면에 표시해야 한다. 화면·내레이션·제목·설명·태그에 제작 도구 문구(AI 분석·Analyst·Claude·Runway 등)는
검사기가 거부한다. 회차 구성은 이번 주 손으로 만든 회차(13장면, 4~5분)와 같은 틀로 9~16장면, `cold_open` → `summary3` →
`market_board` → 핵심 이슈 → 함께 볼 이슈 → `calendar` → `signals`다. 제목 규칙(시리즈 이름은 끝, 앞 20자 숫자·종목, 급락 기준)은
[디자인 명세 §15](DAILY_BRIEF_DESIGN_SPEC.md)를 따른다. 썸네일·장면 이미지는 회차별로 기록하고(`video_asset_usage`)
최근 7일 안에 쓴 이미지는 대본 단계에 제시하지 않는다(그 태그의 모든 이미지가 최근이면 가장 오래전에 쓴 한 장만).

일정은 브리핑 일정(`briefing/schedule.py`)을 그대로 따르므로 휴장일 처리도 같다. 브리핑이 만들어지지 않는 날은
영상도 만들지 않는다.

| 회차 | 원문 발송 | 영상 준비 목표 | 공개 승인 만료 |
| --- | --- | --- | --- |
| 아침 | 07:45 KST(미국 마감 조기 발송 시 더 이르게) | 공개 07:00 KST(미국 서머타임) / 07:50 KST(11/1부터) · 파일은 10분 전(06:50 / 07:40) | 09:00 KST |
| 마감 | KRX 거래일 15:50 수집 시작 · 16:10 기준 · 17:25 마감(`BRIEFING_KR_CLOSE_ENABLED`, 준비되면 조기 발송) | 공개 18:00 KST · 파일은 17:50 | 목표+2시간 30분 (기본 20:20 KST) |

목표 시각까지 영상이 없으면 한 번 상태를 알린다. 늦어지더라도 제작(대본·검토·음성·렌더링)은 중단하지 않고,
완성되면 늦은 시간을 한 줄 적어 Slack 스레드에 전달한다. 만료 시각은 YouTube 공개 승인에만 적용된다.
공개 요청 직전에도 마감과 계정·파일을 확인한다. 목표 시간의 실제 달성은 서버 사양과 실제 음성으로 관찰해야 한다.

## Shorts (소유자 결정 2026-10-19)

`VIDEO_SHORTS_ENABLED=true`이면 본편이 스레드에 전달된 뒤 같은 회차의 세로 Shorts(1080×1920, 40~50초)를 하나 더 만든다.
설계는 [docs/superpowers/specs/2026-10-19-daily-shorts-design.md](superpowers/specs/2026-10-19-daily-shorts-design.md)다.

구성
- 첫 2초 훅: 본편의 제목·썸네일과 같은 숫자나 질문을 바로 말한다. 인사는 하지 않는다.
- 핵심 3개: 큰 숫자 하나와 의미 한 줄씩이다.
- 마무리: 고정 문장 "왜 그런지, 앞으로 볼 신호는 본편에서 확인하세요."

작성과 검사
- 대본은 전달된 본편 대본과 브리핑 본문만 근거로 쓴다(`video/shorts.py`, 계약 `video_shorts_v1`).
- 본편과 같은 결정적 검사를 거친다: 인용한 줄의 숫자만 쓰고, 권유 표현·도구 언급·"보도에 따르면"이 없어야 한다.
- 최대 3번 쓰고, 그래도 통과하지 못하면 그 회차는 건너뛴다.
- 본편 검토에서 남은 지적은 Shorts 작성 지시에 함께 넘겨, 지적된 주장은 쓰지 않게 한다.

본편 우선
- 본편이 제작 중이면 Shorts 단계는 기다린다.
- 다음 본편 작업이 생기면 끝나지 않은 Shorts는 포기하고 스레드에 한 줄 남긴다.
- 본편 다음 회차를 위해 월 크레딧 80을 남길 수 없으면 Shorts는 만들지 않는다.
- `deploy/release.py`는 Shorts가 작업 중일 때(쓰기·음성·렌더 단계에서 lease가 남은 동안) video-worker를 교체하지 않고 기다린다.

렌더와 전달
- 렌더는 작업 서버에서 `shorts_design/` 템플릿으로 한다. Fargate 렌더 이미지에 들어가지 않으므로 Shorts 변경은 렌더 이미지
  교체 없이 배포된다.
- 같은 스레드에 쇼츠 영상(MP4)·자막(SRT)·업로드 문안을 올리고, 업로드가 필요한 메시지라 소유자를 태그한다.
- 제목에는 시리즈 이름("| …오늘의 증시story")을 붙이지 않는다. 그래서 본편 성과 분석에 섞이지 않는다.
- 업로드 뒤 Studio에서 '관련 동영상'을 같은 날 본편으로 지정한다.

## 비용과 계정

대본 작성(`video_plan_v1`)과 별도 검토(`video_review_v1`)는 서버의 기존 Claude 구독 실행기
([Claude runtime](claude-runtime.md), Pro/Max 공식 로그인, `claude-opus-5-5`, 대본 xhigh·검토 max, Claude Code 2.1.280)에서 실행한다. Codex runtime으로
자동 전환하지 않으며 Codex는 영상 계약을 받지 않는다. 실행기는 요청 계약에 맞는 JSON schema만
`--json-schema`로 전달하고 도구·MCP·세션 저장 없이 한 번 실행한다. 직원 독립 검토와 같은 단일 실행 lane과
구독 한도를 공유한다. lane 사용 중(`busy`)이나 구독 한도 소진(`quota`)은 실행 전 거절이므로 호출 예약을
반환하고 같은 요청 ID로 나중에 재시도한다. 회사 일일 호출 제한 집계는 그대로 적용한다.
검색·앱·MCP·셸 도구와 media credentials는 모델에 전달하지 않는다. Claude 계정의 usage credits/extra
usage가 꺼져 있어야 하며(`CLAUDE_USAGE_CREDITS_DISABLED_CONFIRMED=true`) 결제 API 키로 전환하지 않는다.
Runway 음성은 OAuth로 `https://mcp.runwayml.com/mcp`에 연결한다.
[공식 Runway 안내](https://help.runwayml.com/hc/en-us/articles/51931843164691-Connecting-to-Runway-MCP)에
따르면 이 MCP는 웹 계정 크레딧을 사용한다. `dev.runwayml.com/mcp`와 직접 유료 API로 전환하지 않는다.
기존 ChatGPT 앱 연결 토큰이 서버로 자동 이관되지는 않으므로 서버에서 처음 한 번 로그인한다.

음성 모델은 `eleven_v4`(`VIDEO_SPEECH_MODEL`), 기본 voice는 Runway preset `Vincent`로, 손 제작본과 같다.
2026-10-12 서버에서 MCP `generate_speech`의 `tools/list` 스키마를 조회해 `eleven_v3`·`eleven_multilingual_v2`·`eleven_v4`
선택지를 확인했다. `eleven_v4`는 유료 플랜이 필요하고 호출당 최대 2,500자다(장면 내레이션은 최대 600자). 현재 확인한 MCP 조건인
문자 50개당 1 credit을 **장면별 올림**으로 예약한다. 실제 한국어 발음·서비스 조건은 첫 비공개
샘플에서 다시 확인한다. 예를 들어 한 회 3,000자는 최소 약 60 credits이고,
26회면 약 1,560 credits라서 기본 월 제한 1,500을 넘길 수 있다. 수정본도 같은 회차 한도에 합산한다.
기존 다른 영상이 같은 계정 잔액을 쓰므로 생성 직전 잔액과 workspace를 확인한다.
월 한도는 아침·마감 두 회차를 합친 값이다. 회차 한도는 회차(edition)마다 따로 센다. 장면을 내기 전에 남은 장면 전체의
예상 크레딧을 먼저 검사해, 한도를 넘으면 첫 요청도 보내지 않고 작업을 `blocked`(Insufficient narration budget)로 멈춘다.
예상치는 장면(음성 1회 생성)마다 시작한 500자당 1 credit이고, 다시 만들 몫으로 남은 장면 수의 20%를 더해 검사한다.
2026-10-07 Runway 웹(ElevenLabs Eleven v4)에서는 55~210자 장면 20회 생성에 1회당 1 credit이 차감됐다. 19장면 회차면
예상 19+4=23 credits, 하루 두 편·한 달 약 60편이면 약 1,400 credits라 기본 월 한도 1,500 안에 든다. 한도 검사 자체(월 1,500·회차 100)는 그대로다.
13장면 회차(4~5분, 장면당 500자 미만)는 13+3=16 credits로 검사하고 실제 약 13~17 credits를 쓴다.
잔액이 부족하면 `runway_balance`, 월/회차 한도면 `credit_cap`, Runway 연결·로그인 실패면 `runway_unavailable`로 첫 음성 요청 전에
멈추고 brief 스레드에 사유·이번 회차/월 사용량을 한 번 알린다. 구매·충전 도구와 자동 유료 API fallback은 없다.
원화 5만원은 추가 지출 목표이며, 서버 증설과 별도 구독 구매는 이 코드가 실행하지 않는다.

화면은 [아침 시장 브리핑 디자인 명세](DAILY_BRIEF_DESIGN_SPEC.md)의 고정 틀(`VIDEO_TEMPLATE=motion-v2`, 기본값)로 만든다.
Claude는 `video_episode_v1` 계약으로 정해진 카드 종류와 필드만 채운다(`video/episode.py`). 화면 배치·색·모션은
패키지에 포함된 템플릿(`video/design/`, Pretendard OFL 글꼴 포함)이 정한다. 검사기는 화면·티커·자막·내레이션의
모든 숫자가 해당 장면이 인용한 동결 원문 주장 안에 있는지(부호는 ▲▼로 표시하므로 절댓값 비교), 카드 종류·아이콘·
지도 지역·이미지 ID가 허용 목록 안에 있는지 확인한다. `motion.py`는 실제 음성 단어 타임스탬프로 자막을 맞추고,
장면 최종 상태에서 글자 겹침·카드 넘침·빈 공간·자막 3줄을 DOM으로 검사한 뒤 `seek(t)`로 30fps 프레임을 찍어
FFmpeg로 합성한다. 검사 실패, true peak -1.5 dBTP 초과, 검은 화면·긴 무음이면 결과를 만들지 않는다.
이 템플릿 이전에 만든 작업은 정책에 기록된 `text-v1`로 그대로 렌더한다.

이미지는 서버에서 매일 찾지 않는다. 사람이 검수한 라이브러리(`${STATE_DIR}/video-assets`, 읽기 전용 mount)의
`manifest.json`에 있는 ID만 쓸 수 있다. 자료사진은 CC0·퍼블릭 도메인·CC BY만 쓰고 화면 칩(자료사진 · 저작자 ·
라이선스)과 업로드 설명란 출처를 렌더러가 manifest에서 자동으로 만든다. 일러스트는 Codex 구독으로 만든 비사진풍
그림이며 화면에 칩을 달지 않는다(DAILY_BRIEF_DESIGN_SPEC v1.1-screen). 라이브러리 원본은 iCloud `뭐든story/아침브리핑/library/`(images·photos·manifest.json)에
있고 서버 반영은 사람이 복사한다. 음악은 출처가 정해질 때까지 넣지 않는다.
렌더링 시도마다 별도 디렉터리를 쓰므로 중단된 렌더가 승인한 파일을 덮어쓰지 않는다.
DB와 media-auth는 기존 복구 정책에 포함하고, 업로드 영상·manifest·영수증을 함께 백업한다.
렌더와 검사가 끝나면 중간 파일(프레임 조각, 무음 영상, 음성 WAV, 화면 폴더, 회차당 약 0.2GB)을 바로 지운다.
최종 MP4·자막·문안·manifest는 작업이 끝난(공개·만료·보류·대체·차단) 뒤 `VIDEO_RETENTION_DAYS`(기본 14일)가 지나면 지운다.
DB의 계획·영수증·파일 해시는 남는다. 영상 볼륨 여유가 `VIDEO_MIN_FREE_GB`(기본 10GB)보다 적으면 대기 중인 회차를
시작하지 않고 `blocked`(insufficient_disk)로 두며 brief 스레드에 한 번 알린다. 크레딧은 쓰지 않는다.
고정 댓글 문안은 업로드 패키지에 제공하며 댓글 자동 게시·고정은 이 버전에 포함하지 않았다.

## 렌더링 위치: ECS Fargate (소유자 결정 2026-10-12)

2 vCPU 서버에서 8.7분 영상 렌더에 60~70분이 걸려, 상시 서비스(DB·Slack·worker·Claude 실행기)는 서버에 두고
렌더링만 렌더할 때만 존재하는 ECS Fargate 태스크(16 vCPU/32GB, Spot 우선)로 보낸다(`VIDEO_RENDER_BACKEND=remote`).
리소스는 [deploy/render.json](../deploy/render.json) CloudFormation 스택 `quant-company-render`(ap-northeast-2), 이미지는
[deploy/Dockerfile.render](../deploy/Dockerfile.render)다.

1. worker는 렌더 디렉터리 키(계획·음성 take·템플릿·fps의 digest)마다 입력(job·plan·음성·이미지 라이브러리)을
   `s3://<bucket>/render/<job>/<key>/in/`에 올리고 `ready.json`을 마지막에 쓴다. 같은 키는 다시 올리지 않는다.
2. 시도 기록(`video_jobs.qa.render`)을 먼저 저장한 뒤 `RunTask`를 부른다. client token은 job·키·시도 번호다. 응답을
   못 받은 호출(연결 끊김·시간 초과)은 같은 token으로 다시 물어 태스크가 두 개 뜨지 않는다. AWS가 거절하면(용량 부족·권한)
   그 시도는 실패다. 1·2번째 시도는 FARGATE_SPOT, 마지막 시도는 FARGATE다.
3. 태스크(`quant-company video render-task --prefix s3://…`)는 서버와 같은 `MotionRenderer`로 렌더한다. Whisper 모델은
   `models/small/`에서 받는다. 끝난 장면 조각·정렬·배치·검수 프레임은 15초마다, Spot 회수·StopTask(SIGTERM) 때는 즉시
   `out/`에 올린다. 다음 태스크는 그 조각부터 이어서 렌더한다. `manifest.json`을 마지막에 올리는 것이 완료 표시다.
4. worker는 tick마다 상태만 묻는다(60초 간격). 끝나면 manifest의 파일과 검수 프레임만 받아 체크섬을 검증하고 화면 검수로
   넘긴다. 상태 조회 실패는 기다리고(10회 연속이면 그 시도를 포기), 태스크는 90분(`VIDEO_RENDER_TIMEOUT_MINUTES`)이 지나면
   스스로 끝난다. worker도 100분이 지나면 StopTask한다.
5. 3번 실패하면 그 회차는 서버에서 렌더한다(`qa.render_local`). 영상은 항상 만들어진다.

권한: 태스크 역할은 `render/*` 읽기·쓰기와 `models/*` 읽기만 있다. DB·Slack·Runway·Claude 인증은 없다.
worker 사용자(`quant-company-render-worker`)는 이 태스크 정의의 RunTask·DescribeTasks·StopTask, 두 역할의 PassRole,
`render/*`만 쓸 수 있다. 접근 키는 서버 secret `render_aws_credentials`(root:10001, 0440)로 video-worker에만 mount한다.
보안 그룹은 inbound가 없고 HTTPS outbound만 연다. 기본 VPC의 public subnet과 public IP를 써서 NAT 비용이 없다.

비용(서울, 16 vCPU/32GB 온디맨드 $0.908/시간): 2026-10-12 검증에서 4분 52초 영상 렌더 태스크가 약 280초 과금됐다
(온디맨드 기준 약 $0.07, Spot은 그보다 낮다). 하루 2회면 월 약 $5 이하다. `render/`는 3일 뒤 자동 삭제, 로그는 7일,
ECR은 최근 5개 이미지만 남긴다. AWS Budgets `quant-company-render-monthly`가 ECS+ECR 비용 $20/월 기준으로 알린다.

이미지 갱신: `docker build --provenance=false -f deploy/Dockerfile.render .`(linux/amd64) → ECR에 새 태그로 push →
digest로 스택의 `ImageUri`를 바꾸면 새 태스크 정의 revision이 생긴다(이전 revision은 남는다) → worker의
`VIDEO_RENDER_TASK_DEFINITION`을 그 revision으로 바꾼다. worker와 렌더 이미지는 같은 소스 버전으로 만든다.

## 서버 준비

`deploy/video.compose.yaml`은 선택 프로필이다. media worker만 OAuth 파일·영상 파일에 접근하고,
Slack 토큰은 기존 API/socket/dispatcher에만 있다.
[영상 설정 예제](../deploy/video.env.example)는 기존 private runtime.env에 필요한 값만 추가할 때 사용한다.
DB와 Temporal을 기존 서비스와 공유하며
media worker의 작업 큐는 `${TEMPORAL_TASK_QUEUE}-video`다. 모든 프로세스가 같은 버전의
코드·영상 플래그·brief owner/channel 설정을 사용해야 한다.

먼저 기존 app 이미지를 만들고 video 이미지를 만든다. 아래 명령은 템플릿이며 운영 서버에서
실행한 증거가 아니다. 기존 설치의 추가 overlay와 release 고정값을 유지한다.

```sh
docker compose -f deploy/compose.yaml build api
docker compose -f deploy/compose.yaml -f deploy/video.compose.yaml --profile video --profile claude build video-worker
```

video worker는 `claude-runtime`이 healthy일 때 시작한다. 항상 `--profile video --profile claude`를 함께 쓴다.
Claude 실행기 로그인과 usage credits 확인은 [Claude runtime 최초 연결](claude-runtime.md)을 따른다.
영상만 켤 때 `COMPANY_STAFF_REVIEW_ENABLED`를 켤 필요는 없다.

호스트의 `${STATE_DIR}/video`, `media-auth`, `video-models`, `video-assets`를 uid/gid 10001 소유로 먼저 만든다.
`media-auth`는 0700, 토큰/YouTube client 파일은 0600이다. 파일을 Git에 넣지 않는다.
video 이미지에 Chromium, FFmpeg, 한국어 Noto 폰트와 영상 optional dependencies가 들어 있다.
Whisper small은 `/state/models`에 캐시한다. 첫 다운로드와 최초 렌더링을 미리 끝낸다.
기본 worker 한도는 1.5 CPU/1536MiB다. 2GB 서버에는 켜지 않는다. 8GB(권장)·4GB 메모리 설정은
[8GB 예시](../deploy/lightsail-8gb.env.example)·[4GB 예시](../deploy/lightsail-4gb.env.example), 서버 이전은
[서버 요금제 변경 안내](server-migration.md)를 따른다. 렌더 속도는 `quant-company video bench`로 잰다. 추가 서버 구매·사양 변경을 자동으로 수행하지 않는다.

Google Cloud에서 해당 채널용 YouTube Data API v3와 Desktop OAuth client를 준비하고,
`media-auth/youtube-client.json`에 둔다. OAuth scope는 업로드와 `youtube.force-ssl`이다.
OAuth testing 상태의 refresh token 만료와 브랜드 계정 선택을 확인한다.
[YouTube 공식 문서](https://developers.google.com/youtube/v3/docs/videos/insert)는 미검증 API 프로젝트의
업로드가 비공개로 제한될 수 있다고 명시한다. 공개 기능은 필요한 심사와 채널 권한 확인 뒤 켠다.

로컬 또는 서버의 SSH 터널을 통해 로그인한다.

```sh
# 노트북에서: 로그인 동안만 유지
ssh -L 8766:127.0.0.1:8766 -L 8767:127.0.0.1:8767 SERVER
# 서버의 video worker가 실행된 뒤: container listener, 호스트 포트는 loopback에만 노출
docker compose -f deploy/compose.yaml -f deploy/video.compose.yaml exec video-worker \
  quant-company video-auth runway --listen-host 0.0.0.0
docker compose -f deploy/compose.yaml -f deploy/video.compose.yaml exec video-worker \
  quant-company video-auth youtube --listen-host 0.0.0.0
```

처음에는 플래그를 모두 false로 두고 worker를 실행할 수 있다. 로그인은 생성·업로드·공개를 하지 않는다.
출력된 authorization URL을 노트북 브라우저에서 열고 완료한다. 기존 ChatGPT 로그인이나
브라우저 쿠키를 복사하지 않는다. 서버·터미널 인증 출력은 공개 증거에 남기지 않는다.

| 설정 | 기본값 | 의미 |
| --- | --- | --- |
| `VIDEO_ENABLED` | false | 발송된 AM·PM 본문에서 영상 제작 허용; brief 발송도 허용되어야 함 |
| `VIDEO_UPLOAD_ENABLED` | false | false: 완성 파일을 brief 스레드로 전달(소유자 직접 업로드). true: YouTube 비공개 업로드 + Slack 공개 승인. 작업 생성 시점 값이 그 회차에 고정됨 |
| `VIDEO_PUBLISH_ENABLED` | false | 사람의 유효한 Slack 승인 후 공개 허용 |
| `VIDEO_RUNWAY_WORKSPACE_ID` | 0 | 명시적 기존 웹 workspace |
| `VIDEO_YOUTUBE_CHANNEL_ID` | 빈 값 | 명시적 해당 채널 ID |
| `VIDEO_MONTHLY_CREDIT_LIMIT` | 1500 | 예약/불확실 요청을 포함한 KST 월 한도 |
| `VIDEO_EPISODE_CREDIT_LIMIT` | 100 | 수정본을 포함한 회차 한도 |
| `VIDEO_MODEL` | claude-opus-5-5 | Claude 구독 대본·검토 모델(`claude-opus-5` 또는 `claude-opus-5-5`만 허용). 새 작업의 policy에 고정 |
| `VIDEO_REASONING_EFFORT` | xhigh | 대본 작성·화면 카드 수정의 추론 강도(high/xhigh/max). 새 작업의 policy에 고정 |
| `VIDEO_REVIEW_EFFORT` | max | 대본 검토·최종 화면 검수의 추론 강도. 새 작업의 policy에 고정(이전 작업은 단일 강도 유지) |
| `VIDEO_MODEL_RUNTIME_URL` | http://claude-runtime:8080 | 기존 Claude 실행기 내부 주소 |
| `VIDEO_VOICE` | Vincent | Runway preset voice; 첫 실제 샘플에서 한국어 발음 확인 |
| `VIDEO_ALIGNMENT_MODEL` | small | 실제 음성 검증·자막 시점용 로컬 모델/경로 |
| `VIDEO_TEMPLATE` | motion-v2 | 카드·모션 고정 틀. text-v1은 이전 글 화면 |
| `VIDEO_RENDER_WORKERS` | 2 | 프레임 캡처 프로세스 수. 8GB 서버 2(아침 06:50 목표), 작은 서버 1 |
| `VIDEO_AM_PUBLISH_DST` / `VIDEO_AM_PUBLISH_STD` / `VIDEO_PM_PUBLISH` | 07:00 / 07:50 / 18:00 | 유튜브 공개 목표(KST). 아침은 뉴욕 서머타임 여부로 고른다 |
| `VIDEO_PUBLISH_LEAD_MINUTES` | 10 | 공개 몇 분 전까지 파일을 스레드에 올릴지. 작업은 본문 발송 즉시 시작하고, 늦어도 완성되면 바로 올리며 늦은 시간을 한 줄 적는다. 지연돼도 제작은 계속한다 |
| `VIDEO_PLAYLIST_URL` | 증시story 재생목록 | 설명란 "증시story 모아보기" 주소 |
| `VIDEO_RETENTION_DAYS` | 14 | 작업 종료 뒤 최종 파일 보관 일수 |
| `VIDEO_MIN_FREE_GB` | 10 | 이보다 여유가 적으면 새 회차를 시작하지 않음 |
| `VIDEO_ANALYTICS_ENABLED` | false | 공개 업로드의 성과 분석([성과 분석](#성과-분석)). YouTube 채널 ID·브리핑 소유자·전용 성과 채널이 필요 |
| `VIDEO_ANALYTICS_CHANNEL_ID` | 빈 값 | 성과 카드·주간 리뷰를 받을 전용 Slack 채널(`SLACK_ALLOWED_CHANNELS`에 포함, 브리핑 채널과 다름) |
| `VIDEO_ANALYTICS_MAX_LESSONS` | 8 | 동시에 제작에 반영되는 채택 교훈 수 상한 |

활성화 순서는 upstream full brief 품질 통과 → 계정/잔액/채널과 서버 자원 확인 →
첫 비공개 샘플 전체 시청 → 비공개 자동 제작 → 공개 승인 허용이다. 기존 Analyst Slack 앱의
interactivity를 켜고, HTTP 방식이면 `/slack/events/market_brief`로 설정한다.
`quant-company slack-manifests --include-market-brief ...`는 영상이 활성화된 설정에서
해당 interactivity를 포함한다. Socket Mode는 기존 인증 연결로 상호작용을 받는다.

## Slack 파일 전달 (기본)

렌더와 기술 검사가 끝나면 worker는 `video_files` 메시지를 만들고 작업을 `delivering`으로 둔다. Slack bot token은 dispatcher에만
있으므로 전달은 dispatcher가 한다(영상 볼륨은 dispatcher에 읽기 전용으로 mount, media-auth는 mount하지 않음). manifest의 파일
해시를 다시 확인한 뒤 Slack 외부 업로드 API를 쓴다.

1. 파일마다 `files.getUploadURLExternal`(filename, length) → 받은 `file_id`를 `video_deliveries.receipt`에 먼저 기록
2. 받은 URL로 파일 바이트를 스트리밍 POST(영상 전체를 메모리에 올리지 않음)
3. `files.completeUploadExternal`(files, `channel_id`, brief root의 `thread_ts`, 안내 문구)

전달 파일은 `{아침|마감}브리핑_YYYYMMDD.mp4`, `_썸네일.png`, `.srt`, `_업로드문안.txt`(제목·설명·챕터·태그·고정 댓글)다.
3단계 전에는 Slack에 아무것도 공유되지 않으므로 그 전의 거절·전송 실패는 확정 실패(`delivery_failed`)다. 이때 스레드에 실패 사유와
서버 보관 경로(`/state/video/<job>/<attempt>`, 호스트 `${STATE_DIR}/video/...`)를 알린다. 3단계 응답을 잃으면 공유 여부를 알 수
없으므로 `delivery_uncertain`으로 두고 같은 안내를 하되 다시 올리지 않는다. 429는 공유 전이므로 나중에 처음부터 다시 시도한다.
dispatcher가 업로드 중 죽어 outbox가 `uncertain`이 되면 다음 worker tick이 작업도 `delivery_uncertain`으로 바꾸고 알린다.
정확히 한 번 전달을 보장하지 않는다. Analyst 앱에 `files:write` scope가 필요하다(`slack-apps/market_brief.json`).

## 운영자 runbook (서버)

아래는 절차이며 운영 서버에서 실행한 증거가 아니다. 비밀 값은 runtime.env·secret 파일이 참조하며 Git·로그에 복사하지 않는다.

**1. 사전 준비(한 번)**
- 8GB 호스트 한도: [lightsail-8gb.env.example](../deploy/lightsail-8gb.env.example)의 `VIDEO_MEMORY_LIMIT=2048m`, `VIDEO_CPUS=2`,
  `VIDEO_RENDER_WORKERS=2`. 다른 서비스 한도 합계는 그 파일의 6,592MiB 계산을 그대로 따른다.
- 호스트 디렉터리(uid/gid 10001): `${STATE_DIR}/video`, `media-auth`(0700), `video-models`, `video-assets`(검수 라이브러리 복사).
- Slack: Analyst 앱 설정에서 bot scope `files:write`를 추가하고 워크스페이스에 **재설치**한다(소유자만 가능). Analyst 봇이 brief 채널에
  들어 있어야 한다(`not_in_channel` 방지). 새 bot token이 발급되면 slack credentials secret 파일만 갱신한다.
- runtime.env에 [video.env.example](../deploy/video.env.example) 값 추가: 처음에는 `VIDEO_ENABLED=false`,
  `VIDEO_UPLOAD_ENABLED=false`, `VIDEO_PUBLISH_ENABLED=false`, `VIDEO_RUNWAY_WORKSPACE_ID=<기존 웹 workspace ID>`.

**2. Runway 최초 로그인(한 번, 소유자)**

```sh
ssh -L 8766:127.0.0.1:8766 SERVER          # 노트북, 로그인 동안 유지
docker compose -f deploy/compose.yaml -f deploy/video.compose.yaml --profile video --profile claude up -d video-worker
docker compose -f deploy/compose.yaml -f deploy/video.compose.yaml exec video-worker \
  quant-company video-auth runway --listen-host 0.0.0.0
```

출력된 URL을 노트북 브라우저에서 열어 기존 Runway 계정으로 승인한다. 토큰은 `media-auth/runway-*.json`(0600)에만 저장된다.
만료·폐기되면 같은 명령으로 다시 로그인한다(이때 작업은 `runway_unavailable`로 멈춰 있고 스레드 알림이 온다).

**3. YouTube 자격 증명(나중에 켤 때만)**
소유자 노트북의 `~/.config/quant-company/video/youtube-client.json`·`youtube-tokens.json`에 해당하는 파일은 서버의
`${STATE_DIR}/media-auth/youtube-client.json`, `${STATE_DIR}/media-auth/youtube-tokens.json`(uid 10001, 0600)에 둔다.
내용을 출력·커밋하지 않는다. 또는 client 파일만 두고 `quant-company video-auth youtube --listen-host 0.0.0.0`(포트 8767 터널)으로
서버에서 새로 로그인한다. OAuth 앱이 Testing·미검증이면 업로드는 비공개로 잠기므로 `VIDEO_UPLOAD_ENABLED`는 false로 둔다.

**4. 켜기**

```sh
# runtime.env: VIDEO_ENABLED=true (VIDEO_UPLOAD_ENABLED=false 유지)
docker compose -f deploy/compose.yaml -f deploy/video.compose.yaml --profile video --profile claude up -d \
  api worker dispatch slack-socket news-worker video-worker
```

모든 프로세스가 같은 플래그를 써야 한다(dispatcher가 전달 게이트를 같은 설정으로 검사). 끄기는 `VIDEO_ENABLED=false` 후 같은
명령. 끄면 대기 작업은 시작하지 않고, 전달 대기 중인 파일 메시지는 `stale`로 남으며 파일은 서버에 보관된다.

**5. 첫 실행 확인**
- brief가 발송된 뒤 `docker compose ... exec video-worker quant-company video status`에서 그 회차 작업이
  `queued → reviewing → synthesizing → rendering → delivering → delivered`로 가는지 본다. `skips`에는 대체 공지 회차만 있어야 한다.
- brief 스레드에 4개 파일과 "영상 준비 완료" 안내가 왔는지, 파일을 받아 전체 시청(숫자·발음·자막·썸네일·upload.txt 제목/설명)한다.
- `effects`의 speech credits 합계가 Runway 웹 사용 내역과 같은지(약 13~17) 확인한다.
- 실패 시 스레드 알림의 사유를 보고 처리한 뒤 `quant-company video retry --job-id ... --note '...'`.
  재시도 가능한 경우: 렌더 실패, `runway_unavailable`/`runway_account`(음성 요청 전), `delivery_failed`(새 전달 시도).
  `uncertain`/`delivery_uncertain`은 Runway 생성 이력·Slack 스레드를 직접 확인하고 `video reconcile`로만 정리한다.

**6. 되돌리기**
`VIDEO_ENABLED=false`로 바꾸고 위 up 명령을 다시 실행한다(브리핑에는 영향 없음). 코드 롤백은 이전 `RELEASE_COMMIT` 이미지로
같은 명령을 실행한다. 추가된 테이블·열(`video_deliveries`, `video_skips`, `video_asset_usage`, `video_jobs.delivery_message_id`)은
이전 코드가 무시하므로 지우지 않는다. 남은 파일은 `VIDEO_RETENTION_DAYS` 뒤 정리된다.

## 상태와 복구

```sh
uv run --extra video quant-company video status
# 수동 점검: 한 단계만 실행. 실제 설정에서는 크레딧/쓰기가 발생할 수 있음
uv run --extra video quant-company video tick
uv run --extra video quant-company video-worker
```

운영 API `GET /v1/videos`는 operator 인증을 요구한다. 상태와 공개용 영상 ID만 보여 주며
토큰·signed media URL·resumable session은 출력하지 않는다. Temporal은 실행·타이머를,
PostgreSQL은 버전·예산 예약·task ID·승인·효과 영수증을 보존한다.

진행 중인 효과에 결과가 없으면 `uncertain`이다. Runway history의 기존 task를 확인하고
YouTube의 기존 session/video ID를 조회해 원래 요청 결과와 대조한다. 확인 전에는 작업/효과 행을
삭제하거나 새 음성·업로드 요청을 보내지 않는다. 알려진 upload session은
[공식 resumable protocol](https://developers.google.com/youtube/v3/guides/using_resumable_upload_protocol)에
따라 현재 offset을 조회해 이어 올린다. session이 만료되면 자동 대체 업로드를 만들지 않는다.
공개 요청 응답을 잃으면 실제 visibility를 확인하기 전까지 공개 완료라고 보고하지 않는다.
검토 메시지 전송이 불확실하면 기존 Slack client message ID/수신 결과를 대조한다.
복구 전에 DB·영상 파일과 해당 receipt를 보존하고 운영자 reconciliation 기록을 남긴다.

원래 요청의 확인된 receipt를 운영자가 비공개 JSON 파일에 작성하고 아래처럼 대사한다.
이 명령은 외부 생성/쓰기를 하지 않으며 원래 `running` 요청에 결과를 연결하고 기록을 남긴다.
`speech-N`은 원래 `taskId`, `upload-session`은 원래 `session`, `captions`는 `caption_id`,
`thumbnail`은 `video_id`와 `thumbnail_set=true`, `publish`는 기존 `video_id`와 확인한 `privacy=public`을
요구한다. 모델 receipt는 해당 `request_id`, typed `output`을 포함한다. 로컬 단말에 비밀 receipt를 출력하지 않는다.

```sh
quant-company video reconcile --job-id JOB_UUID --effect-key speech-0 \
  --receipt-file /private/verified-receipt.json --note '원래 요청의 task ID와 계정 생성 이력을 대조함'
quant-company video retry --job-id JOB_UUID --note '기존 음성 task와 로컬 렌더 환경을 확인하고 복구함'
```

`retry`는 미확인 요청이 없는 로컬 렌더링 또는 기존 upload session만 재개한다.
크레딧 요청이나 새 업로드를 자동 재전송하는 복구 명령은 없다. Claude 실행기의 `busy`와 `quota`는 실행 전에
반환되는 명시적 거절이므로 모델 호출 예약을 반환하고 같은 요청 ID로 재시도한다(`busy` 최대 60초,
`quota` 최대 15분 뒤). 대기 중에도 마감으로 제작을 끝내지 않는다. 응답 대기 시간이 지나면 같은 요청 ID로 다시 묻는다.
영상 대본·검토 요청의 실행기 프로세스 한도는 2시간, 출력 스트림 한도는 32MiB다(직원 검토는 300초·1MiB). worker는 한 번에 120초까지 기다린다. 서버 렌더링(fallback)을 위해 한 번의 tick은 3시간, 작업 lease는 200분이다. Fargate 렌더는 tick마다 상태만 묻는다. 직원 검토는 계속 `claude-opus-5`를 쓴다.
실행기의 `claude/jobs/video-<job>-<phase>.json` receipt와 DB 효과 행을 먼저 대사한다.

### 검사와 수정 (소유자 결정 2026-10-12: 검사 결과로 제작을 멈추지 않는다)

Claude 세션에서 편집자가 하듯 서버도 쓰고 → 검사하고 → 고치고 → 다시 검토한 뒤 반드시 완성본을 전달한다.

1. 대본은 시도마다 새 요청 ID(`plan`, `plan-2`…)로 작성한다. 출력 계약 위반(실행기가 규칙·위치만 기록)이나 코드 검사
   지적(`episode_violations`가 모든 지적을 한 번에 수집)은 다음 시도의 수정 지시가 된다. 최대 6회 뒤에는 지적이 가장
   적은 버전으로 계속 제작하고 남은 지적을 전달문에 적는다.
2. Claude 검토(`review`, `review-2`)의 지적은 대본 수정 지시로 돌아가 한 번 더 고친 뒤 다시 검토한다. 검토가 끝까지
   통과하지 못해도 음성·렌더링으로 진행하고 지적을 전달문에 적는다.
3. 렌더링의 글자 넘침·자막 3줄·썸네일 넘침·음량/무음/검은 화면, 업로드 문안 규칙은 지적으로 기록하고 파일은 만든다.
   파일 손상(디코딩 실패·길이/해상도 불일치)과 템플릿 오류만 실제 실패다.
4. 모델 호출은 비동기다. worker는 최대 120초 기다린 뒤 같은 요청 ID로 다시 묻는다. 실행기는 끝난 결과를 영수증에서
   돌려주거나(`complete`), 아직 실행 중이면 `pending`, 확정 실패면 같은 코드와 규칙을 다시 돌려준다. 같은 요청 ID로
   추론이 두 번 실행되지 않는다. 시도·지적·검토 기록은 `video_jobs.qa`에 남는다.

보류는 `held`, 새 수정본은 이전 버전을
`superseded`로 만든다. 이전 버전의 버튼은 새 버전을 승인하지 못한다. 09:00 이후에는
최신성 재검토를 거친 별도 발간으로 다뤄야 하며 일반 승인 버튼으로 연장하지 않는다.
자동 기술 QA는 수치 의미와 전체 청취를 보증하지 않는다. 소유자의 전체 시청이 최종 조건이다.

## 성과 분석

소유자가 올린 공개 영상의 성과를 읽어 진단하고, 소유자가 채택한 교훈만 다음 회차 대본 작성에 반영한다
([ADR 0043](adr/0043-video-performance-analytics.md)). 기본값은 꺼짐이다. 운영에서는 2026-10-15 11:43 KST에 켰고,
첫 카드 3건이 11:51~11:53에 성과 채널에 올라갔다
([실연결 기록](project/evidence/video-analytics-20261014/verification.json)).

```mermaid
flowchart LR
    U[채널 공개 업로드] -->|제목의 회차·같은 제목·수동 link| P[video_publications]
    R[Reporting API 노출·CTR 일보고서] --> D[video_reach_daily]
    A[Analytics API 조회·이탈 곡선·유입·검색어] --> S[D+3·D+7·D+28 스냅샷]
    D --> S
    S -->|제작 중인 회차 없을 때| C[Claude 진단 → 성과 채널 카드 · 바꿔볼 것]
    C -->|Slack 수신 뒤| T[스레드: 자세한 진단]
    S --> W[월요일 09:00 주간 리뷰 · 교훈 제안 버튼]
    Q[소유자의 스레드 답글] -->|질문마다 워크플로| AN[Claude 답변 · 지시는 교훈 제안 버튼]
    W -->|소유자 채택| L[활성 교훈]
    AN -->|소유자 채택| L
    L -->|새 작업 policy에 고정| V[다음 회차 대본 지시]
```

- 수집: video worker 프로세스 안의 별도 Temporal 큐 `${TEMPORAL_TASK_QUEUE}-video-analytics`가 30분마다 돈다.
  YouTube 토큰은 이 컨테이너(media-auth)에만 있다. 외부 쓰기는 Reporting API 작업 생성뿐이며, 보고서 종류마다
  한 번 만들고 영수증(`video_report_jobs`)을 남긴다. 나머지는 모두 읽기다.
- 측정 대상: 제목에 `오늘의 증시story`가 들어간 업로드다. 채널의 다른 시리즈(`[역사story]` 등)는 업로드 목록에만
  남고 측정·카드·리뷰에서 빠진다. 시리즈 이름을 지우고 올린 회차는 `link`로 작업에 연결하면 측정한다. 회차는 지금
  형식(`… | 10월 13일 마감 · 오늘의 증시story`)과 10월 12일까지 쓰던 형식(`[오늘의 증시story] … | 10월 8일 마감 브리핑`)을
  모두 읽는다.
- 노출수·CTR은 Reporting API에만 있다. 작업을 만든 뒤 약 이틀 안에 과거 30일 보고서가 채워지고, 매일 약 2일
  늦게 새 보고서가 나온다. 과거분은 최근 날짜부터 온다(2026-10-14 첫 보고서는 이틀 전인 10월 12일치였다).
  같은 날짜의 더 새 보고서는 그날 값을 교체한다. CTR은 0~1 비율로 온다(2026-10-14 첫 보고서, Studio와 같음).
  공식 문서에 단위가 없어서, 1보다 큰 값이 있는 보고서는 백분율로 읽는다.
- 스냅샷: 업로드의 태평양 시간 날짜부터 3·7·28일 고정 구간을 측정한다. 두 노출 보고서(합계, 유입 경로별)는 따로
  도착한다. 구간의 모든 날짜에 두 보고서가 다 오면 측정하고, 4일 지나도 다 오지 않으면 그때까지 온 값으로 측정한다.
  그날 노출이 없던 영상은 보고서에 행이 없지만, 보고서가 왔으면 그날은 채워진 것으로 본다. 구간이 끝나고 6일이
  지나서 측정한 업로드(처음 켤 때의 과거 영상)는 카드 없이 기준선과 주간 리뷰에만 쓴다.
- 3일 카드(성과 채널 새 글, [ADR 0046](adr/0046-video-analytics-studio-edits-and-thread-answers.md)): 노출, CTR,
  조회, 평균 시청, 30초 유지, 구독 두 줄, "✏️ YouTube에서 바꿔볼 것", 한 줄 진단을 담는다. 수치는 같은 회차 종류의
  최근 28일 중앙값과 비교하고, 5편 미만이면 "기준 부족"이라고 쓴다. 진단은 Claude(`video_performance_v1`, 실제 썸네일
  첨부, `VIDEO_REASONING_EFFORT`)가 한다.
  - 바꿔볼 것: Studio에 그대로 붙여 넣을 제목, 설명 첫 줄, 태그 최대 3개와 근거 한 줄이다. 새 제목은 100자 이내이고
    그 회차의 꼬리표(`| M월 D일 아침|마감 · 오늘의 증시story`)로 끝나야 한다. 다른 회차로 읽히는 제목은 서비스가 버린다.
  - 자세한 진단(비교 구간, 유입 상위, 검색어 상위, 가장 가파른 장면, 강점·약점·가설)은 카드의 Slack 수신이 확인된
    뒤 그 스레드의 첫 답글로 붙는다. 카드를 보낸 tick 다음 tick은 2분 뒤에 돈다.
  - 진단이 실패하면 수치와 유입·검색어만으로 올리고 스레드 답글은 없다. 7일 갱신은 같은 스레드에 수치로 단다.
- 스레드 질문: 소유자가 전달된 카드나 주간 리뷰의 스레드에 답글을 달면 질문 하나가 된다(`video_questions`). dispatch가
  질문마다 `VideoQuestionWorkflow`를 분석 큐에서 시작하고, Claude(`video_answer_v1`)가 그 보고서의 입력(수치, 기준선,
  진단, 썸네일)과 앞선 문답 최대 5개를 보고 같은 스레드에 답한다. 카드 스레드에서는 바꿔볼 것도 함께 낸다. 답은
  물은 것부터 짧게(약 600자 이내, 근거는 한 줄) 쓰고, 바꿔볼 것과 교훈은 본문에서 되풀이하지 않고 아래 목록과 버튼으로만
  보인다.
  - 영상 제작 중이면 "제작이 끝나면 답할게요"를 한 번 알리고 5분마다 다시 시도한다. 약 7시간 안에 못 하면 다시
    물어 달라고 알린다.
  - "다음부터 ~해" 같은 지시는 교훈 제안 하나로 돌아오고, 그 답변에 채택·거절 버튼이 붙는다. 다른 사용자, 스레드
    밖 메시지는 질문이 아니다. 질문 하나가 회사 일일 호출 하나를 쓴다.
- 주간 리뷰(월요일 09:00 KST, worker가 멈췄으면 48시간 안에 보충): 최근 28일 회차, 영상별 진단, 활성 교훈의
  적용 전후 중앙값으로 `video_weekly_v1`(`VIDEO_REVIEW_EFFORT`, 썸네일 최대 12장)을 요청한다. 리뷰는 반복 패턴과
  교훈 제안 최대 3개(제목·썸네일·오프닝·구성·길이·키워드), 활성 교훈마다 유지·은퇴·판단 이르음을 낸다. 채널에
  없는 영상을 근거로 든 제안과 이미 활성인 교훈은 버린다.
- 교훈: 리뷰 메시지의 버튼(`채택`·`거절`, 활성 교훈은 `은퇴`)과 답변 메시지의 버튼(`채택`·`거절`)은 브리핑 소유자만
  누를 수 있다. 버튼은 그 메시지(Slack 수신 확인된 ts·채널)에 묶여 있고, 결과는 그 스레드에 답한다. 같은 클릭이
  재전달되면 처음 결과를 돌려준다. 활성 교훈이 상한이면 채택하지 않는다. 채택한 교훈은 그 뒤 새로 만드는 작업의
  `policy.lessons`에 고정되어 `video_episode_v1` 대본 작성 프롬프트에 데이터로 들어간다. 원문 줄·숫자·표기·제목 규칙이 우선한다고 명시하며, 검토 프롬프트에는 넣지 않는다.
  이미 만든 작업의 요청은 바뀌지 않는다.
- 실행 lane: 분석은 영상 대본·검토와 같은 Claude 구독 실행기를 쓰므로 queued~delivering 상태의 영상 작업이 있으면
  요청하지 않는다. 요청 ID는 고정(`video-analytics-<영상>-d3`, `video-analytics-weekly-<월요일>`)이고 회사 일일 호출
  한도에 들어간다. busy·quota는 환불 후 다음 tick에 같은 ID로 다시 요청한다. 질문 답변은
  `video-analytics-answer-<질문>`이다.
- 렌더 manifest에 장면 시각(`scenes`)이 추가됐다. 이전 회차와, 이 버전 이전 이미지로 렌더한 Fargate 작업은
  챕터 시각으로 대신한다.

**켜는 순서(소유자 작업 포함)**

1. Google Cloud 프로젝트에서 YouTube Analytics API와 YouTube Reporting API를 사용 설정한다.
2. OAuth 동의 화면을 프로덕션으로 게시한다. Testing 상태에서는 refresh token이 7일마다 만료돼 수집이 끊긴다.
3. 서버에서 `quant-company video-auth youtube --listen-host 0.0.0.0`으로 다시 로그인한다(포트 8767 터널, 해당 채널의
   브랜드 계정 선택). 이제 scope는 upload, force-ssl, yt-analytics.readonly다.
4. Slack에 성과 채널을 만들고 Analyst 봇을 초대한다. runtime.env의 `SLACK_ALLOWED_CHANNELS`에 넣고
   `VIDEO_ANALYTICS_CHANNEL_ID`를 설정한다. Analyst 앱의 interactivity가 꺼져 있으면 켠다. Socket Mode는 그대로 쓰고,
   HTTP 방식이면 `/slack/events/market_brief`로 설정한다. 파일 전달만 쓰는 현재 운영에서는 꺼져 있을 수 있다.
5. claude-runtime을 이 버전으로 갱신한다. 새 출력 계약 두 개는 실행기가 알아야 한다.
6. `docker compose ... exec video-worker quant-company video-analytics sync --dry-run`을 실행한다. 저장, 게시, 모델 호출
   없이 업로드 목록과 회차 해석, Reporting 작업, 최근 3일 구간 수치를 출력하므로 Studio와 대조한다.
7. `VIDEO_ANALYTICS_ENABLED=true`로 api, worker, dispatch, slack-socket, video-worker를 같은 설정으로 다시 띄운다.
   서버 overlay가 `VIDEO_*` 값을 직접 나열한다면(예: `deploy/combined73/*`), 세 값(`VIDEO_ANALYTICS_ENABLED`,
   `VIDEO_ANALYTICS_CHANNEL_ID`, `VIDEO_ANALYTICS_MAX_LESSONS`)을 같은 서비스들에 넣는다.
   상태는 `quant-company video-analytics status`로 보고, 연결 안 된 업로드는
   `quant-company video-analytics link --youtube-id … --job-id …`로 연결한다.

끄기는 `VIDEO_ANALYTICS_ENABLED=false`다. 대기 중인 성과 채널 글은 `stale`이 되고, 활성 교훈은 새 작업에 고정되지
않는다. 테이블(`video_publications` … `video_lesson_actions`)은 이전 코드가 무시한다.

## 로컬 데모와 검증

```sh
uv sync --extra video
uv run --extra video playwright install chromium
# FFmpeg/ffprobe 설치 후 macOS 기본 음성으로 로컬 디자인 샘플 생성
uv run --extra video python demos/daily_video.py
# Linux에서는 샘플 대본을 읽은 로컬 파일 세 개를 --audio로 제공
uv run --extra video pytest tests/test_video*.py
uv run ruff check .
```

데모는 합성 문안과 macOS 음성/제공 음원을 사용한다. Runway 과금과 YouTube 업로드를 하지 않는다.
Chromium/FFmpeg 실제 렌더링, 실제 오디오 시간과 로컬 ASR를 확인한다. 자동 테스트의 cloud/Slack/model은
명시적 fixture이며 PostgreSQL·Temporal·Chromium·FFmpeg 확인과 구분한다.
[구현 검증 기록](project/DAILY-VIDEO-VALIDATION.md)에 최종 검사와 남은 운영 연결을 기록한다.
