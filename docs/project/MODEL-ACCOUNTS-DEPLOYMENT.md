# 회사 공용 Codex 계정 제어 운영 적용

2026-09-23 KST. **운영 반영 및 서로 다른 두 계정의 공식 인증 완료. 실제 소유자 Slack 전환 명령 확인은 진행 중이다.**

## 적용 버전

| 항목 | 결과 |
|---|---|
| 기능 PR | [#68](https://github.com/JJongAchii/quant-ai-company/pull/68), CI 통과 후 main 병합 |
| main 병합 | `ebb1e08d724a2d0358a59abf0226471b3e0472c7` |
| 실제 운영 소스 | `ce3b93565d7d0fd97ed4d0dcc93a64af2f2aa92e`, `deploy/model-accounts-production` |
| 기존 운영 소스 | `f40fa10755004db724d6c5c74ebadc3eab4ebec2` |
| 적용 상태 | `MODEL_ACCOUNTS_ENABLED=true`, 기존 허용 사용자 중 소유자 한 명 지정 |
| 현재 선택 | 기본 계정, revision 0. 자동 전환 없음 |
| 공식 CLI | Codex 0.154.0 유지 |

운영에 이미 포함된 Quant Scout와 데이터 점검 서비스를 유지하며 통합했다. app, Codex,
maintenance, Claude 이미지는 해당 커밋 전체 소스로 빌드하고 이미지 내부 패키지 파일의
해시를 저장소 소스와 비교했다. Claude CLI 버전과 인증은 유지했다.

연구 워커는 기존 `quant-company-autonomous:31ff903f08a1471d26cb2ce0fef9643a0b34fc75`
이미지와 `COMPANY_CODE_COMMIT`을 유지했다. 컨테이너는 연결 설정 적용을 위해 재생성했다.
모델 URL은 private account-gateway로 변경했다. gateway가 PostgreSQL의 선택 계정으로
요청을 전달하고 별도 Temporal 계정 제어 queue를 처리한다.

## 확인한 운영 상태

- 기존 PostgreSQL 컨테이너 ID 유지. 역할 설정, 연구 프로필, qlab 프로필 해시 유지.
- 모델 실행 잠금을 확보하고 쓰기 서비스를 중지한 후 DB·설정·작업 receipt·연구 기록을
  백업했다. 암호화된 S3 복사와 SHA-256을 기록했다. 인증 파일은 백업이나 Git에 넣지 않았다.
- 새 테이블 마이그레이션 후 기동. 정기 배포에서 사용하는 실제 `stable_health` 검사에서
  12개 서비스의 실행/상태/이미지 버전 및 PostgreSQL 보존을 확인했다.
- 실제 Temporal Cloud의 `company-model-accounts-v1`이 `quant-company-accounts`에서 RUNNING.
- 실제 공식 CLI 인증 조회: 기본·예비 계정 모두 `chatgpt`. 서로 다른 계정임을 호스트 안에서
  비교하고 boolean만 기록했다. 계정 식별자나 인증 내용은 기록하지 않았다.
- 예비 계정 로그인 터미널을 사용자에게 열었다. 사용자가 장치 코드 인증을 활성화한 뒤
  기존 인증 화면의 계속 버튼이 비활성이라고 알려 새 로그인 요청을 발급했다. 사용자
  스크린샷에서 설정 ON과 인증 거절이 계속되는 것을 확인했다. 원인은 아직 확정하지 않았다.
  공식 일반 브라우저 로그인과 SSH callback 전달로 전환해 사용자가 인증을 완료했다.
  로그인 전용 컨테이너에는 예비 auth 디렉터리 하나만 mount했음을 확인했고 완료 후 제거됐다.
- 실제 Slack 명령/응답 및 예비 계정 모델 응답은 아직 검증하지 않았다.

## 테스트 범위

| 실행 | 통과 | 건너뜀 | 실패 |
|---|---:|---:|---:|
| 기능 브랜치 전체 | 1,061 | 37 | 0 |
| 운영 통합본 전체 | 1,168 | 37 | 0 |
| 운영 통합 관련 테스트 | 141 | 1 | 0 |

통합본 전체 실행에서는 live 테스트 1개를 제외했다. 로컬 PostgreSQL 14 및 실제 Temporal
개발 서버를 사용했다. 자동 Slack 입력은 서명된 합성 fixture이고 모델은 가짜 Codex 실행
프로그램이다. 실제 계정 전환이나 실 모델 추론 성공으로 해석하지 않는다. 기능 PR의 CI도 통과했다.

## 적용 과정에서 수정한 문제

1. 예약된 운영 작업과 잠금이 겹쳐 첫 시작은 변경 없이 종료했다.
2. 서버의 Compose `run`이 `--no-build`를 지원하지 않아 첫 cutover는 마이그레이션 전에
   실패했다. 기존 이미지와 설정으로 복구됐음을 확인하고 지원되는 명령으로 다시 적용했다.
3. 128 MiB gateway 안에서 별도 전체 애플리케이션 진단 프로세스를 실행한 것이 메모리
   한도를 초과해 gateway가 한 번 재시작됐다. 당시 후속 DB 조회에서 계정 명령/모델 호출
   기록은 없었다. 진단을 더 큰 runtime/dispatcher 및 psql로 옮겼다. 이후 gateway는
   약 78 MiB로 동작하며 추가 재시작 없이 Temporal에 연결된 상태를 확인했다.

gateway 운영 진단은 `/healthz`와 별도 프로세스의 DB/Temporal 조회를 사용한다.
제한된 gateway 컨테이너 안에서 전체 회사 모듈을 다시 import하는 진단은 실행하지 않는다.

## 다음 단계

사용자가 총괄 봇 DM에 `모델 계정 예비로 전환`을 보내 실제 전환 응답을 확인한다.
회사 전체의 새 요청과 한도 대기 요청에 적용된다.
실행 중이거나 결과가 불확실한 요청, 완료된 요청은 기존 receipt를 보존한다.

[운영 조회/백업 receipt](evidence/model-accounts-20260923/production-activation.json),
[테스트 및 적용 중 조치](evidence/model-accounts-20260923/production-verification.json),
[공식 예비 계정 등록 확인](evidence/model-accounts-20260923/enrollment.json),
[메모리 확인](evidence/model-accounts-20260923/post-probe-stability.json),
[사용 및 복구 절차](../runbooks/model-accounts.md).
