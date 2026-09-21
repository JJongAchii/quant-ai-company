---
judge: leak-auditor
target: .
verdict: pass
issued: '2026-09-21'
target_commit: 84d47462b992261b0bb56add6f8134f196f4bf7e
scope:
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
- tests/test_research_report.py
- tests/test_research_runner.py
- tests/test_research_store.py
- tests/test_research_temporal.py
- tests/test_research_worker.py
scope_digest: cf5350b5fffc
objective_digest: 03d58ee449e6
findings: []
conflicts_with: []
---
# 고정 P11 실행·보고 연결의 인과성 감사

## 판정과 정확한 범위

**pass.** 지정된 회사 실행·보고 코드와 두 실제 반환 ZIP에서, 기존 독립 감사에
연결된 고정 경제 산출물을 바꾸거나 미래 정보를 과거 선택에 새로 공급하는 경로를
발견하지 못했다. 이 판정은 고정 재현의 보존성에 한정된다. 새로운 전략의 연구 통과,
확증, 채택 또는 운영·투자 승인을 발급하는 판정이 아니다.

repoRoot는 `/Users/achii/quant-ws/company-research-execution/quant-ai-company`,
target은 `.`이며 요청된 51개 파일을 감사 시작 전에 확정했다. ZIP은 압축을 풀어
디스크에 쓰지 않고 모든 member를 메모리에서 읽었다. `backtest-hygiene`와 그
`references/guards.md`, 이 저장소의 `AGENTS.md`를 적용했다. 회사 영역은 요청에
지정된 personal domain의 독립 서비스이며 다른 회사·전략 코드를 열지 않았다.

`target_commit`은 실제 두 번째 실행과 감사 대상 코드의
`84d47462b992261b0bb56add6f8134f196f4bf7e`다. 감사 중 통합 HEAD는 문서 작업으로
`40d14ab20cb091dd753f270feb87001837083136`까지 진행했지만, 범위의 29개 버전 관리
파일은 `git show 84d47462…:<path>`와 현재 실제 바이트가 모두 일치했다.
나머지 22개는 이번 두 실행의 동결된 증거 파일이다. 51개 전체를 같은
`qlab.audits.compute_scope_digest(target_root, scope)`에 넘긴 실제 결과는
`cf5350b5fffc`로 요청과 일치했다. objective digest는 호출자가 지정한
`03d58ee449e6`다. 기존 판정을 supersede하지 않는다.

## 1. 실제 결과와 기존 감사의 연결

참조 `p11.json`과 포함된 실제 `audit.md`를 파싱하여, 원 감사의 target 기준 scope를
정규화한 193개 경로 집합이 등록된 `scope_files`와 정확히 같음을 확인했다.
원 감사의 verdict, objective digest, scope digest도 참조와 일치했다.

- 고정 코드 commit: `02649715bd3661826253e1ce84f8002d2a74c822`.
- 고정 증거 commit: `b1c813e1e99bcc6fb9f1329562c17ea14e483c7f`.
- 원 감사 scope digest: `e1c51cf90a17`.
- 6개 warmup/dev 입력과 4개 설정·실행 envelope의 SHA가 원 scope map에 포함된다.
- 33개 expected economic output의 SHA가 원 scope의 각
  `output/eval-m1|m2|m3-liquid-11/` 파일 SHA에 각각 연결된다.

두 ZIP은 각각 정확히 37개 member를 포함했다. 33개 경제 파일 전부에 대해 실제
바이트의 SHA-256을 독립 계산해 등록 값과 대조했다. 두 재실행 사이의 33개 경제
파일도 byte-identical이다. 포함된 원 감사·원 감사 receipt는 회사 reference의
바이트와 동일했다. ZIP 내부 execution/audit-verification JSON과 별도로 회수된
같은 이름의 JSON도 의미상 완전히 같았다. 두 audit-verification은 전체 193개
scope hash map, exact evidence commit, 원 감사·receipt SHA, objective/scope digest,
기존 qlab API 이름과 빈 violations/receipt_violations를 모두 보존했다.

| 실제 반환물 | 실행 회사 commit | ZIP SHA-256 |
|---|---|---|
| initial-artifact.zip | ea389bbac6d7cd09749f9d5d595f87819c0f1932 | 53313d225b7eed4a2e7e0a42755f483a2de90b6e2fba5557996997d1b1cabe4c |
| restart-artifact.zip | 84d47462b992261b0bb56add6f8134f196f4bf7e | dcb11fd730a2451980b0bafabb53c5ba74c1527151fbb09dc8f346b59ea684d3 |

이는 현재 HEAD의 검사 통과로 첫 실행을 대신한 확인이 아니다. 범위의 29개 버전
관리 파일을 두 실행 commit 모두에서 `git show`로 읽고 비교했다. 초기 commit은
감사 코드 commit의 실제 조상이어서 Git 객체가 현재 이력으로 도달 가능하다.

## 2. 실행 경로와 버전별 차이

