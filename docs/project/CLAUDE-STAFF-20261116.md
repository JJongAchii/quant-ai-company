# 직원·Quant의 Claude 전환 준비

사용자는 뉴스·브리핑·영상을 유지하고 나머지 GPT 업무를 Claude로 바꿔 보도록 요청했다.
대상은 총괄·금융전략·국내/글로벌/가상자산 연구·데이터·개발·독립 검증·리스크·운영·개선·Quant Scout다.
모델은 운영 영상에서 이미 쓰는 Opus 5.5, 추론 강도는 각 대상의 기존 값을 유지한다.

현재 구현은 새 요청을 모델 ID별 공급자로 라우팅하고, 소유자가 검토한 preview digest와 현재 정책 revision이
일치할 때만 한 transaction으로 배정을 적용한다. 기존 요청과 재시도·감사 세션의 선택은 바꾸지 않는다.
새 Claude 업무의 API 지출은 허용하지 않으며, 영상 모델 단계가 우선한다.

[전환 대상·적용·복원 절차](../runbooks/claude-staff.md)에 정확한 API 입력과 검증 경계를 기록했다.
회사 저장소를 정본으로 하는 격리 qws에서 작업했으며 quant-workspace의 원래 계약과 연구 저장소를 수정하지 않았다.

상태: 구현·로컬 검증 완료, [PR #241](https://github.com/JJongAchii/quant-ai-company/pull/241) 검토 대기. 검토된 main 병합·정식 릴리스·배정 적용·실제 Claude 계약 호출은 아직 수행하지 않았다.
이 문서를 운영 전환 완료 증거로 사용하지 않는다.

로컬 전체 비-live 회귀 검사 3,406 passed / 147 skipped / 1 deselected, 최종 대상 검사 72 passed와 Ruff를 확인했다.
실제 PostgreSQL과 모의 CLI·HTTP·Slack의 범위, 소스 digest와 검사 결과는 [검증 기록](evidence/claude-staff-20261116/validation.json)에 남겼다.
[운영 읽기 전용 조회](evidence/claude-staff-20261116/production-before.json)는 여전히 정책 #2와 기존 main 커밋을 확인했다.
[12개 대상 제안](evidence/claude-staff-20261116/proposal.json)은 적용 전 문서이며 실제 운영 API가 만드는 apply digest를 대신하지 않는다.
