# 직원 모델 배정 운영 반영 — 2026-10-06

상태: **운영 활성화 완료**. 한국 시간 19:19에 교체를 완료했고 19:23 이후 읽기 검사에서
API·runtime·Temporal·Slack 인증을 확인했습니다. 소유자의 실제 모델 명령과 직접 지정한
모델의 업무 실행은 첫 사용자 입력 뒤 확인합니다. 현재 정책은 revision 0, 고정 없음이며
기존 직원별 모델·추론 강도를 보존했습니다.

## 적용과 보존

기능 소스 commit은 `bc123cad48f5dc9aabb8570c9b4c49cdfa38e968`입니다.
실제 운영 이미지의 코드에 기능 변경만 합쳐 9개 이미지를 만들고 13개 서비스를 교체했습니다.
일반 worker의 최신 총괄 context 수정, 뉴스·브리핑·Quant의 별도 소스 경로,
native 출력 계약, 고정된 연구 실행물, 서비스별 환경·명령·mount·자원·보안 설정을 유지했습니다.
모든 실제 import 경로의 전체 파일 hash를 최종 manifest와 대조했습니다.

PostgreSQL에는 모델 배정 테이블과 `tasks.model_selection`을 추가했습니다.
정책과 요청별 모델 선택은 DB에 기록하며 ProviderRequest의 wire format은 바꾸지 않았습니다.
이미 준비된 요청 10,058개의 digest를 교체 전후와 재확인 시점에 대조했습니다.
PostgreSQL과 독립 Claude container/image는 교체하지 않았습니다.
선택 계정은 primary revision 2이며 배포 중 일시 정지는 종료 후 원래 상태로 복원했습니다.

전체 절차는 기존 host 배포 잠금 아래에서 이미지 검사·실제 계정 목록 조회·호출 drain·
백업·추가 schema 적용·서비스 교체·설정 및 요청 digest 확인 순서로 실행했습니다.
교체 전 백업은 S3에 올렸으며 archive와 checksum 객체의 AES256 암호화·크기를 실제 조회로
확인했습니다. 인증 파일이나 Slack/DB 비밀은 Git에 저장하지 않았습니다.

## 확인 범위

| 검사 | 실제 결과 | 범위와 제한 |
|---|---|---|
| 기능 CI | 1,521 passed, 46 skipped, 1 deselected | 실제 폐기 가능한 PostgreSQL·로컬 Temporal; Slack/모델 응답은 합성 fixture |
| 운영 코드별 호환성 | 요청 생산자별 45~46개 통과, 두 runtime 검사 통과 | 실제 운영 코드 snapshot + 합성 RPC; 세부 [자격검증 기록](evidence/model-assignments-20261006/production-qualification.json) |
| 운영 fleet | 15개 running, OOM 없음, restart 0 | 교체 대상 13개; DB·Claude 보존 |
| 운영 API | `/healthz`, `/v1/model-assignments`, `/v1/agents` 정상 | operator bearer 적용, 무인증 401 |
| 실제 계정 모델 목록 | 두 이미지의 공식 CLI 조회 및 배포된 private HTTP 조회 성공 | 선택 계정 primary, 추론 turn을 시작하지 않는 `model/list` |
| 계정 제어 | `company-model-accounts-v1` RUNNING, activity poller 확인 | 실제 Temporal Cloud와 운영 워커 |
| Slack | 활성 직원 7개 모두 실제 `auth.test` 통과, Socket 프로세스의 TLS 연결 7개 | INFO startup 로그는 기존 설정에서 숨겨짐; 실제 소유자 명령 왕복은 미확인 |
| 새 요청의 배정 적용 | 배포 후 자연 발생한 engineer 요청 18개 completed, 1개 running | 당시 `gpt-5.6-sol max` 기본 배정; 고정 배정·작업 지정의 실제 업무 smoke와는 구분 |
| 기존 요청 | 10개 요청 테이블의 10,058개 digest 동일 | 불명 결과를 재전송하거나 추론을 점검용으로 재시작하지 않음 |
| S3 백업 | hash·크기·AES256 확인 | `/company/company-20261006T101422Z-180ab357.tar.gz` |

운영 목록에서 확인한 모델은 `gpt-6-astra`, `gpt-5.6-sol`, `gpt-5.6-terra`, `gpt-5.6-luna`입니다.
현재 회사에서 입력 가능한 추론 강도는 `low`, `medium`, `high`, `xhigh`, `max`입니다.
목록은 지정할 때 선택 계정에서 새로 검증합니다. 모델 자동 순위 평가·정기 교체는 이번 기능에
포함되지 않습니다.

## 운영상 제한

기존 release executor는 현재 여러 이미지의 기능·별도 소스 경로를 보존하지 못합니다.
`quant-company-release.service`의 ExecCondition guard가 자동 코드 교체를 보류하며 실제
`Result=exec-condition`을 확인했습니다. timer·유지보수 점검·PR 준비는 계속됩니다.
자동 코드 배포 재개에는 현재 fleet manifest와 모델 배정 기능을 보존하는 executor 통합이 필요합니다.
서비스를 수동 재생성할 때도 해당 서비스의 마지막 모델 배정 Compose override를 포함합니다.
이 제한과 복원 절차는 [운영 runbook](../runbooks/model-assignments.md)에 기록했습니다.

## 첫 사용

소유자가 기존 **#ai-account-switch**에 `모델 목록`, `모델 배정 상태`를 보냅니다.
지정 예시는 `모델 지정 개발 gpt-6-astra max`입니다. 업무 한정 지정은 일반 업무 채널·DM에서
첫 줄 `이번 작업 모델 gpt-6-astra high`, 다음 줄에 업무를 적습니다.
우선순위는 이번 작업 지정 → 직원별 고정 → 기본 배정입니다. 이미 준비된 요청에는 소급하지 않습니다.
소유자·채널 allowlist와 두 기능 플래그를 실제 확인했습니다. 채널 metadata 조회에는 기존 앱의
읽기 scope가 없어(`missing_scope`) 이름·가입 여부를 새로 확인하지 않았으며 권한을 변경하지 않았습니다.
채널 이름은 기존 계정 제어 운영 기록 기준입니다.

첫 실제 명령 뒤 `model_assignment_commands.receipt`, 현재 revision, outbox 전달 결과를 대조합니다.
이어 실제 업무의 요청 ID·model·effort와 runtime receipt를 확인합니다. 전달 결과가 불명확하면
같은 변경을 새 명령으로 반복하기 전에 기존 receipt를 대사합니다.

## 영수증

- [최종 기능 CI](evidence/model-assignments-20261006/feature-ci.json)
- [실제 계정 목록 조회](evidence/model-assignments-20261006/real-account-catalog.json)
- [이미지·교체 영수증의 비밀 제외 export](evidence/model-assignments-20261006/production-receipts.json)
- [운영 재확인·배정 현황](evidence/model-assignments-20261006/production-verification.json)
- [백업 실물 확인](evidence/model-assignments-20261006/backup-confirmation.json)
- [소유자 제어 설정 확인](evidence/model-assignments-20261006/owner-control-readback.json)
- [최종 소스 manifest](evidence/model-assignments-20261006/production-overlay-manifest.json)
- [교체 전 단계의 수정 이력](evidence/model-assignments-20261006/staging-repairs.json)

host의 원본 stage 영수증에는 실행 환경 전체가 있어 Git으로 가져오지 않았습니다.
공개한 증거는 metadata와 hash로 제한했습니다. 점검용 Slack 메시지를 보내거나 소유자 이벤트를
만들지 않았습니다.
