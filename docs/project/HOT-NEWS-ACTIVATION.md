# Reporter 운영 활성화

2026-09-19 KST. 사용자의 “이어서 진행해” 요청에 따라 앱 연결·운영 소스 자격검증·기존 서버 배포를 진행한다.
구현만 검증한 이전 [기록](HOT-NEWS-VALIDATION.md)과 실제 운영 결과를 구분한다.

현재 단계는 배포 전 검증이다. Reporter 앱 `A0C2V1Q5CCE`를 기존 workspace `T0C1YRDRPNF`에 설치했고
bot user `U0C2FLSUUMV`의 실제 `auth.test`를 확인했다. 대상 `hot-news`는 `C0C2J1SSX09`다.
채널 참여·운영 발송·재시작 결과는 확인 후 이 기록에 추가한다.

수집처는 연준·ECB·BEA·영국 FCDO/재무부/통상부의 6개 공식 발표 피드로 구성한다.
영국 부처들은 하나의 정부 원천으로 취급하고 원문의 최초 발행 시각을 확인한다.
일반 언론의 글로벌 사건 보도는 아직 포괄하지 않는다. 뉴시스 자동 수집 제한을 반영했고,
BBC·KBS도 이용 범위 확인 전에는 수집을 켜지 않는다. BLS는 실제 HTTP 403이므로 제외한다.
유료 뉴스 계약·추가 서버는 구매하지 않았다. [운영 소스와 조건](../news.md)을 함께 읽는다.

- [로컬 원문 영수증](evidence/hot-news-qualified-originals-local.json): 실제 공개 RSS와 HTML. 모델·Slack 호출 없음.
- [회귀 검사](evidence/hot-news-activation-tests.json): 452 passed, 2 optional skipped. 실제 로컬 PostgreSQL·Temporal,
  pytest의 모델·Slack은 fixture. 배포·실제 발송 검증으로 해석하지 않는다.
- [운영 접속 변경](evidence/hot-news-operator-access.json): 기존 SSH 허용 주소를 보존하고 현재 운영 단말 `/32`만 추가.

배포는 현재 회사 서버의 역할별 사용자 설정·공식 구독 인증·기존 PostgreSQL을 보존하고,
정확한 커밋의 CI와 일관된 DB/모델 영수증 백업을 확인한 뒤 수행한다.
