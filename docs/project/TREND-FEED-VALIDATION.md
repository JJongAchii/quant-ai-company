# 한국 검색 트렌드 브리핑 검증

2026-10-06, 독립 회사 worktree `batfish`, 최초 기준 commit `afe3e88` 위 변경 사항.
운영 준비 중 최신 `origin/main`의 `457a9c20f4e1`을 통합해 새 모델 배정·개선 담당 복구를 보존했다.
사용자가 설계 구현을 승인했고, 후속 요청으로 기존 Slack 워크스페이스의 전용 채널·앱 생성도 승인했다.
[실행 근거와 코드 SHA-256](evidence/trend-feed-20261006/RESULT.json).

## 구현·자동 검사

Google KR 관측, 07:30 KST 동결, 네이버 같은 응답 내 추이, 허용 원문 근거,
하루 최대 2회 구조화 편집, 독립적인 08:00 확정·09:00 만료와 영속 outbox를 구현했다.
전용 발송 계정·CLI·기본 비활성화 설정·네이버 secret의 수집 프로세스 한정 마운트를 포함한다.

| 검사 | 결과 | 실제/모의 범위 |
|---|---|---|
| 신규 기능·관련 기술 피드·역할 회귀 | 67 통과 | 실제 PostgreSQL·로컬 Temporal, HTTP/모델/Slack 모의 |
| 최초 전체 회귀 `pytest -q -m 'not live'` | 1,448 통과, 45 skip, 1 deselect | 최초 기준 위 검사 |
| 최신 모델 배정·릴리스 통합 회귀 | 95 통과 | 실제 PostgreSQL·로컬 Temporal, HTTP/모델/Slack 모의 |
| 통합 후 전체 회귀 `pytest -q -m 'not live'` | 1,514 통과, 47 skip, 1 deselect | 선택적 외부 연동 skip, opt-in 모델 검사는 별도 |
| 실제 운영 이미지에서 추출한 소스 + 트렌드 변경 | 176 통과, 1 skip | 기존 시장 브리핑을 보존한 후보 소스, 실제 로컬 PostgreSQL·Temporal |
| Ruff·`git diff --check` | 통과 | 전체 저장소 |
| 기본 Compose + 네이버 overlay | 파싱 통과 | 수집/발송 기본 off, secret은 news-worker에만 연결 |

Temporal worker 재시작, 커밋 후 activity 재시도와 이력 재생, 수집 대기 중 독립 확정을 검사했다.
마감 경계·반복 관측의 최대값·304·중간 수집 공백·첫날 이력 부족·원문 없는 설명 거부·공통 원문 없는
합치기 거부·모델 2회 제한·quota 대기 ID 유지·취소/불명 호출의 자동 대체 금지·늦은 응답·네이버
100회 제한·정책 변경·만료·Slack 429/불명 영수증·전용 앱 이벤트 차단도 확인했다.
첫 전체 검사에서 기존 최소 설정 fixture에 새 플래그가 없는 문제를 발견해 기본 off 처리를 보완하고
전체 검사를 다시 통과했다. [최초 pytest 출력](evidence/trend-feed-20261006/pytest.txt),
[최신 통합 pytest 출력](evidence/trend-feed-20261006/integrated-pytest.txt),
[운영 이미지 소스 회귀](evidence/trend-feed-20261006/image-derived-pytest.txt).
Reporter 모델 배정의 동결·quota 대기와 발송 전용 계정의 배정 제외,
후속 릴리스에서 네이버 secret overlay의 누락 거부도 확인했다.

## 실제 외부 확인

- [Google KR RSS](evidence/trend-feed-20261006/google-probe.json): 실제 HTTPS, 10개 항목, 파싱 누락 0.
- [실제 Codex 자격검사](evidence/trend-feed-20261006/codex-qualification.json): 공식 ChatGPT 인증,
  서비스 고정 CLI 0.154.0, Reporter의 `gpt-6-astra`/`high` 요청 설정, 정상 완료 1회.
  `news` 실행 경로와 읽기 전용 sandbox를 사용했다. Slack outbox 0건.
- 이 샘플의 Google 후보에서는 허용 원문 0건이었다. 실제 모델은 분류·숫자·링크 형태로 완성했다.
  따라서 실제 원문에 근거한 배경 설명의 품질 검증으로 해석하지 않는다. 이 모델 검사에는
  네이버 키를 연결하지 않았고, 네이버 실제 인증·조회는 아래 별도 검사로 확인했다.
- [네이버 실제 연결 검사](evidence/trend-feed-20261006/naver-connection.json): 사용자가 Git 밖의
  비밀 파일에 저장한 키로 검색어 트렌드·뉴스 검색 각각 1회, 모두 HTTP 200이었다.
  당시 Google 후보 3개의 응답을 요청한 키워드와 대조하고, 같은 응답 안에서 최근 7일/이전
  7일 추이를 계산했다. 최신 자료 날짜는 2026-10-05이고 뉴스 5건을 받았다.
  이 검사는 DB·모델·Slack에 연결하지 않았다. 뉴스 검색 결과는 원문 설명의 검증이 아니다.
- 자격검사만 기능 시계를 다음 날 아침으로 옮겼다. 원천 날짜는 바꾸지 않았으며 3일 실제 관측을
  대신하지 않는다. 최초 로컬 CLI 0.160.1은 실행 전 버전 검사에서 거부됐고,
  [실패 기록](evidence/trend-feed-20261006/codex-initial-version-rejection.json)을 보존한다.
  전역 Codex를 바꾸지 않고 `.local`에 고정 버전을 설치해 검사했다.

