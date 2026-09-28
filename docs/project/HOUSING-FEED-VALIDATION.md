# 서울·경기 housing-feed 검증

2026-09-28 사용자가 새 `#housing-feed`와 서울·경기 범위로 Slack 알림을 요청했다.
공공데이터포털 계정·키가 없으므로 청약홈·LH·SH의 공식 공개 HTML을 읽는다.
프로그램에 API 키나 모델 호출은 추가하지 않았다. 상세 정책은 [운영 안내](../housing-feed.md).

## 구현

- PostgreSQL 공고 버전·수집 lease·발송 영수증, 기존 outbox와 Reporter 발송 경로.
- Temporal의 전용 housing queue, 5분 durable timer와 시간당 수집.
- 서울·경기 분양주택 필터, 접수 세부 일정, 원문·공고문 링크, 신규·변경·전날·당일 알림.
- 발송 직전 설정·공고 버전·날짜·소스 신선도 재검사. 불명확한 Slack 응답은 자동 재발송하지 않음.
- 수집 전용 컨테이너에는 Slack·모델 자격증명을 전달하지 않음.

## 검사 상태

최초 전체 검사: 1,184 passed / 38 skipped / 1 failed. 실패는 기존 테스트의 최소 설정 객체에
새 housing 설정 필드가 없는 경우였다. 기본 비활성 처리를 추가하고 관련 검사 45개를 통과했다.
추가 배포 검사에서는 다른 서비스 설정 보존, 소유자 검증, 실패 시 pending/uncertain 보존과
기존 dispatcher 복구 순서를 검사했다. 전체 최종 결과와 실제 운영 영수증은 아래 증거에 기록한다.

공개 HTML fixture·HTTP mock·Slack mock 테스트와 실제 공개 사이트 조회/Slack 게시는 별개다.
실제 PostgreSQL 14 임시 DB 및 로컬 Temporal 서버에서 activity 재시도·worker 재시작·history replay를 검사했다.
SH의 첨부 문서에만 있는 일정은 자동 추출하지 않으며, GH 별도 공고·임대·오피스텔은 수집 범위 밖이다.

최종 공통 검사: **1,203 passed / 38 skipped**, 285.18초.
공용 채널 목록 변경이 다른 feed의 policy digest를 바꿀 수 있어 housing 전용 발송 허용 목록으로
수정했다. 이후 관련 **52개 검사 통과**, 전체 lint 통과. 최초 피드에서는 수신 Slack 이벤트의 허용 범위를 확장하지 않았다.
[기계 판독 증거](evidence/housing-feed/automated-validation.json)에 검사 버전과 범위를 구분했다.

## 2026-09-28 운영 활성화

- 새 `housing-feed` 채널 `C0C4UV5C14J`, 기존 Reporter `U0C2FLSUUMV`, workspace `T0C1YRDRPNF`.
- 운영 release `ccdcc43c2a0985dcc035262f33d19e0ae979f0a1`.
- 서버 preview는 청약홈 APT 2건, 잔여세대 7건, LH 1건, SH 최근 모집 공고 0건. 네 소스 모두 성공.
- DB·Temporal 실제 운영 경로 활성화. `company-housing-feed-v1` RUNNING, activity 완료 및 300초 timer 확인.
- 12:15 KST readback에서 **3건 실제 게시, 7건 순차 발송 대기**. 게시 ID·채널·bot·client_msg_id 일치.
  Slack이 URL의 `&`를 `&amp;`로 돌려주므로 HTML entity를 풀어 비교했고, 게시 본문은 일치한다.
- 이후 소스별 다음 수집은 13:12 KST 이후. 공고 변경·신규·확인 가능한 접수 일정의 알림을 지속한다.
- collector와 dispatcher만 변경했고, 기존 PostgreSQL·연구 worker·다른 서비스의 컨테이너와 상태를 보존했다.
  collector에는 DB·Temporal secret만 마운트되어 있다. 실제 Slack token 값은 출력·Git 저장하지 않았다.
