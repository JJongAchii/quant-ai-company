# Codex 0.160.1 운영 갱신 — 2026-10-06

상태: **두 운영 Codex runtime 갱신·실제 계정 자격검증·매일 신버전 후보 감지 활성화 완료**.
운영 교체는 2026-10-06 20:43 KST에 완료했고 20:45 KST에 전체 상태를 확인했습니다.
소유자는 이전 진단과 구체적인 갱신 계획을 확인한 뒤 `그래 진행해줘`로 실행을 승인했습니다.

두 실행기와 회사의 실제 목록 조회 모두 선택 계정 primary revision 2에서 `gpt-6.1-sol`,
`gpt-6-sol`, `gpt-6-luna`를 포함한 7개를 반환합니다. 직원 배정은 revision 0, bindings {}를
유지합니다. CLI 기본 picker의 변경으로 회사 역할 모델이 바뀌지 않도록 모든 요청의 명시적 모델을 보존했습니다.

## 구현과 이후 갱신

검증 manifest 하나에서 Docker 설치 버전·공식 npm integrity·Linux 실행 파일 SHA-256과
실행·목록 조회 gate를 연결합니다. 무검증 CLI는 실제 호출 전에 거절합니다.
private 계정 목록 응답은 실제 바이너리 gate를 통과한 `cli_version`과 검사 완료 `checked_at`을 제공합니다.
기존 회사 client와 `모델 목록`은 같은 `profile`/`models` 계약으로 작동합니다.

`Codex stable release watch`가 매일 09:17 KST에 신버전 stable 릴리스를 확인하고 unqualified 후보 PR을
남깁니다. main의 실제 수동 dispatch도 성공했고 현재 0.160.1보다 새 후보가 없었습니다.
후보 PR에는 직원 모델 변경·모델 추론·자동 운영 반영이 없습니다. 해당 버전의 실제 계정·호환성 검증과
운영 반영 요청을 거쳐 적용합니다. 기본 GitHub workflow token 권한은 read를 유지하며 후보 생성 workflow만
contents/pull-requests write를 요청합니다. 저장소의 Actions PR 생성 허용도 확인했습니다.

모델은 기존 제어 채널에서 `모델 목록`으로 다시 조회합니다.
예를 들어 총괄을 별도로 지정하려면 `모델 지정 director gpt-6.1-sol max`를 사용합니다.
프로그램 갱신 중에는 이 지정 명령을 실행하지 않았습니다.

## 실제 검증

- 정확한 적용 코드 기준 Linux CI: **1532 passed, 47 skipped, 1 deselected, 2 warnings**.
  실제 PostgreSQL·Temporal이며 Slack/model 출력은 모의입니다.
- Linux CLI 별도 CI: **9 passed**. 실제 CLI·빈 인증·모의 login gate의 설정 및 model/list protocol 검사이며
  실제 계정 사용권·추론과 구분합니다. Linux의 `/tmp` 인증 경로 helper 경고는 테스트의 basetemp를
  운영 경로와 같은 방식으로 조정했습니다. 운영의 엄격한 preflight 조건을 완화하지 않았습니다.
- 로컬 전체 회귀: 1531 passed, 48 skipped, 1 deselected. 두 실제 runtime source cohort는 각각
  97 passed, 1 skipped, 1 deselected입니다. runtime에 설치되지 않는 company-side `RuntimeClient.models`
  검사는 cohort에서 제외하고 main 전체 suite와 실제 운영 client 경로로 검증했습니다.
- 실제 선택한 ChatGPT 구독으로 작은 호출 5개를 수행했습니다. 0.154.0 세션 생성, 0.160.1로 동일
  thread 이어받기와 누적 사용량 차분, `gpt-6.1-sol` typed 응답, native Quant v4 JSON,
  실제 native 웹검색과 사용량이 통과했습니다. 같은 ID의 완료 receipt 조회는 동일 결과를 반환했습니다.
  불명 호출을 새 ID로 재실행하지 않았고 연구·주문·점검용 Slack 발송은 하지 않았습니다.
- 운영의 두 private HTTP 모델 endpoint는 실제 버전 0.160.1·검사 시각·새 모델을 반환하고 무인증을
  401로 거절합니다. 실제 회사 client 경로, operator API, Temporal 제어 workflow와 poller 2개,
  기존 활성 Slack 직원 7명의 실제 auth.test 및 소켓 연결도 확인했습니다.

