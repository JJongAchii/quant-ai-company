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