`executor.py:168`의 `verify_original_audit`는 고정 evidence checkout·전체 파일
SHA를 확인하고, 기존 `parse_audit`/`validate_audit`/`check_receipt` API를 실제로
호출한다. 원 목적함수 내용 digest까지 대조한 결과가 등록된 전체 객체와 같아야
다음 단계로 간다. 검증 전후에도 원 scope 파일을 확인한다.

`executor.py:196`의 `stage_checkout`은 고정 science commit의 새 detached
checkout만 사용한다. 허용 입력은 warmup/dev의 6개 파일, 설정은 3개 고정 후보와
고정 실행 envelope다. `verify_runtime`은 연구 Python과 실제 import 경로를
checkout에 묶는다. 이 회사 서비스의 새 경로는 모델·소유자 메시지로부터 날짜,
설정, shell 명령 또는 입력 경로를 받지 않는다.

`executor.py:338`의 후보별 실행은 `qualify` 후 `evaluate` 순서이며, 그 사이에
`verify_qualification`이 code/config/lake/host, nonempty typed data와 미열람
조건을 확인한다. 회수된 12개 command receipt의 명령은 이 고정 명세와 같고 모두
exit 0이다. 환경·실행 receipt의 science/company commit, Python, 3070 identity,
job별 import 경로도 서로 연결된다. 실행 후에는 경제 파일 SHA와 입력·설정·원
scope를 다시 검사한다. 두 회사 commit 사이에서 executor·worker·고정 reference는
바뀌지 않았다.

첫 실행 commit의 실제 `report.py`를 `git show`로 읽어 메모리에서 실행했다.
실제 initial ZIP은 `objective_schema_mismatch`로 차단됐다. 현재 구현은 같은
ZIP을 정상 검증한다. 차이는 `report.py:344`의 `riskConstraintResults` 검사다.
이전 dict 요구를 실제 고정 결과의 세 제약 목록으로 바꿨다. 원 경제 바이트,
목적함수, 입력·설정이나 신호 계산은 바뀌지 않았다. 이 과거 차단은 누수의 증거가
아니며 현재 범위의 finding으로 재발급하지 않는다.

추가된 `ResearchStore.revalidate`는 같은 승인 revision·manifest·수신 ZIP digest를
확인한 뒤 `awaiting_audit`에서 `received`로만 이동한다. 실행 lease·원 실행 company
commit·archive identity를 바꾸거나 새 평가를 제출하지 않는다. `ResearchRunner.tick`
은 저장 ZIP의 SHA를 다시 확인하고 실제 validator를 통과해야 publication 및 회사
source 등록으로 진행한다. 실패 시 수치를 source로 등록하지 않는다.

## 3. 독립 동적 검증

회사 Python 3.12의 `.venv/bin/python -B`에서 실제
`quant_company.research.report.validate_bundle`과 `build_report`를 실행했다.
파일 쓰기·네트워크 연결을 거부하는 Python audit hook을 함께 두었고, 차단 대상
시도는 0회였다. 압축 변형은 `BytesIO`에서만 만들었다.

1. **실제 ZIP 두 개 모두 검증·보고 생성 통과.** 각 실행 receipt에서 job/project/
   revision/approval/manifest를 읽어 Assignment를 만들고, 해당 실행의 회사 commit을
   expected commit으로 사용했다. 비교용 lease는 합성 값이다. 실제 DB lease나 Slack
   인증을 이 메모리 검사가 증명한다고 주장하지 않는다.
2. **총 50개 변조 케이스 차단.** 각 ZIP에서 25개를 실행했다. 경제 CSV/JSON 바이트,
   원 감사·receipt 바이트, audit scope map/digest/objective/evidence commit/verdict,
   qualification·sealed·시행 수·실행 횟수·코드/manifest/company identity, 추가 sealed
   member, 누락 후보, 잘못된 expected company commit, 취소 Assignment를 변경했다.
   모두 실제 validator가 거부했다. 검증을 통과하지 않은 값으로 보고서를 만들지 않았다.
3. **보고 산술 대조.** 모든 후보·두 비용의 일별/월별 달력, 초기자산을 포함한 누적
   최고값과 낙폭, 월말 수준과 월별 수익 산술을 실제 CSV에서 확인했다. 원 objective의
   값과 stress CSV로 계산한 값은 실제 validator의 허용오차 안에서 일치했다.
4. **과거 보고값의 절단·미래 교란.** 실제 6개 시계열 각각에서
   `1, 2, 5, 20, 63, 252, 777, 1553, n-1` 행의 9개 절단점을 사용했다.
   절단 일별 행에서 월말 표를 메모리로 다시 만들고 `_series`를 재계산했다.
   일별 수준·낙폭과 절단점 이전에 끝난 달의 수익은 전체 실행 prefix와 같았다.
   미래 equity를 역순으로 배치하고 `7 / (1 + equity)`로 바꾸는 유효 양수 교란에도
   같은 과거 값이 유지됐다. **54개 절단·교란 쌍이 모두 통과**했다. 진행 중인 달의
   최종 월수익은 과거 불변 비교에 넣지 않았다.