갱신 후 소유자의 새 Slack 이벤트를 만들어내지 않았습니다. 갱신 전 실제 `모델 목록` 응답의 delivered
영수증은 유지되며, 갱신 후 새 목록은 실제 운영 HTTP/client 조회로 확인했습니다.

## 배포와 보존

적용 구현 commit은 `b77dc9511fb6aae2beff68df1c0ba7852bac6bd4`이고 최종 Linux CI head는
`90fc51d62a31ddebb4b1362fb881982101eabe9e`입니다. 두 commit 사이에는 CI 경로와 자격검증 script만
바뀌었으며 적용 runtime source delta는 같습니다. 구현은 [PR #111](https://github.com/JJongAchii/quant-ai-company/pull/111)로 병합했습니다.

| 실행기 | 실제 활성 package root | 적용 immutable image |
|---|---|---|
| main | `/app/.venv/lib/python3.12/site-packages/quant_company` | `sha256:1da677f0627c690e3ecaadba4f368aad9271bfa581d71b20b1f07deb872ec8e6` |
| Quant | `/opt/quant-code/src/quant_company` | `sha256:94d32ec482ea0ba9cfd7890ef9f13113403beec858380ab8e3205d7b400e8fa1` |

기존 두 source cohort에서 version contract·목록 상태의 reviewed delta 4개 파일만 적용했습니다.
image의 원래 env/cmd/entrypoint/user와 source provenance, 실제 mount·network·보안·자원 설정을 보존했습니다.
동시 trend-feed 배포의 공통 잠금이 풀린 뒤 최신 전체 fleet을 다시 조회했고, 해당 변경과 원래 역할을 유지했습니다.
15개 서비스에서 Codex 두 개만 교체했으며 나머지 container ID/image를 보존했습니다. 전체 fleet의
running/health·OOM 여부를 확인했고 두 실행기 모두 실제 실행 파일 SHA-256이 manifest와 일치했습니다.

공통 배포 잠금·호출 drain·writer 중지·PG/jobs/evidence 백업 후 교체했습니다. **동결 입력 10,121개와
기존 receipt/session JSON 해시를 보존**했고 모델 배정·선택 계정은 바뀌지 않았습니다.
원래 runtime pause를 복구했고 실행과 Slack 연결이 재개됐습니다.

백업은 `/var/lib/quant-company/backups/company-20261006T113947Z-f0b8974e.tar.gz`이며
SHA-256은 `a8bea0b5350730c8d54628b995f04d7aaade2072ba9450972dadbf862e3d98e4`입니다.
표준 S3 백업과 checksum 객체의 실제 head, AES256 저장 암호화, 로컬 archive hash를 확인했습니다.
인증 파일·토큰은 저장소와 백업에 포함하지 않았습니다.

현재 mixed-fleet 일반 자동 code release 보호 guard는 유지됩니다. 신버전 감지·후보 PR 작성은 활성이고,
일반 코드의 자동 운영 cutover는 현행 overlay·버전 계약 보존이 executor에 통합될 때까지 보호됩니다.
운영 release manifest는 `/var/lib/quant-company/config/codex-runtime-release.json`, service별 지속 overlay는
`/var/lib/quant-company/config/codex-upgrade-20261006-NAME.compose.json`입니다. 이미 active인 배포 script를
무작정 재실행하지 않으며 복원 시 갱신 이후의 외부 효과 receipt를 되돌리거나 재생하지 않습니다.

## 증거와 절차

- [실제 운영 검증](evidence/codex-upgrade-20261006/production-verification.json)
- [실제 구독·세션·Quant·웹검색](evidence/codex-upgrade-20261006/actual-subscription-qualification.json)
- [실제 계정의 후보 목록](evidence/codex-upgrade-20261006/candidate-account-catalog.json)
- [immutable 이미지·정확한 source delta](evidence/codex-upgrade-20261006/staged-images.json)
- [배포 영수증](evidence/codex-upgrade-20261006/production-cutover-receipt.json), [백업 확인](evidence/codex-upgrade-20261006/backup-verification.json)
- [검사 범위·CI](evidence/codex-upgrade-20261006/source-validation.json), [실제 신버전 감지 실행](evidence/codex-upgrade-20261006/release-watch-activation.json)
- [갱신 runbook](../runbooks/codex-upgrades.md), [원인 진단](CODEX-UPDATE-DIAGNOSIS-20261006.md)
