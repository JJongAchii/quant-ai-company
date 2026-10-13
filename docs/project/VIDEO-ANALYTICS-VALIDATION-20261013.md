# 영상 성과 분석 구현 검증 · 2026-10-13

소유자가 요청한 영상 성과 분석을 구현했다(설계: [ADR 0043](../adr/0043-video-performance-analytics.md), 운영:
[daily-video 성과 분석](../daily-video.md#성과-분석)). 공개 업로드를 제작 작업에 연결하고, 노출·CTR·조회·이탈·유입·
검색어를 고정 구간으로 측정한다. Claude 진단 카드와 월요일 리뷰를 성과 채널로 보내며, 소유자가 채택한 교훈만 새 영상
작업에 고정한다. **운영 서버에 배포하거나 켠 증거는 없다.** 기본값은 `VIDEO_ANALYTICS_ENABLED=false`다.

## 확인한 범위

| 구분 | 방식 |
| --- | --- |
| PostgreSQL | 실제 로컬 PostgreSQL 14(일회용 클러스터). 테스트마다 새 DB를 만든다 |
| Temporal | 실제 로컬 Temporal dev server. 분석 큐 tick과 history replay를 확인했다 |
| YouTube Data·Analytics·Reporting API | **simulated**(httpx MockTransport와 fixture client). 실제 Google API는 호출하지 않았다 |
| claude-runtime(Claude 구독) | **simulated** provider. 실제 모델은 호출하지 않았다 |
| Slack | **simulated** 수신·발신(서명 payload와 outbox 전송 fixture) |

전체 검사(2026-10-13, 로컬, `main` 70c5884 위로 rebase한 브랜치): `uv run pytest` 2,603 통과, 79 건너뜀
(Playwright·FFmpeg 등 optional 의존성이나 외부 환경이 필요한 검사), 실패 0. `uv run ruff check .` 통과.

## 검증한 동작 (새 테스트 74개)

- 제목 끝 회차 해석(아침·마감·연말·옛 접두어 거절), 같은 제목 대체 연결, 수동 link 유지
- 노출 보고서: CSV 열 검증, 백분율 표기 보고서 환산, 같은 날짜의 새 보고서는 교체, 오래되거나 반복된 보고서는 무시,
  유입 경로별 합산. 읽지 못하는 보고서 하나가 나머지를 막지 않음
- 태평양 시간 고정 구간 D+3·D+7·D+28, 노출 보고서 대기와 4일 유예, 60일보다 오래된 업로드 제외
- 이탈 곡선 보간, 장면별 이탈 기울기와 가장 가파른 장면, 옛 회차의 챕터 대체, 렌더 manifest 장면 시각
- 기준선: 같은 회차 종류, 직전 28일, 5편 미만이면 기준 부족
- 분석 영수증: 고정 요청 ID, 회사 일일 호출 한도, busy 환불 후 같은 ID 재요청, 실행 중 재요청 때는 처음 고정한
  요청(프롬프트·썸네일·effort)을 그대로 다시 보냄, 계약 위반·실패 때는 수치만으로 카드 게시
- 제작 중인 회차가 있으면 분석 대기(영상 제작이 꺼져 있거나 6시간 넘게 멈춘 작업은 제외)
- 3일 카드 내용, 7일 갱신은 카드 수신 확인 뒤 스레드 답글, 과거 업로드는 카드 없이 측정
- 월요일 리뷰: 썸네일 첨부, 채널에 없는 영상을 근거로 든 제안과 없는 교훈 평가 제거, 버튼 렌더링, 재실행 없음
- 교훈 버튼: 소유자, 채널, 수신 확인된 리뷰 메시지 ts, 버튼 라벨, block에 묶임. 위조 7종과 미수신·다른 메시지 거절,
  재전달 멱등, 활성 상한, 은퇴
- 새 작업에 활성 교훈 고정, 꺼져 있으면 고정 안 함, 대본 작성 프롬프트 반영, 나중에 채택한 교훈은 기존 작업에
  반영 안 됨, 교훈이 없으면 프롬프트가 이전과 동일
- 설정 검증(전용 허용 채널, 소유자, YouTube 채널), Analyst interactivity, OAuth scope, 썸네일 호스트와 토큰
  미전송, 보고서 다운로드 호스트 제한, 꺼지면 outbox `stale`

## 남은 운영 연결

- 소유자 작업: Google Cloud에서 YouTube Analytics API·Reporting API를 사용 설정하고, OAuth 앱을 프로덕션으로
  게시한 뒤 서버에서 YouTube에 다시 로그인한다(scope 3개). 성과 채널을 만들고 Analyst 봇을 초대한다.
- claude-runtime과 video-worker를 이 버전으로 갱신해야 한다. Fargate 렌더 이미지는 manifest `scenes`를 위해 다음 갱신 때
  함께 빌드한다. 그 전 회차는 챕터 시각을 쓴다.
- 첫 실연결: `video-analytics sync --dry-run`의 조회·시청 수치를 Studio와 대조한다. 첫 Reporting 보고서가 오면
  CTR 단위(비율/백분율)를 Studio 값과 대조한다. 그 뒤 첫 카드 1건을 실제로 게시하고 evidence에 기록한다.