- 최종 GitHub CI: **1,244 passed / 36 skipped / 1 deselected**, lint 통과.
  [CI](https://github.com/JJongAchii/quant-ai-company/actions/runs/36372204330), [PR #75](https://github.com/JJongAchii/quant-ai-company/pull/75).

최초 `5fc89a2` full image build는 layer export 도중 900초 제한으로 종료됐으며 활성화하지 않았다.
의존성 입력 다섯 파일이 기존 release와 동일함을 확인하고, 기존 image ID를 고정한 새 image에
회사 source 전체와 wheel을 다시 설치했다. 새 image 생성은 41초였다. 실행 중인 컨테이너 수정이나
Slack 발송 재생은 하지 않았다. 서버 journal의 `updates`는 **preview 설정**이며, 실제 활성화 설정은
아래 영수증의 `effective_housing_settings` 및 실제 worker 조회에서 확인한다.

- [공식 소스의 서버 접근](evidence/housing-feed/server-source-access.json)
- [배포·프로세스·설정 영수증](evidence/housing-feed/release-receipt.json)
- [실제 PostgreSQL·Temporal·Slack readback](evidence/housing-feed/live-readback.json)

## 2026-09-28 움직이는 지도 패널 추가

사용자는 정적 지도 사진이 아니라 Slack 안에서 움직이는 지도를 원했고, 오른쪽 패널 방식을 선택했다.
기존 Reporter 앱의 Work Object Previews에 일반 항목 유형과 `www.openstreetmap.org` 허용 주소를
등록하고 `entity_details_requested` bot 이벤트를 구독했다. 별도 도메인·공공데이터 API 키·지도 API 키는 없다.
검증된 공고 주소의 행정구역 중심을 지도 표식으로 사용하며, 정확한 대지 위치로 표현하지 않는다.

첫 정적 이미지 방식은 Slack이 `invalid_blocks`로 거절해 `sent_ts`가 없는 채로 차단됐다.
해당 운영 릴리스는 기존 버전으로 되돌렸고 실제 Slack 게시가 없었다. 현재 구현은 Slack의
[Work Objects embed](https://docs.slack.dev/messaging/work-objects-embeds/)를 사용한다.

- 운영 릴리스 `c54667e2827506d50b4cbfaa63d924f2b69f21ab`. 같은 소스의 수집기·발송기·
  Slack Socket 수신기만 교체했다. 재시작과 OOM은 0, 다른 서비스 컨테이너는 보존됐다.
- 실제 서버 사전 검증에서 지오코딩, 공개 HTTPS 임베드 HTTP 200, DB 마이그레이션을 확인했다.
  재수집 때 네 소스 모두 성공했고 활성 공고 10건 중 주소 있는 9건 모두 지도용 위치가 생겼다.
  지도 정보만으로 새 공고 알림이 생성되지 않아 `queued=0`이었다.
- 기존 공고를 분명한 시험 메시지로 한 번 발송했다. Slack 게시·`client_msg_id`·outbox 영수증 일치.
  macOS Slack에서 Work Object 카드 클릭 시 인터랙티브 지도 로딩, 확대, 오른쪽 사이드 패널 표시를 확인했다.
  클릭 세부정보 응답 3건은 DB에서 모두 `delivered`, 오류 없음.
- `conversations.history`는 게시된 메시지를 반환했지만 Work Object entity 배열은 반환하지 않았다.
  카드·지도는 실제 Slack UI와 `entity.presentDetails` 응답 영수증으로 따로 확인했다.
- 로컬 전체 검사 **1,215 passed / 38 skipped**, ruff 통과.
  [새 CI](https://github.com/JJongAchii/quant-ai-company/actions/runs/36382267702)도 성공했다.

[지도 패널 배포 영수증](evidence/housing-feed/panel-release-receipt.json),
[PostgreSQL·Slack readback](evidence/housing-feed/panel-live-readback.json),
[Slack UI 확인 기록](evidence/housing-feed/panel-ui-validation.json),
[오른쪽 패널 화면](evidence/housing-feed/panel-sideview.png)에 결과를 남겼다.

청약 자동 접수, 개인 자격 판정, GH 별도 공고, 임대·오피스텔, SH PDF-only 일정 추출은 포함하지 않는다.
