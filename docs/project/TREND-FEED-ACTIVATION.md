# 한국 검색 트렌드 운영 전환 준비

2026-10-06. 사용자가 **미리보기·시험 메시지·검증 후 정기 발송까지 진행**을 승인했다.
운영 서버에 키를 연결하고 두 대상 프로세스를 미리보기 모드로 전환했다.
백업은 S3에 보존했으며, 기존 Slack 항목·역할·모델 요청·다른 컨테이너를 보존했다.
실제 실행에서 뉴스 워커의 128 PID 한도가 부족한 것을 발견해 256으로 조정하고 있다.
Slack 시험 발송과 3일 아침 검증은 진행 중이며, 정기 발송 플래그는 아직 꺼져 있다.

## 준비한 대상

- 기존 회사 서버: AWS 서울 `quant-company-host-4gb-20260928`, 새 인스턴스 없음.
- 기존 워크스페이스: `T0C1YRDRPNF`, 소유자 `U0C250E23NW`.
- [전용 채널](https://app.slack.com/client/T0C1YRDRPNF/C0C6WTA9ECV): `C0C6WTA9ECV`.
- [전용 앱](https://api.slack.com/apps/A0C7VRH8JV6): `A0C7VRH8JV6`, `chat:write` 단일 권한.
- 실제 네이버 검색어 트렌드·뉴스 인증 성공. 키는 Git 밖의 로컬 0600 파일에 보관 중.
- 소비 프로세스: 기존 `news-worker`, `dispatch`. 다른 실행 중인 프로세스는 대상에서 제외한다.

운영 서버는 여러 기능의 개별 릴리스가 함께 실행된다. 전체 회사 이미지를 일괄 교체하지 않고
두 대상의 **실제 이미지에서 추출한 소스**에 검토된 트렌드 변경만 합친 후보를 만들었다.
기존 시장 브리핑·모델 배정·뉴스 기능을 보존하고, 부모 이미지의 의존성·환경·명령·사용자·
기존 revision label을 유지한다. 별도 trend label과 파일별 before/after SHA-256으로 변경을 식별한다.

[실제 서버 staging 영수증](evidence/trend-feed-20261006/production-stage.json),
[소스 manifest](evidence/trend-feed-20261006/production-source-manifest.json).
staging과 [실제 미리보기 전환](evidence/trend-feed-20261006/production-cutover.json)을 구분해 기록한다.

## 운영 연결과 미리보기

1. 배포 잠금, 실행 중인 릴리스 여부, 두 대상의 이미지·컨테이너 ID·설정 digest를 다시 확인한다.
   검사 후 다른 작업이 대상을 바꿨으면 중단하고 새 기준을 검토한다.
   기존 뉴스 모델 작업·대기 outbox가 비어 있는 전환 구간을 사용한다.
2. 기존 백업 경로로 DB·설정·영수증을 보존한다. 토큰은 백업·Git·모델 입력에 포함하지 않는다.
3. 기존 Slack secret에 `trend_scout` 항목만 추가하고 기존 항목을 대조해 보존한다.
   NAVER secret은 서버의 `/var/lib/quant-company/secrets/trend-naver-credentials.json`에 설치한다.
   부모 secrets 디렉터리의 root 접근 제한과 읽기 전용 마운트를 유지하며, `news-worker`만 읽는다.
   역할 설정에는 발송 전용 `active=false` 항목만 추가하고, 허용 채널에 새 ID를 추가한다.
4. 트렌드 테이블을 기존 앱 DB 계정 권한으로 추가한다. 운영 DB에 합성 후보나 가짜 날짜를 넣지 않는다.
5. 기존 compose 파일·개별 override·모델 배정·메모리 한도를 보존한 채 두 대상만 전환한다.
   다음 설정으로 실제 수집·편집·08시 확정을 시작한다.

```dotenv
TREND_FEED_ENABLED=true
TREND_FEED_PUBLISH_ENABLED=false
TREND_FEED_CHANNEL_ID=C0C6WTA9ECV
TREND_FEED_OWNER_USER=U0C250E23NW
TREND_FEED_NAVER_ENABLED=true
```

`news-worker`에만 `/run/secrets/trend_naver_credentials`를 마운트한다.
후속 릴리스·백업·복구에서도 이 overlay를 유지하는 호스트 경로를 확인해야 한다.
Codex 실행기에는 Slack·NAVER·DB secret을 추가하지 않는다.

6. 실제 Temporal의 수집·편집·확정 workflow와 DB의 Google 수집·네이버 응답을 대조한다.
   기존 프로세스의 ID·설정·상태가 보존됐는지도 확인한다. 최초 날의 이력 부족을 표시한다.
7. 발송 범위가 승인됐으면 새 채널에 연결 확인 메시지 **1건**을 보내 실제 채널·`ts`를 기록한다.
   내용은 `Trend Scout 연결 확인 완료. 3일간 미리보기 검증 중이며 정기 브리핑은 아직 시작하지 않았습니다.`다.
   불명 결과는 재전송하지 않고 실제 Slack과 영수증을 대조한다.

## 3일 확인과 정기 발송

실제 시계로 3일의 아침 확정을 관측한다. 첫날 집계 구간이 짧으면 이력 부족을 유지한다.
세 날짜 모두 다음 사항을 DB 영수증과 최종 미리보기로 확인한다.

- 수집 시각·중간 공백이 정직하게 표시되고, 07:30 입력과 08:00 최종 본문이 일치한다.
- 네이버 지수는 같은 응답 안에서 비교하고 최신 자료 날짜·비교 불가 사유를 표시한다.
- 8개 이하 주제에 중복·허용되지 않은 출처·원문 없는 배경 설명이 없다.
- 모델 요청은 KST 하루 2개 이하, 네이버 요청은 100개 이하이며 기존 회사 예산을 공유한다.
- 늦은 모델 응답·재시작으로 최종 본문이 교체되지 않고 미리보기 outbox가 생성되지 않는다.
- 운영 오류·불명 호출·기존 뉴스 및 시장 브리핑 회귀가 없다.

호스트의 미리보기 검증 timer가 실제 DB 기록과 확정 본문을 검사하고,
각 본문을 두 번 관측해 불변성을 확인한다. 3개의 연속된 실제 날짜가 모두 통과한 후
이미 승인한 범위대로 `TREND_FEED_PUBLISH_ENABLED=true`로 전환한다.
이미 확정한 미리보기는 소급 발송하지 않고, 전환 다음 날부터 매일 **08:00 KST·최대 8개**를 전한다.
기간이 아직 지나지 않았거나 품질 조건이 충족되지 않으면 미리보기를 유지한다.

## 중단과 복구

트렌드의 수집·발송 플래그를 모두 끄고 두 대상을 기존 이미지·설정으로 복구한다.
추가 테이블과 모델·Slack 영수증은 보존해 불명 요청을 자동 재실행하지 않는다.
원래 실행 중이던 릴리스·백업 timer 상태를 복구한다. 네이버 overlay를 지원하지 않는 구버전으로
되돌릴 때는 네이버 활성화 플래그도 함께 끈다.

Slack 구형 verification token은 새 토큰 활성화 후 페이지 재로드까지 확인했다.
[교체 영수증](evidence/trend-feed-20261006/slack-token-rotation.json)은 변경 여부만 보존하며 값은 기록하지 않는다.
