# 후속 가설 참조 오류 복구

## 실제 상태와 원인

2026-10-12 16:14 KST 확인에서 후속 proposal stage `378b5f19-1a59-5021-85a9-eb8c131d2242`는 attempt9 waiting이었다. 2026-10-13 08:58 KST 재확인에서도 attempt25 waiting, 진행 중 turn·outbox 발송·연구 job은 모두0이었다. 보고된 경제 실험은1건이며, 추가 경제평가는 시작하지 않았다.

총괄의 10월12일 기술 종료는 그대로 유효하다. 종료된 trial `5868e1af…`에는 측정 result가 없고, 해당 selected proposal `fa1c5f89…`에는 director rejection이 없다. 연구원이 이 ID들을 `predecessor_trial_ids` 또는 `supersedes_proposal_id`에 넣어 제출하면서 소비자가 정상적으로 거절했다. 다른 시도에는 UUID 형식 오류와 response rejection도 있었다.

기존 새 시도 준비가 stage.error를 지웠고, prompt에는 이번 미션에서 허용되는 두 관계의 ID 목록이 없었다. 따라서 이전 거절 원인을 새 provider 시도에 전달하지 못했다. 원래 응답·시도·실패·기술 종료의 영수증은 보존되어 있다.

근거: [첫 상태 확인](evidence/etf-exploration-20260930/successor-blocked-progress-20261012.json), [실제 제출 필드와 거절](evidence/etf-exploration-20260930/successor-proposal-rejections-20261012.json), [08:58 KST 재확인](evidence/etf-exploration-20260930/successor-blocked-progress-20261013.json).

## 변경과 검증

구현 커밋 `be0ae26`은 controller의 proposal prompt에 실제 PostgreSQL 소비자와 같은 조건의 ID 목록과 schema를 투영한다. 선행 ID는 result와 interpretation이 있는 같은 미션 trial이며, latest result를 포함해야 한다. supersedes ID는 같은 미션·현재 cycle에서 총괄이 거절한 proposal에 한정하고 null을 허용한다. 기술 종료의 실패 근거는 읽고 가설에 반영하되 두 관계의 자격이 생기는 것으로 안내하지 않는다. 새 proposal attempt에는 이전 contract error를 보존한다.

MissionStore·과학 가설·직원 응답·서명된 명세·평가기·데이터·비용·리스크·실험 예산·3070 release를 수정하지 않는다. 새로운 가설과 독립 반론·선정은 기존 직원 단계가 담당한다.

실제 PostgreSQL 대상 검사18건과 전체 회사 검사1856건이 통과했고14건은 skip이다. 실제 Git·고정 qlab 경로를 사용했다. 테스트의 모델·Slack·성과 값은 합성 fixture이며, 실제 시장 실험이나 Slack 배포 검증으로 인정하지 않는다. 첫 가설의 빈 관계, 측정 결과와 기술 종료의 구분, 소비자의 부적격 ID 거절, 실제 후속 단계 진입, 다음 attempt의 원래 오류 보존을 검사했다. repository lint는 통과했다. [검사 영수증](evidence/etf-exploration-20260930/proposal-navigation-regression-20261013.json).

## 운영 반영 상태

**2026-10-13 09:34:15 KST** 운영 묶음 `5ec088b2854a2a94c6b514a25e2ae64e1a3dc6b0`을 적용했다. 실제 부모 source와 설치 runtime의 hash를 확인하고, runtime에 별도로 있는 모델 정책9줄을 보존한 채 commit diff를 fuzz0으로 적용했다. 나머지 파일은 실제 부모 바이트를 유지했다. [현재 부모 식별자](evidence/etf-exploration-20260930/proposal-navigation-runtime-baseline-20261013.json).

기존 배포·진행 승인 범위에서 PR #105와 정확한 적용 명세를 검토했다. 첫 적용 시도는 별도 maintenance 배포가 컨테이너를 변경했기 때문에 pause·교체 전 거절됐다. 최신 maintenance `2427a48`을 보존하는 새 준비·검토를 거친 뒤 API/worker만 교체했다. 그 외 모든 서비스 ID·image·env hash·메모리, runtime.env·역할·과학 profile·qlab 설정을 보존했고 owned pause를 해제했다. DB·내구 기록의 일관된 외부 백업 SHA256은 `5d4e64f5b5422fd4556e2bcc3a5a3be4663a08dc8f29855b9bfabff118acfcc2`이다. [적용 영수증](evidence/etf-exploration-20260930/proposal-navigation-runtime-applied-20261013.json).

