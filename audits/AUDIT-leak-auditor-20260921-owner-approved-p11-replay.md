---
judge: leak-auditor
target: .
verdict: pass
issued: '2026-09-21'
target_commit: b852356c431c7af2cde93b721e5af82fed8c3655
scope:
- audits/AUDIT-leak-auditor-20260921-research-preparation-recovery.md
- audits/AUDIT-leak-auditor-20260921-research-preparation-recovery.receipt.json
- deploy/research_preparation_recovery.py
- docs/project/RESEARCH-PREPARATION-RECOVERY-20260921.md
- docs/project/evidence/research-execution-20260921/initial-artifact.zip
- docs/project/evidence/research-execution-20260921/initial-audit-verification.json
- docs/project/evidence/research-execution-20260921/initial-environment.json
- docs/project/evidence/research-execution-20260921/initial-m1-evaluate.command.json
- docs/project/evidence/research-execution-20260921/initial-m1-qualify.command.json
- docs/project/evidence/research-execution-20260921/initial-m2-evaluate.command.json
- docs/project/evidence/research-execution-20260921/initial-m2-qualify.command.json
- docs/project/evidence/research-execution-20260921/initial-m3-evaluate.command.json
- docs/project/evidence/research-execution-20260921/initial-m3-qualify.command.json
- docs/project/evidence/research-execution-20260921/initial-receipt.json
- docs/project/evidence/research-execution-20260921/restart-artifact.zip
- docs/project/evidence/research-execution-20260921/restart-audit-verification.json
- docs/project/evidence/research-execution-20260921/restart-during-execution.json
- docs/project/evidence/research-execution-20260921/restart-environment.json
- docs/project/evidence/research-execution-20260921/restart-m1-evaluate.command.json
- docs/project/evidence/research-execution-20260921/restart-m1-qualify.command.json
- docs/project/evidence/research-execution-20260921/restart-m2-evaluate.command.json
- docs/project/evidence/research-execution-20260921/restart-m2-qualify.command.json
- docs/project/evidence/research-execution-20260921/restart-m3-evaluate.command.json
- docs/project/evidence/research-execution-20260921/restart-m3-qualify.command.json
- docs/project/evidence/research-execution-20260921/restart-receipt.json
- docs/project/evidence/research-execution-20260921/worker-restart.json
- docs/project/evidence/research-owner-acceptance-20260921/artifact.zip
- docs/project/evidence/research-owner-acceptance-20260921/audit-verification.json
- docs/project/evidence/research-owner-acceptance-20260921/checkout.command.json
- docs/project/evidence/research-owner-acceptance-20260921/ci-restart-test.json
- docs/project/evidence/research-owner-acceptance-20260921/clone.command.json
- docs/project/evidence/research-owner-acceptance-20260921/environment.command.json
- docs/project/evidence/research-owner-acceptance-20260921/environment.json
- docs/project/evidence/research-owner-acceptance-20260921/m1-evaluate.command.json
- docs/project/evidence/research-owner-acceptance-20260921/m1-qualify.command.json
- docs/project/evidence/research-owner-acceptance-20260921/m2-evaluate.command.json
- docs/project/evidence/research-owner-acceptance-20260921/m2-qualify.command.json
- docs/project/evidence/research-owner-acceptance-20260921/m3-evaluate.command.json
- docs/project/evidence/research-owner-acceptance-20260921/m3-qualify.command.json
- docs/project/evidence/research-owner-acceptance-20260921/original-audit-verification.command.json
- docs/project/evidence/research-owner-acceptance-20260921/process.json
- docs/project/evidence/research-owner-acceptance-20260921/publication-verification.json
- docs/project/evidence/research-owner-acceptance-20260921/published-report.html
- docs/project/evidence/research-owner-acceptance-20260921/receipt.json
- docs/project/evidence/research-owner-acceptance-20260921/renderer-comparison.json
- docs/project/evidence/research-owner-acceptance-20260921/report.html
- docs/project/evidence/research-owner-acceptance-20260921/result.json
- docs/project/evidence/research-owner-acceptance-20260921/validation.json
- docs/project/evidence/research-recovery-20260921/actual-bound-approval.json
- docs/project/evidence/research-recovery-20260921/builder-qualification.json
- docs/project/evidence/research-recovery-20260921/config-pin-repair-20260921.json
- docs/project/evidence/research-recovery-20260921/failed-job-server-observation.json
- docs/project/evidence/research-recovery-20260921/failed-preparation-public.json
- docs/project/evidence/research-recovery-20260921/integration.json
- docs/project/evidence/research-recovery-20260921/pre-recovery-backup-get.json
- docs/project/evidence/research-recovery-20260921/pre-recovery-backup.json
- docs/project/evidence/research-recovery-20260921/production-failure-preflight.json
- docs/project/evidence/research-recovery-20260921/recovery-proof.json
- docs/project/evidence/research-recovery-20260921/recovery-server-resumption.json
- docs/runbooks/research-worker.md
- scripts/accept_research_server.py
- scripts/build_research_reference.py
- src/quant_company/api.py
- src/quant_company/company.py
- src/quant_company/config.py
- src/quant_company/contracts.py
- src/quant_company/db.py
- src/quant_company/research/__init__.py
- src/quant_company/research/api.py
- src/quant_company/research/contracts.py
- src/quant_company/research/executor.py
- src/quant_company/research/recipes.py
- src/quant_company/research/reference/audit.md
- src/quant_company/research/reference/audit.receipt.json
- src/quant_company/research/reference/p11.json
- src/quant_company/research/report.py
- src/quant_company/research/runner.py
- src/quant_company/research/schema.sql
- src/quant_company/research/store.py
- src/quant_company/research/worker.py
- src/quant_company/research/workflow.py
- src/quant_company/runtime.py
- src/quant_company/slack.py
- src/quant_company/task_control.py
- tests/test_research_preparation_recovery.py
- tests/test_research_report.py
- tests/test_research_runner.py
- tests/test_research_store.py
- tests/test_research_temporal.py
- tests/test_research_worker.py
scope_digest: 57fa1670f50a
objective_digest: 03d58ee449e6
findings: []
supersedes:
- audits/AUDIT-leak-auditor-20260921-company-research-replay.md
- audits/AUDIT-leak-auditor-20260921-research-preparation-recovery.md
---