## 실제 운영 미리보기 상태

사용자가 2026-10-06에 미리보기·시험 메시지·검증 후 정기 발송까지 승인했다.
기존 4GB 회사 서버의 실제 두 소비자 이미지에 변경을 합쳐 `news-worker`·`dispatch`를 전환했다.
DB·설정·모델 영수증·연구 증거의 표준 백업을 S3에 저장하고 기존 Slack 항목과 역할을 보존했다.
네이버 키는 root 전용 secrets 디렉터리의 UID10001 0400 파일로 설치했으며,
읽기 전용 `news-worker` 마운트만 추가했다. 모델 런타임에 Slack/NAVER 키를 추가하지 않았다.

실제 실행에서 기존 뉴스 워커의 128 PID 한도가 부족한 `can't start new thread` 오류를 발견해
이 프로세스만 256으로 조정했다. 이미지의 다른 소스와 실행 환경·메모리 제한을 보존하고,
Trend Scout의 본문에 회사 지시 버전 접두사가 붙지 않도록 수정했다.
추가 회귀 **43개**, 실제 이미지에서 유래한 후보의 같은 회귀 **43개**가 실제 PostgreSQL에서 통과했다.
이 추가 테스트의 HTTP·모델·Slack은 모의이며, 아래 운영 성공과 구분한다.
[추가 회귀](evidence/trend-feed-20261006/activation-pytest.txt),
[이미지 후보 추가 회귀](evidence/trend-feed-20261006/activation-image-pytest.txt),
[실제 수리](evidence/trend-feed-20261006/production-repair.json).

- [운영 수집/연결](evidence/trend-feed-20261006/production-live.json): Google 성공 관측 2건,
  네이버 트렌드·뉴스 각 1건 성공, 실제 Temporal 수집·편집·확정 workflow 3개 RUNNING.
  해당 수동 네이버 연결 검사도 운영 DB의 하루 사용량/영수증에 기록했다. 모델·Slack을 호출하지 않았다.
- [실시간 미리보기](evidence/trend-feed-20261006/live-preview.txt)는 실제 수집 시작부터 당시 현재까지다.
  이력 부족과 AI 배경 설명 미포함을 표시했다. 아침 확정 결과나 3일 검증 완료로 해석하지 않는다.
- [전용 채널](https://app.slack.com/client/T0C1YRDRPNF/C0C6WTA9ECV)에 연결 시험 메시지 **1건**을 보냈다.
  [실제 발송 영수증](evidence/trend-feed-20261006/slack-test-receipt.json)은 `delivered`, 시도 1회,
  실제 채널·Slack `ts`를 포함한다. [채널 화면](evidence/trend-feed-20261006/slack-test-ui.json)도 확인했다.
  본문과 stable ID를 먼저 PostgreSQL outbox에 커밋했으며 불명 결과를 수동 재전송하지 않는다.
- 미사용 구형 verification token은 사용자 재발급 후 활성화하고 재로드 지속성을 확인했다.
  [교체 증거](evidence/trend-feed-20261006/slack-token-rotation.json)는 변경 여부만 보존한다.
- [현재 검사](evidence/trend-feed-20261006/production-final.json): 15개 컨테이너 실행,
  수리 이후 새 스레드 오류 0, 수집 실패 0, NAVER secret 소비자는 `news-worker` 하나다.
  자체 전환·수리 각각의 직후 두 대상 외 컨테이너를 보존했다. 기간 전체에서는 별도
  Codex upgrade label의 두 모델 런타임 교체가 관측됐다. 실행 설정·마운트는 동일하고,
  [별도 기록](evidence/trend-feed-20261006/model-runtime-peer-change.json)에 현재 건강 상태를 보존했다.
  앞선 로컬 0.154.0 실호출을 새 운영 모델 런타임의 실호출 검사로 간주하지 않는다.

`TREND_FEED_PUBLISH_ENABLED=false`다. 실제 아침 편집·최종 본문·예산·불변성 검증은 남아 있다.
설치한 timer는 10월 7일부터 매일 08:10·08:20·08:30 KST에 실제 PostgreSQL 기록을 읽는다.
3개 연속 날짜의 완료된 편집·원문 근거·네이버 응답·예산·확정 본문과 두 번의 관측이 모두 통과하고,
시험 발송 영수증과 배포 기준이 유지되면 이미 승인한 정기 발송 플래그를 켠다.
7·8·9일 통과 시 10월 10일 **08:00 KST·최대 8개**부터 발송하며, 미리보기를 소급 발송하지 않는다.
시간 미경과·검증 실패·불명 결과가 있으면 미리보기를 유지한다. 첫 실제 발송 영수증도 같은 timer가 확인한다.
[예약 영수증](evidence/trend-feed-20261006/preview-timer.json),
[초기 관측: 완료 날짜 0개](evidence/trend-feed-20261006/preview-observation-initial.json),
[발송 전환 설정 검사: 적용하지 않음](evidence/trend-feed-20261006/publication-dry-config.json).

[운영 전환 기록](TREND-FEED-ACTIVATION.md), [설정·데모·복구](../trend-feed.md),
[설계 결정](../adr/0041-korean-search-trends-briefing.md).
