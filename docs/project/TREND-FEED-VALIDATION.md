# 한국 검색 트렌드 브리핑 검증

2026-10-06, 독립 회사 worktree `batfish`, 기준 commit `afe3e88` 위 변경 사항.
사용자가 설계 구현을 승인했고, 후속 요청으로 기존 Slack 워크스페이스의 전용 채널·앱 생성도 승인했다.
[실행 근거와 코드 SHA-256](evidence/trend-feed-20261006/RESULT.json).

## 구현·자동 검사

Google KR 관측, 07:30 KST 동결, 네이버 같은 응답 내 추이, 허용 원문 근거,
하루 최대 2회 구조화 편집, 독립적인 08:00 확정·09:00 만료와 영속 outbox를 구현했다.
전용 발송 계정·CLI·기본 비활성화 설정·네이버 secret의 수집 프로세스 한정 마운트를 포함한다.

| 검사 | 결과 | 실제/모의 범위 |
|---|---|---|
| 신규 기능·관련 기술 피드·역할 회귀 | 67 통과 | 실제 PostgreSQL·로컬 Temporal, HTTP/모델/Slack 모의 |
| 전체 회귀 `pytest -q -m 'not live'` | 1,448 통과, 45 skip, 1 deselect | 기존 선택적 외부 연동 skip, opt-in 모델 검사는 별도 |
| Ruff·`git diff --check` | 통과 | 전체 저장소 |
| 기본 Compose + 네이버 overlay | 파싱 통과 | 수집/발송 기본 off, secret은 news-worker에만 연결 |

Temporal worker 재시작, 커밋 후 activity 재시도와 이력 재생, 수집 대기 중 독립 확정을 검사했다.
마감 경계·반복 관측의 최대값·304·중간 수집 공백·첫날 이력 부족·원문 없는 설명 거부·공통 원문 없는
합치기 거부·모델 2회 제한·quota 대기 ID 유지·취소/불명 호출의 자동 대체 금지·늦은 응답·네이버
100회 제한·정책 변경·만료·Slack 429/불명 영수증·전용 앱 이벤트 차단도 확인했다.
첫 전체 검사에서 기존 최소 설정 fixture에 새 플래그가 없는 문제를 발견해 기본 off 처리를 보완하고
전체 검사를 다시 통과했다. [최종 pytest 출력](evidence/trend-feed-20261006/pytest.txt).

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

## 연결 상태

기존 `Achii's Quant Research` (`T0C1YRDRPNF`)에
[Trend Scout 앱](https://api.slack.com/apps/A0C7VRH8JV6) 생성을 실제 앱 목록으로 확인했다.
`chat:write` 한 권한, 이벤트 0개인 manifest로 생성했다.
[전용 채널](https://app.slack.com/client/T0C1YRDRPNF/C0C6WTA9ECV) `C0C6WTA9ECV`를 만들고,
Trend Scout 설치와 채널의 앱 목록 등록을 확인했다. 실제 `auth.test`에서 워크스페이스·봇 ID와
`chat:write` 단일 권한을 확인했다. 봇 토큰은 Git 밖 로컬 비밀 파일에 0600 권한으로 저장했다.
[Slack 연결 근거](evidence/trend-feed-20261006/slack-connection.json).
실제 메시지는 아직 보내지 않았으며 운영 배포·발송 성공으로 표시하지 않는다.

새 앱 설정 화면의 미사용 구형 verification token이 도구 출력에 포함돼 출력 마스킹을 보완했다.
사용자가 비밀번호 재확인을 완료했으나 교체 요청이 다시 인증 화면으로 돌아가,
앱 설정에서 직접 재발급을 완료하도록 요청한 상태다.
토큰 값은 회사 코드·검증 파일에 기록하지 않는다. 서비스는 이 구형 토큰을 사용하지 않는다.

네이버 키는 사용자가 Git 밖의 사용자 설정 디렉터리에 직접 저장했고 0600 권한을 확인했다.
API HUB의 검색어 트렌드·뉴스 검색 실제 인증과 조회를 확인했으며 키 값은 검증 기록에 포함하지
않는다. 운영 서버 secret 연결, 전용 채널 실제 발송 영수증, 3일 미리보기 관측은 남아 있다.

[설정·데모·복구](../trend-feed.md), [설계 결정](../adr/0038-korean-search-trends-briefing.md).
