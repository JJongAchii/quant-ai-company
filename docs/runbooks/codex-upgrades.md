# Codex 프로그램 갱신

직원의 모델을 지정하는 `모델 지정 TARGET MODEL_ID EFFORT`와 Codex CLI의 배포 버전은
별도 설정입니다. CLI 갱신은 기존 직원 배정·기본값·진행 중인 요청의 모델을 변경하지 않습니다.

## 버전과 신버전 감지

- `src/quant_company/providers/codex_release.json`이 검증 대상 버전과 공식 npm 배포 integrity,
  Linux x86_64 실행 파일 SHA-256을 정의합니다. Docker 설치와 실행·목록 조회 gate가 이 기준을 공유합니다.
- `Codex stable release watch`는 매일 09:17 KST에 공식 npm의 stable `latest`를 읽습니다.
  GitHub 스케줄에는 지연이 있을 수 있습니다. `workflow_dispatch`로 즉시 확인할 수도 있습니다.
- 신버전은 `updates/codex-cli-VERSION` 브랜치의 후보 PR로 기록합니다. 같은 버전의 기존·종료 PR은
  재생성하지 않습니다. 중단된 branch 생성은 원격 후보 identity/integrity를 확인한 뒤 PR만 이어갑니다.
- 후보 파일 `deploy/codex-candidate.json`은 `state=unqualified`입니다. 후보 PR을 병합해도 검증 manifest,
  운영 이미지, 직원 배정은 바뀌지 않습니다. 감지 workflow에는 운영 자격증명·모델 호출·배포 기능이 없습니다.
- 실제 계정 목록은 private runtime의 인증된 `GET /v1/models/primary` 또는 `backup`에서 확인합니다.
  응답의 `cli_version`은 실제 바이너리 gate 통과 버전이고 `checked_at`은 목록 검사 완료 시각입니다.
  기존 회사 client는 `profile`과 `models`를 그대로 사용하며 `모델 목록`이 새 목록을 읽습니다.

## 후보를 실제 배포하기

1. 공식 변경 기록과 npm 배포를 확인하고 manifest의 정확한 version/integrity/실행 파일 hash를 준비합니다.
   검증 pin을 바꾸는 PR은 실제 자격검증과 운영 반영에 대한 소유자의 승인으로 처리합니다.
2. `uv run ruff check .`와 실제 PostgreSQL·Temporal 회귀 검사를 통과시킵니다. CI `codex-protocol`은
   검증 파일로 Linux CLI를 설치하고 빈 인증의 configuration/model-list protocol을 검사합니다.
   빈 인증 RPC 검사는 실제 계정 사용권이나 추론 성공을 증명하지 않습니다.
3. 운영의 현재 두 immutable image와 실제 package root를 조회합니다. main은 설치 package,
   Quant는 별도 PYTHONPATH를 사용할 수 있습니다. `deploy/Dockerfile.codex-upgrade`로 각 원본을 보존하고
   reviewed source delta만 적용합니다. 원래 env/cmd/entrypoint/user/보안/자원·mount·image provenance를 비교합니다.
4. 선택한 실제 ChatGPT 계정으로 두 후보의 모델 목록을 확인하고, 작은 실제 구조화 응답·native Quant JSON,
   웹검색 event/사용량 및 구버전 session continuation을 검증합니다. 요청 ID와 receipt를 고정하고
   실패·불명 결과를 새 ID로 재실행하지 않습니다. 테스트는 메시지를 발송하거나 연구 실행을 하지 않습니다.
5. 운영 `.backup.lock`을 잡고 release timer와 새 요청을 잠시 멈춥니다. 활성 호출을 drain하고 writers를
   멈춘 뒤 표준 PG/jobs/evidence 백업을 확인합니다. 새로운 cutover journal과 service별 compose override로
   두 runtime만 교체합니다. schema와 assignment/account policy, 이미 동결된 입력 및 receipt를 보존합니다.
6. 두 실행기의 실제 버전·인증·목록과 전체 fleet 상태를 확인하고 원래 pause와 서비스를 복구합니다.
   모호한 cutover는 journal을 조정한 뒤 명시적으로 복구하며 같은 배포 script를 무작정 재실행하지 않습니다.

운영 복원은 원래 두 runtime의 immutable image/compose stack과 현재 영수증을 사용합니다.
백업 전체 복원으로 갱신 후 완료된 호출·외부 효과를 없애거나 다시 실행하지 않습니다.
현재 mixed-fleet 자동 code release 보호 guard는 유지합니다. 일반 release executor가 해당 overlay와
runtime version contract를 보존하기 전에는 자동 code cutover를 재개하지 않습니다.

## 이번 배포 기록

[2026-10-06 갱신](../project/CODEX-UPGRADE-20261006.md)은 실제 검증·배포 상태와 영수증을 기록합니다.
직원 모델 배정 명령은 [모델 배정 runbook](model-assignments.md)을 따릅니다.
