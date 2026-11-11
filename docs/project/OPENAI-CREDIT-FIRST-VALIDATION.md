# OpenAI API-first staff execution validation

Production is active on main `f9a19b4b9cfbf661a5056c332daff4f8e6babb8c` as of 2026-11-06.
All 17 services verified without drift. Real API calls, real employee API work and the ChatGPT subscription CLI
completed successfully; see the operational receipts below.

2026-11-06. The owner requests a **$120 KST calendar-month service cap**, then the existing subscription.
The latest version 2 policy no longer requires the provider's actual credit expiry or a free-only billing assertion.
It does not guarantee all charges are offset by the Pro benefit. The owner confirmed auto recharge is disabled.

Predeployment local qualification after integrating production main 7dda5b6: **451 passed, 3 skipped** in 144.34s; repository Ruff
and diff checks passed. Real disposable PostgreSQL 14 and real Compose configuration are used. Responses HTTP
and subscription inference are simulated (the latter uses real synthetic subprocesses). The skipped tests are
explicitly opt-in real Codex configuration probes. Key file mode 0600 and model metadata GET authentication were
also confirmed; these are not an inference or provider-billing qualification.

Coverage includes the $120 authorization limit, KST month/year/leap-month boundaries, budget exhaustion to
subscription, retrying API after the next month, shared reservations, explicit API refusals, ambiguous-call replay
prevention, existing sessions/effort, account routing and non-secret operational display. Existing Claude API,
release, account and operator-status regressions passed.

Before rollout, read-only inspection found production on CI-green main 7dda5b6 without drift and without the
API key/policy or enable setting. The owner subsequently approved PR #220 and assigned this session as release
operator. The reviewed PR was merged, main CI passed, and the standard release completed as recorded below.
The older local-check and handoff files retain their original predeployment state.

- [Latest check receipt](evidence/openai-monthly-120-20261106/local-checks.json)
- [Exact monthly policy](evidence/openai-monthly-120-20261106/monthly-budget-policy.json)
- [Predeployment operator handoff (historical)](evidence/openai-monthly-120-20261106/operator-handoff.json)
- [Activation and reconciliation runbook](../runbooks/openai-credits.md)
- [Earlier version 1 grant-policy tests](evidence/openai-credit-first-20261105/local-checks.json)

## 2026-11-06 운영 활성화

소유자가 PR #220 병합과 이 세션의 릴리스 운영을 승인했다. main CI를 통과한
`f9a19b4b9cfbf661a5056c332daff4f8e6babb8c`를 기존 `deploy/release.py`의
prepare/build/migrate/apply/activate 절차로 반영했다. 신규 서비스·렌더 이미지·호스트 unit 변경은 없다.
main CI는 서비스 회귀 3,376 passed / 142 skipped / 1 deselected, 실제 CLI 프로토콜 9 passed였다.
운영자 설정으로 키와 월 $120 정책을 설치하고 `OPENAI_API_ENABLED=true`를 적용했으며,
작업 중 일시 정지한 자동 릴리스를 기존 `true`로 복원했다. 키 값·인증 파일·runtime.env는 이 저장소에 보관하지 않는다.

두 모델의 실제 API 호출을 먼저 로컬에서 검증했고, 그 사용액도 서버의 같은 월 원장에 옮겨 계상했다.
배포 후 두 운영 런타임에서 각각 Sol/Astra의 실제 API 완료 영수증을 확인했다.
검증용 호출 외에 실제 직원 작업의 API 완료도 확인했다. 별도 증거에는 입력·출력 본문을 제외하고
요청 ID 해시, 모델, 실행 경로, 토큰 수와 예산 정산만 남겼다.
effort가 없는 기존 요청 형식으로 ChatGPT 구독 CLI의 실제 완료 영수증도 확인했다.
이는 양쪽 실행 경로의 운영 확인이며, 예산을 소진하는 실험은 하지 않았다.
예산·잔액 부족에 따른 자동 전환은 앞서 기록한 회귀 테스트로 검증했다.

월 $120은 소유자가 승인한 서비스 예산이다. OpenAI의 매월 무료 지급액이나 실제 잔액 조회를 뜻하지 않는다.
예산은 KST 매월 1일 갱신된다. 검색·이미지·기존 대화·가격 미등록 모델 등은 기존 구독 경로를 유지한다.

운영 증거: [배포 결과](evidence/openai-monthly120-live-20261106/release-result.json),
[전체 서비스 검증](evidence/openai-monthly120-live-20261106/release-verification.json),
[실제 API·구독 실행](evidence/openai-monthly120-live-20261106/live-verification.json),
[직원 API 완료 영수증](evidence/openai-monthly120-live-20261106/production-api-receipts.json),
[자동 릴리스 상태](evidence/openai-monthly120-live-20261106/release-status.json).
