# 후속 가설 참조 오류 복구

## 실제 상태와 원인

2026-10-12 16:14 KST 확인에서 후속 proposal stage `378b5f19-1a59-5021-85a9-eb8c131d2242`는 attempt9 waiting이었다. 2026-10-13 08:58 KST 재확인에서도 attempt25 waiting, 진행 중 turn·outbox 발송·연구 job은 모두0이었다. 보고된 경제 실험은1건이며, 추가 경제평가는 시작하지 않았다.

총괄의 10월12일 기술 종료는 그대로 유효하다. 종료된 trial `5868e1af…`에는 측정 result가 없고, 해당 selected proposal `fa1c5f89…`에는 director rejection이 없다. 연구원이 이 ID들을 `predecessor_trial_ids` 또는 `supersedes_proposal_id`에 넣어 제출하면서 소비자가 정상적으로 거절했다. 다른 시도에는 UUID 형식 오류와 response rejection도 있었다.

기존 새 시도 준비가 stage.error를 지웠고, prompt에는 이번 미션에서 허용되는 두 관계의 ID 목록이 없었다. 따라서 이전 거절 원인을 새 provider 시도에 전달하지 못했다. 원래 응답·시도·실패·기술 종료의 영수증은 보존되어 있다.

근거: [첫 상태 확인](evidence/etf-exploration-20260930/successor-blocked-progress-20261012.json), [실제 제출 필드와 거절](evidence/etf-exploration-20260930/successor-proposal-rejections-20261012.json), [08:58 KST 재확인](evidence/etf-exploration-20260930/successor-blocked-progress-20261013.json).

## 변경과 검증

구현 커밋 `be0ae26`은 controller의 proposal prompt에 실제 PostgreSQL 소비자와 같은 조건의 ID 목록과 schema를 투영한다. 선행 ID는 result와 interpretation이 있는 같은 미션 trial이며, latest result를 포함해야 한다. supersedes ID는 같은 미션·현재 cycle에서 총괄이 거절한 proposal에 한정하고 null을 허용한다. 기술 종료의 실패 근거는 읽고 가설에 반영하되 두 관계의 자격이 생기는 것으로 안내하지 않는다. 새 proposal attempt에는 이전 contract error를 보존한다.

MissionStore·과학 가설·직원 응답·서명된 명세·평가기·데이터·비용·리스크·실험 예산·3070 release를 수정하지 않는다. 새로운 가설과 독립 반론·선정은 기존 직원 단계가 담당한다.

실제 PostgreSQL 대상 검사18건을 통과했다. 테스트의 모델·Slack·성과 값은 합성 fixture이며, 실제 시장 실험이나 Slack 배포 검증으로 인정하지 않는다. 첫 가설의 빈 관계, 측정 결과와 기술 종료의 구분, 소비자의 부적격 ID 거절, 실제 후속 단계 진입, 다음 attempt의 원래 오류 보존을 검사했다. 전체 회사 검사는 실행 중이며 완료 영수증 전에는 적용하지 않는다. repository lint는 통과했다.

## 운영 반영 상태

배포 준비 중이다. 실제 부모 source와 설치 runtime의 hash를 확인하고, runtime에 별도로 있는 모델 정책9줄을 보존한 채 commit diff를 fuzz0으로 적용했다. 나머지 파일은 실제 부모 바이트를 유지했다. [현재 부모 식별자](evidence/etf-exploration-20260930/proposal-navigation-runtime-baseline-20261013.json).

기존 배포·진행 승인 범위 안에서 PR #105와 정확한 적용 명세를 검토한 후 운영 반영한다. 새 과학 승인이나 직원의 가설 선택을 운영자가 대신하지 않는다. 적용 뒤 실제 신규 staff prompt와 소비자 접수·후속 단계의 영수증을 확인한다. 추가 실험 시작이나 성공은 그 실행 영수증이 나온 뒤에만 기록한다.