# 실제 owner 승인 P11 재현 — 산출물 보존성 추가 감사

## 판정

**pass.** 지정한 90개 파일에서 이번 실제 재현이 동결된 P11의 입력·명세·경제 산출물과 기존 인과성 검증을 바꾼 근거를 발견하지 못했다. 이 판정은 회사 실행·복구·보고 경로의 보존성에 한정한다. `backtest-hygiene`와 guards 참조, 대상 `AGENTS.md`를 적용했다.

위 `supersedes`는 앞선 두 회사 판정을 승계한다. 원 quant-lab P11 과학 판정은 대체하지 않는다. 기존 도구 검토와 61개 음성 검사는 반복하지 않았다. 최초 대조에서 이전 64개 scope의 digest `ac155f665861`, 이전 판정의 `validate_audit`와 영수증 검사가 모두 유효했다. 최종 변경은 재시작 테스트의 대기시간 10→30초·설명 두 줄과 `ci-restart-test.json` 추가뿐이다. 최종 90개 파일 모두 지정 커밋의 Git 객체와 바이트가 같다.

## 실제 산출물 대조

- 회수 ZIP SHA-256은 `1f84528764ba45f20d90217de11d80465af107d2bbdd24d9bbeae718139c1782`다. 중복 없는 정확한 37개 항목 중 경제 파일 33개의 실제 바이트 SHA가 고정 recipe의 `expected_outputs` 및 원 감사 scope-map의 대응 값과 모두 같다. 입력 6개·설정 4개 해시도 같은 원 scope-map에 묶인다.
- ZIP의 원 감사·영수증은 저장된 reference와 바이트가 같다. 포함된 `audit-verification.json`과 별도 회수본이 같으며, 193개 scope-map 전체·audit/receipt SHA·objective/scope digest·`pass`·빈 위반 목록을 보존한다. 원 audit의 target-relative 경로를 정규화한 집합도 그 193개 경로와 정확히 같다.
- 실제 job `6a64df0e-9c60-555f-aa3a-a80a70e7f985`, project `9aac0de4-2b97-5195-a720-287d324234f3`, revision **5**, 승인 `slack:T0C1YRDRPNF:C0C2B9EUEGM:1789970318.691419:director`, manifest `f0fb9af6a98756438d95b36530a583b01dec771c469d7e72333f4fea8e4ec4b0`가 승인 캡처·실행 receipt·복구 proof 사이에서 일치한다.
- 실제 회사 pin은 `0e89d998022abfb626cf29a4ff99012cd83ffb74`, 과학 pin은 `02649715bd3661826253e1ce84f8002d2a74c822`다. 환경·process·고정 detached checkout·명령 기록과 연결되며, scope 안의 24개 회사 runtime/reference/script 파일은 실제 회사 pin의 바이트와 같다. 10개 명령 기록은 종료 코드 0이며 고정된 세 방법의 qualify/evaluate 인자와 원 감사 API 호출을 보존한다. receipt는 qualification 통과, `sealed_read=false`, `scientific_trials_added=0`을 기록한다.

