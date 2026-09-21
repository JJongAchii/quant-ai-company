---
judge: leak-auditor
target: .
verdict: pass
issued: '2026-09-21'
target_commit: 125ad96bb8e0734c8a2940f342b9c35f73bfc2cc
scope:
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
- docs/project/evidence/research-recovery-20260921/actual-bound-approval.json
- docs/project/evidence/research-recovery-20260921/builder-qualification.json
- docs/project/evidence/research-recovery-20260921/config-pin-repair-20260921.json
- docs/project/evidence/research-recovery-20260921/failed-job-server-observation.json
- docs/project/evidence/research-recovery-20260921/failed-preparation-public.json
- docs/project/evidence/research-recovery-20260921/integration.json
- docs/project/evidence/research-recovery-20260921/pre-recovery-backup-get.json
- docs/project/evidence/research-recovery-20260921/pre-recovery-backup.json
- docs/project/evidence/research-recovery-20260921/production-failure-preflight.json
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
scope_digest: ac155f665861
objective_digest: 03d58ee449e6
findings: []
conflicts_with: []
---
# P11 준비 실패 복구의 인과성 감사

## 판정·범위

**pass.** 연구 명령 전에 종료된 company commit 사전검사 실패를 복구하는 도구가
동결 P11 인과성 경로를 보존한다. 새 전략·성과·확증·배치 판정이나 실제 운영 복구
완료 인증은 아니다. 이전 고정 재현 판정을 supersede하지 않는다.

최종 요청의 64개 scope를 확정한 뒤 판정했다. `backtest-hygiene`와 그 guards 참조,
대상 `AGENTS.md`를 적용했다. repoRoot는
`/Users/achii/quant-ws/company-research-execution/quant-ai-company`, target은 `.`이다.
64개 실제 파일은 모두 `git show 125ad96bb8e0734c8a2940f342b9c35f73bfc2cc:<path>`와
일치했다. 같은 qlab scope 함수가 계산한 digest는 `ac155f665861`이다.

기존 51개 실행·보고 범위도 다시 계산한 digest가 `cf5350b5fffc`로 같았다.
worker/executor/store/contracts/recipe/reference는 실제 runtime pin
`0e89d998022abfb626cf29a4ff99012cd83ffb74`에서도 종전 감사 코드와 byte-identical이다.
따라서 기존 신호·입력·기간·비용·후보·expected economic hashes와 검증 경로를 변경하지
않는다. 이전 P11 전체 감사를 다시 발급하지 않았다.

## 핵심 근거

공개 승인·서버 관찰·실패 preflight·config 수정 자료의 job
`6a64df0e-9c60-555f-aa3a-a80a70e7f985`, revision 5, manifest, 원 승인,
failed launch `942d7e7c58694b8eb85172d5be20decf`, failed sequence 3과 8파일 hash map이
서로 연결된다. 빈 log/lock, checkout·command·경제 output 없음, dead process/group과
company pin 하나의 수정이 기록돼 있다. 호스트 간 wall-clock 차이로 순서를 추정하지 않았다.

runtime `executor.py:329`의 회사 repository 검사는 원 감사·checkout·qualification·
경제 명령보다 앞에 있다. 공개 실패 이유 `repository-commit-mismatch`는 그 경로와
일관된다. 원시 lease-bearing 8파일과 원격 process/Slack/DB는 감사자가 다시 열지 않았다.
공개 증거의 연결성과 실제 적용 시 아래 검사가 요구되는지를 심사했다.

- **`deploy/research_preparation_recovery.py:147`**: 정확한 8파일과 SHA, 빈 log/lock,
  정규 파일만 허용한다. directory·symlink·추가/unknown output, 불명확한 실패, 바뀐
  assignment/launch/heartbeat/ack, 살아 있거나 재사용된 PID·남은 group을 거부한다.
  company pin 외 설정 변경도 거부하며 corrected checkout과 전체 등록 recipe를 확인한다.
- **같은 파일 205행**: worker lock을 잡고 원본 디렉터리와 검증된 복사본을 보존한다.
  prepared 4파일에 기존 state/approval/lease/sequence·recipe를 복사한다. config pin과
  technical launch ID만 바꾸며 직접 spawn하지 않는다. 완료된 같은 recovery는 기존
  proof만 반환하고 다른 recovery·미완료 journal은 차단한다. 설치 후에만 proof를 발급한다.