동일 renderer의 메모리 HTML SHA는 initial
`d21ba223ba680c5b73adc59f65c7b030fca945369b3322ee1c0485c043653288`, restart
`e38d1b17cdd79710fc191b0a03ba46000bef5fd12246af97ca753e3c02f86b1c`였다.
이 값은 메모리에서 실제 생성한 HTML의 digest이며, 외부 게시 상태를 대신하지 않는다.

## 4. 가드 적용·시간 창·시행 수

감사한 회사 실행 경로에는 새 피처, 미래수익 label, 유니버스, 학습, CV 분할 또는
정규화 적합을 생성하는 함수가 없다. 고정 연구 CLI를 호출하고 산출물을 보존한다.
현재 source/test의 함수·call AST와 실제 함수 본문을 확인했으며 직접
`assert_causal_*` 호출은 없었다. 이는 피처 생성 경로의 상시 가드 누락이 아니다.
새 시계열 가공은 보고용 `_series`이며 위에서 직접 절단·미래 교란을 수행했다.

`_series`는 일별 누적 peak로 낙폭을 계산하고, 각 달의 마지막 관측을 월말 수준으로
사용한다. 전체기간 CAGR·전체기간 최대낙폭과 SVG 축 범위는 사후 보고 통계다.
이 값들이 과거 목표 종목·주문·가중치로 되먹임되는 경로는 없다. orders/memberships
롱 패널은 등록된 바이트를 검증해 보존할 뿐 회사 코드에서 단면 랭킹이나 행 단위
causal guard를 적용하지 않는다. 따라서 날짜 경계를 가로지르는 직접 가드 사용도 없다.

기존 과학 코드의 전체 피처 가드에 관한 원 감사의 minor는 포함된 원 판정에 보존돼
있다. 그 외부 코드의 새로운 판정을 여기서 발급하거나 같은 finding을 복제하지 않는다.
보고기는 세 후보를 모두 포함하고, 기존 과학 시행 사용량과 추가 0회, 개발기간,
기준선 미측정·초과수익 없음·확증/실거래 주장 없음의 경계를 유지한다.

## 5. 재시작 연결 및 확인하지 않은 것

두 번째 실행은 2026-09-21 01:14:04.419474~01:14:59.842234 UTC였고,
`restart-during-execution.json`의 01:14:04.811123 UTC가 그 안에 있음을 확인했다.
job ID와 재시작 전후 launch ID·PID/PGID·boot/start/command identity가 같으며,
기록된 child alive 값도 전후 true다. 첫 실행의 `worker-restart.json`은 그 실행이
이미 완료된 뒤의 재시작으로 구별했다. 새 science 실행 선택이나 결과 교체의 근거로
사용하지 않았다.

worker는 launch intent·process·terminal receipt를 보존하고, 불확실한 launch를
새 실행으로 자동 재시도하지 않는다. 서버 재검증·업로드·publication retry도 같은
identity를 유지하는 경로임을 코드와 범위 내 테스트 본문에서 확인했다. 이 감사자가
pytest, PostgreSQL/Temporal 테스트나 3070 계산을 재실행한 것은 아니다.

원래 P11의 193개 파일을 이번 감사 범위로 확장해 다시 열지 않았다. 기존 독립 감사와
그 scope에 묶인 참조, 반환된 기존 API verification, 실제 경제 바이트의 일치를 통해
보존성을 확인했다. 각 qualification 원본 JSON은 이번 반환 ZIP에 없으므로 원본을
다시 파싱한 것으로 표현하지 않는다. 정확한 실행 코드의 선행 검사와 회수된 명령·
typed execution receipt 및 결과 동일성으로 연결했다. 봉인/forward 데이터,
새 후보·과학 시행, 원 연구의 새 causal 검증은 수행하지 않았다.

실제 Slack ingress·LLM 응답·운영 활성화는 요청에서 미수행으로 지정됐다. 범위 밖
server/S3 증거와 실제 최종 Slack 전달, 모든 장애의 복구성, 외부 데이터의 최초 공표
빈티지·실거래 체결성·경제적 채택 여부는 이 pass가 인증하지 않는다.

## 판정 파일 검사

아래에는 같은 repoRoot에서 판정 파일 작성 후 실행한 실제 검사 결과를 기록한다.

- `PYTHONDONTWRITEBYTECODE=1 make check-audits`: **exit 2**.
  실제 오류: check-audits 대상 규칙을 찾을 수 없음.
  이 회사 repo에 Makefile/해당 규칙이 없어 전체 make gate가 통과했다고 주장하지 않는다.
- 호출자가 지정한 read-only qlab API 경로와 연구 Python `-B`를 사용한
  `parse_audit(path)` (path는 이 판정의 절대경로)은 성공했고, `validate_audit(record, repoRoot)`는
  **빈 violations 목록 `[]`** 을 반환했다. 51개 실제 scope와 두 digest를 확인했다.
- 새 판정의 사람용 reporting receipt와 `qlab.control verify-audits`는 호출자가
  별도로 생성·검증해야 한다. 이 감사자는 판정 `.md` 하나만 작성했다.
