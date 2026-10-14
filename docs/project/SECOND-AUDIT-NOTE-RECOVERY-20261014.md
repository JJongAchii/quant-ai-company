# 두 번째 결과 검증 메모 복구 — 2026-10-14

## 실제 중단

10월14일08:32 KST 실제 PostgreSQL/네이티브 영수증 확인에서 두 번째 trial의 계산·결과 수신·초기 해석은 완료됐지만, audit stage는 10월13일11:32:06 KST 이후 waiting/error=audit_runtime_requires_reconciliation였다. 진행 중 turn·연구 job은0이었다. 최종 보고서는 없다.

거절된 실제 Astra 응답5는 JSON 구조와 packet digest가 맞고, notes 길이8020자가 기존8000자 최대값을20자 넘었다. 원문 receipt SHA256은 ab126d331502c6a6144560125353e442750a88beceb6dd6fe42550209b24f602다. 원래 네이티브 응답·4개 완료 packet·미접수 packet5·실험 result는 보존돼 있다. [실제 거절 메타데이터](evidence/etf-exploration-20260930/second-audit-note-stall-20261014.json).

## 수정과 자격검증

source commit6a64d792333e0d5a871eb865f55d154656a06cb1의 제품 코드는 audit_delivery.MAX_NOTES 한 상수만8000에서24000으로 바꾼다. 누적 관찰 기록의 허용 길이를 넓히며, 기존6000자 작성 목표·90000자 전체 prompt 최대값·turn/token 예산·필수 원문 전량 읽기·packet digest·provider thread/session·최종 audit 검증은 유지한다. notes를 자르거나 직원 판단을 수정하지 않는다.

실제 테스트 PostgreSQL을 사용한13개 검사는 통과했다. 8020자와24000자 원문 보존,24001자 거절, padding 거절, 최종 resident context90000자 초과 거절, 부분 읽기의 final 오인 방지, audit turn/token budget hold를 검사했다. 모델·Slack·시장 결과는 합성 fixture다. 전체 회사 검사는 진행 중이며 repository lint는 통과했다.

운영 부모 API/worker의 실제 식별자·환경·기타 source/module digest를 먼저 확인했다. 두 모드의 audit_delivery 바이트가 동일하고 원래8000자 정책이다. 최신 worker를 포함한 실제 부모 위에 이 모듈 한 개만 교체하는 비활성 이미지를 준비하고, 원래 frozen response를 unchanged packet/session/evidence consumer로 통과시키는 롤백 검사를 수행한다. [실제 부모 확인](evidence/etf-exploration-20260930/audit-note-runtime-baseline-20261014.json).

## 운영 반영과 실제 계속 여부

현재 운영 적용 전이다. 기존 배포·진행 승인 범위에서 PR105와 exact image/manifest를 검토하고, 일관된 백업·drain·원래 환경과 동시 배포 보존을 거친 뒤 API/worker만 적용한다. 등록된3070 science release와 source/data/비용/리스크/서명 예산은 그대로다.

적용 뒤 native cached response5를 원문 그대로 기존 stage consumer에 한 번 접수해 실제 다음 요청6까지 확인한다. 형식 오류 복구를 audit pass·새 경제평가·보고 완료로 계상하지 않는다. 현재 측정 result2건, 최종 보고1건이며 과학 프로그램은 계속 active다. 추가 owner 승인이 이번 중단 원인은 아니다.
