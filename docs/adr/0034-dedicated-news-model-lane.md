# 0034: 연구와 독립된 뉴스 모델 실행 자리

날짜: 2026-09-22. 상태: 사용자 승인, 운영 적용 완료. 실제 관찰은 활성화 보고서 참조.

## 요구와 결정

사용자는 연구 작업과 별도로 뉴스 작업 1개가 계속 처리되도록 요청했다.
기존 연구 우선 조건은 실행 시각이 된 queued/running/waiting 업무가 하나만 있어도
뉴스 선별·편집·검색을 미뤘다. 총 실행 수 제한과 뉴스의 무기한 대기를 구별한다.

- 일반 회사/직원/maintenance 모델 자리 1개를 유지하고 뉴스 전용 자리 1개를 추가한다.
- 뉴스 선별·편집·검색은 `기존 queue-news-model`의 단일 activity 자리를 공유한다.
  뉴스 수집과 함께 별도 `news-worker` 프로세스에서 실행한다. 연구 worker의 개별 중지와 분리한다.
  workflow ID와 DB frozen request ID는 보존하고, queue 이동은 정상 continue-as-new로 한다.
- Temporal patch는 아직 실행하지 않은 첫 tick에서만 새 queue를 선택한다. 패치 판단이
  메모이즈되므로 전환 전 tick별 patch ID를 사용하고, 다음 run부터는 전용 queue를 계속 사용한다.
  기존 queue의 뉴스 activity 등록은 이미 예약된 구버전 activity를 처리하기 위해 남긴다.
- Codex gateway는 서비스가 발급한 `news-` ID를 하나의 뉴스 파일 잠금으로 라우팅한다.
  다른 ID는 원래 회사 잠금을 쓴다. 여러 runner 객체에도 lane당 1개이며, 총 2개다.
- 기존 요청 digest·영수증 위치·cached completion·취소·불명 결과 보호를 유지한다.
  새 영수증에만 실행 lane을 기록한다. ASGI worker 수는 취소 소유권 때문에 여전히 1개다.
- 연구 대기 조건만 제거한다. 공통 PostgreSQL 일일 사용량 예약, 전역 pause, 실제 구독
  quota 처리, 뉴스 내용 검증·주기·발송 시간·Slack outbox 안전성은 변경하지 않는다.

## 경계와 배포

계정·서버·예산을 추가하지 않는다. Tech Scout는 계속 무모델 발송 identity다.
프로세스는 분리하지만 서버·DB·Codex·dispatcher를 공유하므로 전체 장애 격리나 무중단 배포를
보장하지 않는다. 새 프로세스는 192 MiB 상한이며 Slack·연구 레이크/실행 자격증명을 받지 않는다.
백업·업데이트·복구 서비스 목록에 포함한다. 동시 처리로 시간당 소모량이 증가할 수 있으나
예산을 자동 확대하지 않는다.

구버전/신버전 실행기를 같은 jobs volume에 동시에 띄우지 않는다. 기존 모델 호출을
마친 후 두 잠금을 확보해 새 호출을 막고, 백업·코드 전환·잠금 해제·재기동 순서를 따른다.
queued/frozen 요청과 uncertain 영수증은 보존한다. 배포 후 자연 뉴스 작업과 서버 자원을
관찰하며, 모의 모델/Slack 테스트와 실제 구독/Slack 증거는 별도로 기록한다.

## 검증

실제 subprocess 모의 Codex로 회사/뉴스 동시 실행, lane당 직렬화, 중복 호출·변경된
입력 거부, 독립 취소, 구버전 orphan 영수증 보존을 검사한다. 실제 PostgreSQL·Temporal로
공유 예산 경합, 연구 중 뉴스 처리, 연구 worker 없는 뉴스 편집/검색 직렬화, 구버전 기록 재생과
같은 workflow ID의 다음 run 전환을 검사한다. 운영 결과는 별도 활성화 보고서에 남긴다.