배포 정지 중 생성된 기존 turn `bd144bfd…`는 해제 전45분 만료를 기다리는2695초 Temporal timer를 가지고 있었다. **09:38:06 KST** 그 unfrozen·due turn에 현재 선택된 primary revision2를 한 번 알렸다. account 선택·revision·요청·turn ID를 바꾸거나 새 turn/과학 실행을 만들지 않았다. effect 전 intent와 실제 signal 영수증을 보존했다. [대기·재개 근거](evidence/etf-exploration-20260930/proposal-navigation-existing-turn-wake-review-20261013.json), [실제 재개](evidence/etf-exploration-20260930/proposal-navigation-existing-turn-wake-applied-20261013.json).

**09:40:49 KST** 실제 attempt26은 Astra high request sequence29 running,28개 read 구간, error없음이다. 새로운 계약이 실제 요청에 들어간 것과 배포 뒤 완료된 자료 읽기·CLI0.160.1 실행을 확인했다. attempt26은 이전 코드에서 시작했으므로 그 시도의 이전 오류 보존을 소급 주장하지 않는다. [실제 요청](evidence/etf-exploration-20260930/proposal-navigation-actual-model-contract-20261013.json). 모델·API·PostgreSQL은 healthy, 앱·Slack socket은 running이며 OOM·restart없고 socket TCP443 연결8개를 확인했다. 새 signed Slack 수신→답변 왕복은 이번 적용의 별도 검증으로 주장하지 않는다.

## 실제 후속 단계 확인

**2026-10-13 09:42:20 KST**, 연구원의 attempt26 가설 `895fe4bb-dc4c-4fde-9bb3-b3ca089b9766`이 기존 소비자를 통과했다. 실제 제출의 선행 trial은 측정·해석된 `de3597b0…` 하나이고 supersedes는 null이다. 기술 종료 후보의 실패·계약 source는 근거로 유지했다. 직원 가설과 원래 artifact SHA256 `95d27193eec1ecebacc19997e36c48f259a28e0cf86dd4a4872a9ed089556130`을 운영자가 수정하지 않았다. [실제 접수 필드](evidence/etf-exploration-20260930/proposal-navigation-accepted-relationship-fields-20261013.json).

**09:47:54 KST**, 다음 독립 반론 stage `1599e78f-1fb3-5484-a9b2-cb7e9d301771`은 financial_strategist attempt1 running,22개 자료 읽기 구간, 실제 Astra xhigh request23 running이며 error가 없다. proposal stage는 completed다. [실제 후속 검토](evidence/etf-exploration-20260930/proposal-navigation-actual-proposal-accepted-20261013.json).

**09:49:40 KST** 독립 반론 제출이 완료됐다. **09:51:01 KST** 총괄 selection stage `7728b37d-7269-50bc-b9ff-bc863f9ce95c`은 attempt1 running,5개 자료 읽기 구간, 실제 Astra max request6이며 error가 없다. 현재 challenge `86fb575e…` 하나와 실제 선정 응답 schema가 일치했다. 이는 채택 결정이나 실험 시작을 뜻하지 않는다. [실제 총괄 선정 검토](evidence/etf-exploration-20260930/proposal-navigation-actual-director-selection-20261013.json).

새 가설의 outbox delivered와 실제 Slack 메시지의 author·thread·ts 일치를 확인했다. 긴 메시지의 top-level text는 outbox 본문과 달라 본문 전체 동일성은 이번 읽기로 판정하지 않았다. [메시지 확인 영수증](evidence/etf-exploration-20260930/proposal-navigation-actual-slack-message-20261013.json), [실제 연구 스레드 메시지](https://achiisquantresearch.slack.com/archives/C0C2B9EUEGM/p1791852141316649?thread_ts=1789633942.673909&cid=C0C2B9EUEGM).

INTENT-v21의 참조 오류 복구·배포·실제 후속 접수 확인은 완료했다. 과학 프로그램은 계속 active이며 완료된 경제 실험은1건, 추가 경제평가와 연구 job은 아직 시작하지 않았다. 현재 단계는 추가 owner 승인 대기가 아니다. 다음 독립 반론과 총괄 선정을 통과한 후보만 구현·실험으로 진행한다. 원래 보류·기술 종료·실패·응답과 과학 예산은 보존했다. 기존24시간 상한 관찰기는 read-only active/running으로 재확인했으며 10월13일11:39 KST까지의 상한과 자동 경보·수리 없음이 유지된다.
