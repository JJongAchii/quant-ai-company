# 직원 모델 배정 검증 — 2026-10-06

상태: **구현·검증 완료, 운영 활성화 완료**. 이 문서는 테스트 범위를 기록하며
실제 운영 확인은 [별도 배포 기록](MODEL-ASSIGNMENTS-PRODUCTION-20261006.md)에 남겼습니다.
새 환경의 배포 기본값은 `MODEL_ASSIGNMENTS_ENABLED=false`입니다.

사용자가 승인한 첫 구현 범위는 직원별 모델·추론 강도 고정, 이번 업무 한정 지정,
모델 목록·배정 현황·변경 이력 조회, 고정 해제와 과거 배정 복원입니다.
새 모델의 자동 순위 평가·정기 교체와 독립 Claude 검토 모델 변경은 포함하지 않습니다.

## 검증 결과

| 검사 | 결과 | 검증 범위 |
|---|---|---|
| 최종 기능 CI | 1,521 passed, 46 skipped, 1 deselected, 2 warnings | 실제 PostgreSQL 및 로컬 Temporal; [CI 영수증](evidence/model-assignments-20261006/feature-ci.json) |
| main 통합 로컬 전체 pytest | 1,484 passed, 47 skipped, 2 warnings | 실제 폐기 가능한 PostgreSQL 및 로컬 Temporal 포함 |
| 최초 구현 전체 pytest | 1,430 passed, 47 skipped, 2 warnings | 실제 폐기 가능한 PostgreSQL 및 로컬 Temporal 포함 |
| 추가 회귀 2건 | 2 passed | 실제 감사 요청 생산자의 세션 모델 유지, 총괄 교체와 목록 장애 중 현황 조회 |
| Ruff | 통과 | 전체 저장소 |
| diff whitespace 검사 | 통과 | 최종 변경 |
| 실제 Codex CLI 0.154.0 | 설정·프로토콜 검사 통과 | 빈 인증 디렉터리와 모의 로그인 확인; `initialize`/`model/list`만 실행 |

추가 2건은 최초 전체 테스트의 수집이 끝난 뒤 추가한 테스트이며 당시 구현 코드는 동일합니다.
실제 CLI 검사는 앞선 집중 테스트 35건 통과에 포함됩니다. 이후 catalog 구현은 변경되지 않았습니다.
건너뛴 검사는 통과로 간주하지 않습니다.

## 확인한 동작

- 소유자·서명·workspace·channel·봇 응답 검증, 수신 시점과 처리 시점의 권한 재검사.
- Slack 중복 이벤트·동시 tick·Temporal 재실행에도 하나의 배정 revision과 결과만 커밋.
- 계정 변경 도중의 배정 거절, 없는 모델·지원하지 않는 강도 거절, 총괄 max 요구.
- PostgreSQL에서 배정과 이력을 읽으므로 다른 회사 프로세스에서도 동일한 배정 조회.
- 이번 작업 지정이 해당 직원의 후속 턴·웹검색에 유지되고 전역 고정이나 동료 배정을 덮어쓰지 않음.
- 기존 요청 재시도·직원 연습 snapshot·개선 비교·감사 세션은 원래 모델·강도를 유지.
- 일반 대화·연구 요청 생산자·직원 연습·개선 작업·뉴스 편집/선별/검색·Quant 편집의 연결.
- 모델 목록은 인증된 private runtime에서 페이지 단위로 조회하며 추론 thread/turn을 시작하지 않음.

위 테스트의 Slack 이벤트, 계정 상태와 모델 응답은 합성 fixture입니다. 빈 인증 디렉터리의 CLI
검사는 지원 옵션과 RPC 동작의 증거입니다. 이후 운영 계정의 실제 목록·인증·서비스 상태도
확인했으며 해당 증거는 별도 배포 기록에 구분했습니다. 소유자의 실제 배정 명령과 지정 모델
실행은 첫 사용자 입력을 기다립니다. 점검용 Slack 메시지를 보내지 않았습니다.

## 운영 반영 조건

추가 migration 적용 후 API·Slack 수신기·계정 제어 worker·private runtime과 모델 요청을 만드는
모든 worker를 이 코드로 갱신해야 합니다. 기존 연구 worker의 이미지가 고정된 배포도 포함됩니다.
혼합 버전에서 기능을 켜면 구버전 worker는 저장된 배정을 해석하지 못합니다.
인증 profile에 `config.toml`이 있으면 모델 목록 조회는 거절됩니다.

운영 활성화와 실제 모델·Slack 확인 범위는 별도 기록을 참고합니다. 배정의 일반 복원은
`모델 배정 복원 번호`를 사용합니다.

- [사용 명령과 운영 절차](../runbooks/model-assignments.md)
- [설계 결정](../adr/0038-owner-controlled-model-assignments.md)
- [검증 영수증·소스 SHA-256](evidence/model-assignments-20261006/validation.json)
- [전체 pytest 출력](evidence/model-assignments-20261006/pytest.txt)