## 준비 실패와 경제 실행의 구분

공개 실패 캡처의 정확한 8개 파일 해시·승인·lease 해시·sequence 3을 실제 recovery proof와 대조했다. 이전 실패는 `repository-commit-mismatch`이며 연구 명령·출력 부재를 기록한다. 준비 후 `recipe.json`·`state.json` 해시는 실패 당시와 같고, 고친 회사 pin과 새 launch만 실제 실행으로 연결된다.

실제 proof를 고정 helper의 `RecoveryProof`와 `validate_proof`로 검사했다. canonical proof digest `595e4c0446f6a134998f0eb8d704959a80e5543d3b8cb15e39aedb9c911a49dd`, tool commit `125ad96bb8e0734c8a2940f342b9c35f73bfc2cc`, helper SHA가 서버 재개 영수증과 일치한다. 새 launch `615463e253ce47099bf92f4fe9a4cc9e`는 회수한 process·ready 결과와 같다. 증거는 **준비 실패 1회(경제 실행 0회) 후 경제 재현 1회**를 구분하며, 이를 추가 과학 시행이나 새 confirmation으로 세지 않는다.

## 게시 HTML 보존성

로컬 `report.html` SHA는 `48f8ca6999ad8589212efa61fbf8f7d921f6e187009ec5706e04cd332ebd9d76`, 실제 게시 회수본 `published-report.html` SHA는 `73d9d64cccac2bf80b2b96cd39e59c88f847f7c7cd59a6bc0e41191bfd3c70b6`다.

두 HTML의 정확히 세 `<pre>` JSON을 독립 파싱했다. 세 객체 모두 의미가 같고 각각 실제 실행 receipt·고정 recipe/ZIP 출처·원 감사 검증 객체와 일치한다. 같은 pretty-JSON 인코딩에서 두 번째 객체의 `input_files`, `config_files`, `expected_outputs`, `scope_files` 키 순서만 다르다. 그 JSON 내부를 제외한 HTML 전체가 바이트 단위로 같으므로 본문·금융 차트도 같다. ZIP·게시 HTML의 로컬 SHA/크기는 공개 서버/S3 GET 검증 기록과 일치한다.

## 범위와 한계

새 피처·라벨·유니버스·분할·정규화·모델 적합·롱 패널 가드 경로는 추가되지 않았다. 기존 감사와 해시로 묶인 결과의 보존성을 확인했으며 전략 실행, 성과 재계산, 새 절단/교란·음성 검사, P5 과학 판정을 수행하지 않았다.

심판은 Mac에 회수된 scope 파일만 검사했다. 원 연구 레이크와 193개 원본 파일의 원격 재검증, PostgreSQL/Temporal·S3·Slack 직접 접속, 서비스·브라우저 상태 및 최종 Slack 전달은 이번 판정 대상이 아니다. 원격 사실은 명시된 캡처·명령·검증 영수증과 로컬 바이트의 연결까지 확인했다. 재시작 테스트의 실제 PG/Temporal 1 passed(13.62초)는 동봉된 실행 기록이며 심판이 재실행한 결과가 아니다.

## 판정 파일 검사

같은 repoRoot에서 `qlab.audits.compute_scope_digest(root, scope)`로 `57fa1670f50a`를 계산했다. 실제 `parse_audit`는 성공했고 `validate_audit(record, root)`는 `[]`를 반환했다. `make check-audits`도 실행했으나 회사 repo에 해당 target이 없어 exit 2(`No rule to make target check-audits`)였다. HTML 영수증과 `qlab.control` 검증은 호출자가 별도로 수행한다.