- **같은 파일 314행**: proof/actor/tool, active owner/revision, 승인·manifest·company pin,
  lease/assignment hash, sequence·failed update를 확인한다. artifact/report 또는 다른
  unresolved job이 있으면 거부한다. scheduling state와 notification marker만 변경하고
  실패·승인·lease·sequence를 보존하는 event를 남긴다. 재호출은 진행된 state를 되돌리지 않는다.

실제 재실행은 기존 Worker의 준비 heartbeat와 서버 `running` acknowledgement를 거친다.
기존 원 감사·qualification·경제 SHA 검사를 우회하는 새 평가 경로는 없다.

## 독립 검사 및 qualification 연결

회사 `.venv/bin/python -B`에서 실제 helper의 `tree_files`, `validate_failure`,
`validate_proof`, `resume_server`를 실행했다. 파일/proc/git/host/DB 경계는 메모리
fixture로 대체하고 fingerprint는 scoped company의 실제 정의를 사용했다.
디스크 쓰기·네트워크·subprocess를 거부하는 audit hook에서 해당 시도는 0회였다.
실제 PostgreSQL·프로세스·filesystem crash 시험을 감사자가 수행한 것은 아니다.

| 독립 검사 | 실제 결과 |
|---|---|
| 정상 로컬 실패·proof fixture | 통과 |
| 추가 파일/비정규 객체, 변경된 identity/config/failure/proof, 살아 있는 process/group 등 | 음성 40건 모두 차단 |
| 정상 서버 복구 후 동일 recovery 재호출 | 승인·lease·sequence 보존, 진행된 running/sequence 4 유지, 추가 mutation/event 없음 |
| 바뀐 승인·revision·lease·sequence·manifest·pin, artifact/report, 다른 job/recovery 등 | 음성 21건 모두 차단 |

builder qualification은 실제 disposable PostgreSQL과 detached **비성과 fixture**의
producer→consumer 검증을 기록한다. 그 code/proof commit은
`bfce16d01113f79273c671dc1a16f6b0f57dbd7e`로 같고, captured input SHA는 scoped
`production-failure-preflight.json`의 실제 SHA와 일치했다. helper/test/runbook 세 파일을
builder `bfce16d…`, integration `615c373…`, target `125ad96…`에서 각각 읽어 동일성과
`integration.json`의 SHA를 확인했다. builder의 전체 pytest를 감사자가 재실행했다고
주장하지 않는다.

새 helper에 피처·label·universe·분할·모델 적합·정규화·수익/비용 계산 함수는 없다.
hash는 증거 identity이며 투자 신호가 아니다. 새 long-panel guard 경로도 없다.
따라서 qlab causal guard 미호출을 피처 검증 공백으로 기록할 대상은 없다.
준비 실패 1회와 그 시점 경제 실행 0회는 proof/event로 구분되며 성과에 따른 재시도
조건이나 과학 시행 기록을 삭제하는 경로는 없다.

## 운영 적용 한계

Git 없는 container의 직접 함수 호출은 runbook의 host clean tool commit·helper SHA와
copied bytes 확인을 전제로 한다. helper SHA는
`e67a7acf0069b29ea8da2839807ffb11587728621a5838733cffb18e76f089b2`다.
이는 CLI의 Git identity 확인만 대체하며 proof/row 검사는 유지한다. 실제 attestation,
polling 중지, prepare/resume 적용, 이후 경제 결과·보고·Slack 전달은 root가 별도로
확인해야 한다. 이 pass는 그 완료를 선인증하지 않는다. 감사자는 SSH·운영 실행·코드
수정·추가 연구를 하지 않았고 이 판정 파일만 썼다.

## 판정 파일 검사

같은 repoRoot에서 작성 후 `PYTHONDONTWRITEBYTECODE=1 make check-audits`를 실행했다.
**exit 2: check-audits 대상 규칙 없음.** 이 회사 repo에는 Makefile/해당 규칙이 없어
전체 make gate가 통과했다고 주장하지 않는다.

호출자가 지정한 read-only qlab API로 정확한 새 파일을 `parse_audit`한 결과는 성공,
`validate_audit(record, repoRoot)` 결과는 **`[]`** 였다. objective digest는
`03d58ee449e6`다. HTML/reporting receipt와 `qlab.control verify-audits`는 root가 별도
생성·검증해야 하며, 감사자는 receipt 파일을 만들지 않았다.
