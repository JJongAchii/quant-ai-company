# 회사 공용 Codex 수동 계정 전환 검증

날짜: 2026-09-23. 구현·로컬 검증 완료. **운영 배포·예비 계정 등록은 미실시**.

## 구현

- 총괄 DM의 정확한 세 명령: 상태, 기본으로 전환, 예비로 전환. 지정 소유자만 허용.
- Slack 명령과 회신은 기존 DB/inbox/outbox를 사용하며 상태·전환에 모델 추론을 사용하지 않는다.
- 별도 Temporal 제어 queue와 PostgreSQL의 선택 revision·명령 영수증·호출 binding·재개 신호 기록.
- 분리된 공식 ChatGPT `CODEX_HOME`과 공용 작업 receipt. 실행 중 요청은 기존 계정을 유지하고,
  명시적인 quota 거절만 새 소유자 선택으로 옮긴다. 완료·불확실·취소 결과는 보존한다.
- 계정별 한도 대기와 한 번의 소유자 알림. 자동 계정 순환·API 전환·추가 구매는 없다.

## 검증 범위

| 항목 | 검증 |
|---|---|
| 신규 계정 기능 | 실제 PostgreSQL과 실제 자식 프로세스, Codex 응답은 fixture. 25개 통과 |
| Temporal | 실제 로컬 서버에서 quota 대기 → 전환 → 동일 요청 완료, 신규/기존 이력 replay, 이전 worker의 타이머 이후 새 신호 방식 채택 |
| 공식 CLI | 고정 Codex 0.154.0의 빈 인증 디렉터리에서 설정 probe 2개 통과. 실제 로그인·추론 없음 |
| Slack | 서명 검증을 포함한 합성 이벤트. 권한 밖 사용자·공개 채널·bot·편집·변조 서명 차단. 실제 Slack 송신 없음 |
| Claude | 공용 HTTP transport 회귀 발견 후 수정, 기존 Claude runtime 25개 검사 통과 |
| 전체 서비스 | 1,055 passed, 37 skipped, 0 failed. PostgreSQL을 사용하는 전체 회귀 검사 |
| Compose / 계산 fixture | 29 / 3 passed. 배포는 수행하지 않음 |
| 정적 검사 | Ruff, diff 공백, 로그인 스크립트 구문 통과 |

전체 서비스 검사는 `TEST_DATABASE_URL=<일회용 PostgreSQL> uv run pytest`,
Compose 검사는 `uv run pytest deploy/test_deployment_contract.py`, 정적 검사는
`uv run ruff check .`, `git diff --check`, `bash -n deploy/codex-account-login.sh`로 실행한다.
로컬 Temporal 시험 서버의 저장소는 일회성이다. 운영 실행 큐는 기존 Temporal Cloud를 사용한다.
37개 skip은 별도 실행한 공식 CLI probe 2개, 등록된 qlab checkout 필요 31개, Linux 3070 운영 프로필 필요 3개, 실제 Codex 구독 호출 1개다.
실제 ChatGPT 구독 두 계정으로 전환·추론한 것으로 표시하지 않는다.

초기 Temporal 통합 fixture의 과거 날짜 follow-up과 공용 Claude transport의 추가 인자 호환성 문제를
찾아 수정했다. 이 기록은 최초 실패를 숨기지 않으며 최종 결과와 구분한다.

## 운영 상태와 남은 단계

호스트를 읽기 전용으로 조회했다. 앱은 `f40fa107…`, Codex/Claude runtime은 `377335dc…`,
연구 worker는 `31ff903f…`로 각각 실행 중이다. 예비 인증 디렉터리와 계정 기능 설정은 아직 없다.
현재 동작하는 quant-feed/데이터/뉴스/연구 설정을 과거 Compose 또는 단일 main 이미지로 덮어쓰지 않는다.

남은 운영 단계는 현재 배포 묶음에 변경 통합, 새 이미지·추가 스키마 배포, 사용자의 예비 계정
공식 기기 로그인, 지정 소유자 설정과 기능 활성화, 실제 소유자 Slack 명령과 다음 모델 응답 확인이다.
기존 대기 workflow의 최초 timer는 완료 후 새 신호 방식을 채택하므로 첫 전환의 재개가 늦을 수 있다.

- [명령·설치·복구](../runbooks/model-accounts.md)
- [설계 결정](../adr/0037-owner-selected-codex-accounts.md)
- [검증 증거](evidence/model-accounts-20260923/verification.json)
- [비밀정보를 제외한 운영 조회](evidence/model-accounts-20260923/host-inventory.json)

qws 도구는 독립 회사 저장소를 인식하지 않아 이 격리 worktree의
`docs/work/anglerfish/INTENT-v1.json`, `STATUS.json`에 작업 범위를 기록했다.
원래 연구 저장소의 고정된 계약·과거 기록은 변경하지 않았다.
