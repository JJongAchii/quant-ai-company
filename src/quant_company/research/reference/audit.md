---
judge: leak-auditor
target: labs/company-kr-etf-pilot
verdict: pass
issued: '2026-09-19'
target_commit: 773c2e42c92873f0051d93fb4ee3e567a74f3731
scope:
- ../../Makefile
- ../../core/src/qlab/audits/__init__.py
- ../../core/src/qlab/audits/__main__.py
- ../../core/src/qlab/audits/cli.py
- ../../core/src/qlab/audits/receipt.py
- ../../core/src/qlab/audits/record.py
- ../../core/src/qlab/control/__init__.py
- ../../core/src/qlab/control/__main__.py
- ../../core/src/qlab/control/cli.py
- ../../core/src/qlab/control/record.py
- ../../core/src/qlab/guards/__init__.py
- ../../core/src/qlab/guards/asof.py
- ../../core/src/qlab/guards/causal.py
- ../../core/src/qlab/guards/panel.py
- ../../core/src/qlab/runner/__init__.py
- ../../core/src/qlab/runner/lake.py
- ../../core/src/qlab/runner/seed.py
- ../../core/src/qlab/runner/snapshot.py
- ../../docs/research/company-kr-etf-pilot/APPROVAL-CANDIDATE-FIRST-20260919.json
- ../../docs/research/company-kr-etf-pilot/C01-worker.log
- ../../docs/research/company-kr-etf-pilot/C02-worker.log
- ../../docs/research/company-kr-etf-pilot/C03-worker.log
- ../../docs/research/company-kr-etf-pilot/DISCOVERY-ARGS-v4-execution.json
- ../../docs/research/company-kr-etf-pilot/DISCOVERY-ARGS-v4.json
- ../../docs/research/company-kr-etf-pilot/P10-EVALUATION.json
- ../../docs/research/company-kr-etf-pilot/P10-MANIFEST.json
- ../../docs/research/company-kr-etf-pilot/P10-OBJECTIVE-TRACE.json
- ../../docs/research/company-kr-etf-pilot/P10-SEARCH-MANIFEST.json
- ../../docs/research/company-kr-etf-pilot/P10-SOURCE-BLOCKER.json
- ../../docs/research/company-kr-etf-pilot/P11-C01-ARTIFACTS.json
- ../../docs/research/company-kr-etf-pilot/P11-C01-DECISION.json
- ../../docs/research/company-kr-etf-pilot/P11-C02-ARTIFACTS.json
- ../../docs/research/company-kr-etf-pilot/P11-C02-DECISION.json
- ../../docs/research/company-kr-etf-pilot/P11-C03-ARTIFACTS.json
- ../../docs/research/company-kr-etf-pilot/P11-C03-DECISION.json
- ../../docs/research/company-kr-etf-pilot/P11-CONTROL-OBJECTIVE.json
- ../../docs/research/company-kr-etf-pilot/P11-D1-CONTROL.json
- ../../docs/research/company-kr-etf-pilot/P11-ENVELOPE-BINDING.json
- ../../docs/research/company-kr-etf-pilot/P11-EVALUATION.json
- ../../docs/research/company-kr-etf-pilot/P11-GATE-RESULTS.json
- ../../docs/research/company-kr-etf-pilot/P11-MANIFEST.json
- ../../docs/research/company-kr-etf-pilot/P11-OBJECTIVE-TRACE.json
- ../../docs/research/company-kr-etf-pilot/P11-PLAN.json
- ../../docs/research/company-kr-etf-pilot/P11-Q12-ARTIFACTS.json
- ../../docs/research/company-kr-etf-pilot/P11-Q12-QUALIFICATION.json
- ../../docs/research/company-kr-etf-pilot/P11-Q13-ARTIFACTS.json
- ../../docs/research/company-kr-etf-pilot/P11-Q13-QUALIFICATION.json
- ../../docs/research/company-kr-etf-pilot/P11-Q14-ARTIFACTS.json
- ../../docs/research/company-kr-etf-pilot/P11-Q14-QUALIFICATION.json
- ../../docs/research/company-kr-etf-pilot/P11-REPORT-BUNDLE.json
- ../../docs/research/company-kr-etf-pilot/P11-REPORT-INPUTS.json
- ../../docs/research/company-kr-etf-pilot/P11-SEARCH-MANIFEST.json
- ../../docs/research/company-kr-etf-pilot/P11-SUMMARY.json
- ../../docs/research/company-kr-etf-pilot/P11-guard-worker.log
- ../../docs/research/company-kr-etf-pilot/P11-optional-validator-probe.log
- ../../docs/research/company-kr-etf-pilot/P11-package-pointer-failure.log
- ../../docs/research/company-kr-etf-pilot/P11-transport.log
- ../../docs/research/company-kr-etf-pilot/P11-trials/C01/ARTIFACTS.json
- ../../docs/research/company-kr-etf-pilot/P11-trials/C01/INTERPRETATION.json
- ../../docs/research/company-kr-etf-pilot/P11-trials/C01/daily-base.csv
- ../../docs/research/company-kr-etf-pilot/P11-trials/C01/daily-stress.csv
- ../../docs/research/company-kr-etf-pilot/P11-trials/C01/memberships-base.json
- ../../docs/research/company-kr-etf-pilot/P11-trials/C01/memberships-stress.json
- ../../docs/research/company-kr-etf-pilot/P11-trials/C01/monthly-base.csv
- ../../docs/research/company-kr-etf-pilot/P11-trials/C01/monthly-stress.csv
- ../../docs/research/company-kr-etf-pilot/P11-trials/C01/objective.json
- ../../docs/research/company-kr-etf-pilot/P11-trials/C01/orders-base.json
- ../../docs/research/company-kr-etf-pilot/P11-trials/C01/orders-stress.json
- ../../docs/research/company-kr-etf-pilot/P11-trials/C01/result-summary.json
- ../../docs/research/company-kr-etf-pilot/P11-trials/C01/run_meta.yaml
- ../../docs/research/company-kr-etf-pilot/P11-trials/C01/start.json
- ../../docs/research/company-kr-etf-pilot/P11-trials/C01/terminal-events-base.json
- ../../docs/research/company-kr-etf-pilot/P11-trials/C01/terminal-events-stress.json
- ../../docs/research/company-kr-etf-pilot/P11-trials/C02/ARTIFACTS.json
- ../../docs/research/company-kr-etf-pilot/P11-trials/C02/INTERPRETATION.json
- ../../docs/research/company-kr-etf-pilot/P11-trials/C02/daily-base.csv
- ../../docs/research/company-kr-etf-pilot/P11-trials/C02/daily-stress.csv
- ../../docs/research/company-kr-etf-pilot/P11-trials/C02/memberships-base.json
- ../../docs/research/company-kr-etf-pilot/P11-trials/C02/memberships-stress.json
- ../../docs/research/company-kr-etf-pilot/P11-trials/C02/monthly-base.csv
- ../../docs/research/company-kr-etf-pilot/P11-trials/C02/monthly-stress.csv
- ../../docs/research/company-kr-etf-pilot/P11-trials/C02/objective.json
- ../../docs/research/company-kr-etf-pilot/P11-trials/C02/orders-base.json
- ../../docs/research/company-kr-etf-pilot/P11-trials/C02/orders-stress.json
- ../../docs/research/company-kr-etf-pilot/P11-trials/C02/result-summary.json
- ../../docs/research/company-kr-etf-pilot/P11-trials/C02/run_meta.yaml
- ../../docs/research/company-kr-etf-pilot/P11-trials/C02/start.json
- ../../docs/research/company-kr-etf-pilot/P11-trials/C02/terminal-events-base.json
- ../../docs/research/company-kr-etf-pilot/P11-trials/C02/terminal-events-stress.json
- ../../docs/research/company-kr-etf-pilot/P11-trials/C03/ARTIFACTS.json
- ../../docs/research/company-kr-etf-pilot/P11-trials/C03/INTERPRETATION.json
- ../../docs/research/company-kr-etf-pilot/P11-trials/C03/daily-base.csv
- ../../docs/research/company-kr-etf-pilot/P11-trials/C03/daily-stress.csv
- ../../docs/research/company-kr-etf-pilot/P11-trials/C03/memberships-base.json
- ../../docs/research/company-kr-etf-pilot/P11-trials/C03/memberships-stress.json
- ../../docs/research/company-kr-etf-pilot/P11-trials/C03/monthly-base.csv
- ../../docs/research/company-kr-etf-pilot/P11-trials/C03/monthly-stress.csv
- ../../docs/research/company-kr-etf-pilot/P11-trials/C03/objective.json
- ../../docs/research/company-kr-etf-pilot/P11-trials/C03/orders-base.json
- ../../docs/research/company-kr-etf-pilot/P11-trials/C03/orders-stress.json
- ../../docs/research/company-kr-etf-pilot/P11-trials/C03/result-summary.json
- ../../docs/research/company-kr-etf-pilot/P11-trials/C03/run_meta.yaml
- ../../docs/research/company-kr-etf-pilot/P11-trials/C03/start.json
- ../../docs/research/company-kr-etf-pilot/P11-trials/C03/terminal-events-base.json
- ../../docs/research/company-kr-etf-pilot/P11-trials/C03/terminal-events-stress.json
- ../../docs/research/company-kr-etf-pilot/Q12-worker.log
- ../../docs/research/company-kr-etf-pilot/Q13-worker.log
- ../../docs/research/company-kr-etf-pilot/Q14-worker.log
- ../../docs/research/company-kr-etf-pilot/RESEARCH-BRIEF-v4.md
- ../../docs/research/company-kr-etf-pilot/SEARCH.jsonl
- configs/m1-liquid-11.json
- configs/m2-liquid-11.json
- configs/m3-liquid-11.json
- data/snapshot-20260918/dev/meta.parquet
- data/snapshot-20260918/dev/prices.parquet
- data/snapshot-20260918/dev/snapshot.json
- data/snapshot-20260918/warmup/meta.parquet
- data/snapshot-20260918/warmup/prices.parquet
- data/snapshot-20260918/warmup/snapshot.json
- output/eval-m1-liquid-11/daily-base.csv
- output/eval-m1-liquid-11/daily-stress.csv
- output/eval-m1-liquid-11/memberships-base.json
- output/eval-m1-liquid-11/memberships-stress.json
- output/eval-m1-liquid-11/monthly-base.csv
- output/eval-m1-liquid-11/monthly-stress.csv
- output/eval-m1-liquid-11/objective.json
- output/eval-m1-liquid-11/orders-base.json
- output/eval-m1-liquid-11/orders-stress.json
- output/eval-m1-liquid-11/result.json
- output/eval-m1-liquid-11/run_meta.yaml
- output/eval-m1-liquid-11/start.json
- output/eval-m1-liquid-11/terminal-events-base.json
- output/eval-m1-liquid-11/terminal-events-stress.json
- output/eval-m2-liquid-11/daily-base.csv
- output/eval-m2-liquid-11/daily-stress.csv
- output/eval-m2-liquid-11/memberships-base.json
- output/eval-m2-liquid-11/memberships-stress.json
- output/eval-m2-liquid-11/monthly-base.csv
- output/eval-m2-liquid-11/monthly-stress.csv
- output/eval-m2-liquid-11/objective.json
- output/eval-m2-liquid-11/orders-base.json
- output/eval-m2-liquid-11/orders-stress.json
- output/eval-m2-liquid-11/result.json
- output/eval-m2-liquid-11/run_meta.yaml
- output/eval-m2-liquid-11/start.json
- output/eval-m2-liquid-11/terminal-events-base.json
- output/eval-m2-liquid-11/terminal-events-stress.json
- output/eval-m3-liquid-11/daily-base.csv
- output/eval-m3-liquid-11/daily-stress.csv
- output/eval-m3-liquid-11/memberships-base.json
- output/eval-m3-liquid-11/memberships-stress.json
- output/eval-m3-liquid-11/monthly-base.csv
- output/eval-m3-liquid-11/monthly-stress.csv
- output/eval-m3-liquid-11/objective.json
- output/eval-m3-liquid-11/orders-base.json
- output/eval-m3-liquid-11/orders-stress.json
- output/eval-m3-liquid-11/result.json
- output/eval-m3-liquid-11/run_meta.yaml
- output/eval-m3-liquid-11/start.json
- output/eval-m3-liquid-11/terminal-events-base.json
- output/eval-m3-liquid-11/terminal-events-stress.json
- output/p11-orchestration/P11-RESEARCH-RESULT.json
- output/q-m1-liquid-11/feature-schema.parquet
- output/q-m1-liquid-11/qualification.json
- output/q-m1-liquid-11/run_meta.yaml
- output/q-m1-liquid-11/start.json
- output/q-m2-liquid-11/feature-schema.parquet
- output/q-m2-liquid-11/qualification.json
- output/q-m2-liquid-11/run_meta.yaml
- output/q-m2-liquid-11/start.json
- output/q-m3-liquid-11/feature-schema.parquet
- output/q-m3-liquid-11/qualification.json
- output/q-m3-liquid-11/run_meta.yaml
- output/q-m3-liquid-11/start.json
- pyproject.toml
- run.py
- scripts/build_discovery_report.py
- scripts/discovery-report.css
- src/company_kr_etf_pilot/__init__.py
- src/company_kr_etf_pilot/cli.py
- src/company_kr_etf_pilot/engine.py
- src/company_kr_etf_pilot/terminal_accounting.py
- src/company_kr_etf_pilot/terminal_adapter.py
- src/company_kr_etf_pilot/terminal_inputs.py
- src/company_kr_etf_pilot/terminal_review.py
- src/company_kr_etf_pilot/terminal_sources.py
- tests/test_pilot.py
- tests/test_report.py
- tests/test_terminal_accounting.py
- tests/test_terminal_adapter.py
- tests/test_terminal_collectors.py
- tests/test_terminal_inputs.py
- ../../uv.lock
scope_digest: e1c51cf90a17
objective_digest: 03d58ee449e6
findings:
- severity: minor
  location: tests/test_pilot.py:69
  claim: 상시 as-of 가드는 targets 출력만 비교하여 최종 선택을 바꾸지 않는 전체 피처값 및 adv20 변화를 직접 검사하지 않는다. 이전 독립 감사자가 수행한 전체 피처 synthetic
    13개 및 frozen 11개 cutoff 절단·교란 통과 증거를, 현재 과학 코드·입력·결과가 byte-identical임을 재확인하여 유지한다.
  frequency: 현재 피처 빌더 1개(features_asof)의 전체 반환값 경로; 세 후보 공유
conflicts_with: []
supersedes:
- audits/AUDIT-leak-auditor-20260919-company-kr-etf-pilot-p11-discovery.md
---
# P11 보고서 감사 경로 수정 후 인과성 재판정

## 판정

**pass.** 보고서가 control receipt의 target 기준 감사 경로를 올바르게 해석하도록
수정됐으며 현재 평가 package에 새 인과성 위반은 없다. 기존 연구 코드·입력·결과의
동일성을 직접 확인하여 이전 독립 검증 근거를 현재 package에 연결했다.

대상은 `labs/company-kr-etf-pilot`, 작업 repoRoot는
`/Users/achii/quant-ws/company-kr-etf-pilot/quant-lab`이다. 같은 193개 scope를 먼저
확정했다. 이 세션에서 앞서 적용한 `backtest-hygiene`와 대상 `CLAUDE.md` 불변식을
계속 적용했다. 판정 파일 외에는 쓰지 않았다.

이 판정은 `audits/AUDIT-leak-auditor-20260919-company-kr-etf-pilot-p11-discovery.md`를
supersedes한다. 이전 파일은 수정하지 않았다. 이전 판정 후 사람이 읽는 보고서를
만드는 과정에서 발견된 경로 계약 결함을 검토한 것이며, 이전 연구를 다시 실행하거나
다른 과거 finding을 재심한 것이 아니다.

## 변경 범위와 현재 연결

- 이전 감사 입력 HEAD: `36ba6ec1d5c7ca84eaba029122f066012886ff56`.
- 수정 직전 메인 HEAD: `41dd428b18c76c2720825729af384ca94e01f7e8`.
- 현재 HEAD: `773c2e42c92873f0051d93fb4ee3e567a74f3731`.
- 실제 세 후보/Q12~14의 실행 커밋은 여전히
  `02649715bd3661826253e1ce84f8002d2a74c822`다.
- 앞선 scope digest `f897c405c3d8`은 보고 스크립트 변경으로 현재 상태에 적용되지 않는다.
  위 새 digest는 현재 193개 실제 파일을 대상으로 `qlab.audits.compute_scope_digest`로
  다시 계산했다. 이전 digest를 손으로 고치거나 복사하지 않았다.

`git diff 41dd428b..HEAD`를 확인했다. scope 안의 변경은 다음 세 파일뿐이다.

1. `scripts/build_discovery_report.py:159`: 감사 파일 해석을
   `root / external['auditPaths'][0]`에서
   `root / args['target'] / external['auditPaths'][0]`로 변경.
2. `tests/test_report.py:89`: target 기준 감사 경로를 가진 control receipt의
   synthetic 회귀검사 추가. 파일을 찾은 뒤 실제 report scope 검사를 계속 수행함을 검사한다.
3. `../../docs/research/company-kr-etf-pilot/P11-REPORT-BUNDLE.json`: 위 두 파일의 SHA만 갱신.

audit request는 새 판정 경로·supersedes 및 이유를 지정하는 용도로 읽었다.
별도 경위 receipt는 성과 계산이나 report 입력이 아니므로 내용 판정에 사용하지 않았다.

## 독립 검증

### 1. 과학 증거 동일성

이전 감사 시점 커밋의 `P11-AUDIT-REQUEST.json` 및 `P11-REPORT-BUNDLE.json`을
`git show`로 읽고 현재 목록/내용과 대조했다.

- `scopeTargetRelative` 193개와 objective digest `03d58ee449e6`가 동일하다.
- bundle의 input 경로 집합 192개가 동일하고, 현재 각 파일의 SHA-256이 전부 일치한다.
- 두 보고 파일을 제외한 **190개 파일의 해시가 이전 감사 시점과 동일**하다.
- bundle의 다른 필드도 동일하다. 따라서 연구 설정·신호/유니버스/회계 코드·가드,
  frozen warmup/dev 입력, SEARCH, decision, qualification, raw/compact 결과·objective·
  daily/monthly CSV·orders/memberships와 보고 입력의 scientific 값은 변경되지 않았다.

이 확인은 호출자의 “동일함” 주장에 의존하지 않고 실제 파일 바이트와 이전 Git 객체에
대해 수행했다. 과학 입력이 그대로이므로 동일 검사 전체를 반복하지 않았다.

### 2. 실제 control 계약과 보고서의 일치

`../../core/src/qlab/control/record.py:587`의 `_verify_audits`는
`target_root = repo_root / manifest['target']`를 만든 뒤
`_inside(target_root, rel, kind='audit')`로 경로를 해석한다.
따라서 receipt의 `auditPaths` 항목은 `audits/AUDIT-....md`처럼 target 기준이다.

수정된 보고서 `build()` 함수 자체를 AST로 읽어 **메모리에서 실행**했다.
파일/디렉터리를 만들지 않도록 read/parse/output 경계를 메모리 fixture로 대체하고,
검증된 pass 형태의 target 기준 receipt를 넣었다. 결과는 다음과 같다.

- parse에 전달된 경로가 실제
  `repoRoot/labs/company-kr-etf-pilot/audits/AUDIT-leak-auditor-20260919-company-kr-etf-pilot-p11-discovery.md`
  였다. 그 경로의 파일이 실재함을 확인했다.
- 같은 scoped control 모듈의 실제 `_inside` 함수 본문을 메모리에서 실행한 결과와
  완전히 같은 resolved path였다.
- scope를 비운 fixture에서는 정확히
  `Report input is outside the actual independent audit scope`로 차단됐다.
  경로 수정이 기존 감사 범위 검사를 우회하지 않는다.
- 디스크 쓰기는 0회였다. 호출자가 보고한 pytest 4 passed를 이 독립 실행의 결과로
  가장하지 않았으며, 파일 쓰기가 필요한 pytest suite를 감사자 권한으로 재실행하지 않았다.

보고서의 disclosure 조건, objective/scope digest 검증, bundle SHA 검증 및 수치 계산에는
변경이 없다. 이 수정은 과거 신호나 가중치에 새 정보를 공급하지 않는다.

### 3. 현재 동일한 입력에 연결되는 선행 독립 검사

이전 판정에서 이 감사자가 실제 수행한 검사의 핵심은 아래와 같다. 이번에는 위의
byte 동일성 확인을 통해 같은 causal path에 적용됨을 확인했다.

- 실행 커밋의 synthetic 테스트 **26케이스** 통과.
- 전체 피처 및 M1/M2/M3 targets에 대해 synthetic **13 cutoff**, frozen 실제 입력
  **11 cutoff**에서 qlab as-of 절단 재계산·미래 교란 통과.
- 원시 date/ticker 패널의 날짜 경계를 보존한 절단과 유효 미래 가격/유동성/분류 변경:
  **4 cutoff × 3 methods**의 과거 선택 불변성 확인.
- 실제 **152개 월별 신호일**, 세 후보 각각 **760개 membership 행** 재계산 일치.
- 여섯 비용 경로 **6,138개 주문**, 각 경로의 **3,107개 개발 세션**, 월말 수준과
  저장 지표의 산술 대조 일치. 실제 시가 0 nofill과 신호일 다음 첫 관측일 체결도 확인.
- Q12→C01, Q13→C02, Q14→C03의 exact commit/config/lake/3070 identity,
  qualification 선행 순서, SEARCH decision→실행→해석 및 이전 결과 근거 연결 확인.
- baseline은 미측정으로 유지했고 P05 보강 terminal 입력은 null임을 확인.

피처/선택을 만드는 함수는 여전히 `features_asof`와 `targets`이며 원시 패널 구성은
`make_panel`이다. 테스트는 `assert_causal_asof`를 사용한다. long panel은 날짜별 wide
수치 프레임으로 변환한 후 검사하므로 행 중간에 걸리는 직접 qlab 가드 사용은 없다.
학습·미래수익 label·CV split·전기간 정규화 적합 경로는 없다.

현재 코드에는 T까지의 253개 가격, T-21 형성 종료, 63개 수익 변동성 및 T를 포함한
20개 유효 거래대금 관측으로 공통 pool을 정한 뒤 단면 순위를 계산하는 경로가 그대로다.
익일 시가는 과거 선택을 바꾸지 않으며 opening fill과 이후 EOD 평가는 분리돼 있다.

## 현재 minor와 한계

F1(`tests/test_pilot.py:69`)은 현재 코드에서도 남아 있는 전체 피처 반환값의 상시 가드
공백이다. targets만 비교하는 테스트는 선택을 보존하는 피처 크기나 adv20 변경을
직접 관측하지 못한다. 선행 감사의 전체 피처 절단/교란 검사로 실제 값의 인과성을 확인했고
그 경로가 byte-identical이므로 pass를 유지한다. 이 공백을 누수라고 판정하지 않는다.

이 pass는 동결된 일별 입력을 소비한 현재 개발 평가 package의 인과성에 한정된다.
원 수집기의 모든 역사 빈티지·행별 최초 공표 시각, 독립 거래소 달력의 완전성은
인증하지 않는다. baseline·초과성과·알파·실거래 체결성·배치 적합성도 판정 대상이 아니다.
봉인 구간과 원격 worker를 열지 않았으며 추가 연구를 실행하지 않았다.

최종 HTML·외부 발행 상태는 범위 밖이다. 새 판정의 사람용 HTML receipt 및
`qlab.control verify-audits`는 호출자가 정확한 target 기준 경로로 별도 실행해야 한다.

## 판정 후 동일 repoRoot의 게이트 검사

`PYTHONDONTWRITEBYTECODE=1 UV_NO_SYNC=true make check-audits`를 실제 실행했다.
결과는 exit 2이며 `판정 아티팩트 79 건 검사 (유효 19, 은퇴 60)`가 출력됐다.

- 새 파일의 이름·frontmatter·scope digest 검증은 통과했다.
  `parse_audit`와 `validate_audit` 직접 검사도 통과했다.
- 이전 P11 판정은 supersedes로 은퇴 처리됐다. 이전 파일의 stale scope를
  새 판정의 통과 근거로 사용하거나 과거 digest를 수정하지 않았다.
- 새 판정의 현재 게이트 실패는 사람용 HTML receipt 미생성 1건이다.
  `AUDIT-leak-auditor-20260919-company-kr-etf-pilot-p11-report-path.receipt.json`를 호출자가 기존 reporting 경로로 생성해야 한다.
  감사자는 판정 .md 하나만 작성했고 receipt를 생성하거나 꾸미지 않았다.
- 현재 package 밖의 기존 판정에서 scope 입력 파일 부재 5건이 남아 있다.
  이 오류들을 새 과학적 finding으로 재발급하지 않았다.
- frozen raw 입력/회수 산출물을 scope에 포함해 이식성 경고는 유지된다.
  다른 checkout에서는 동일 해시의 artifact를 회수해야 한다.

따라서 현재 인과성 판정과 새 파일 자체의 형식·scope 검증은 pass지만,
레포 전체 gate가 통과한 상태라고 주장하지 않는다. 별도의 새 control receipt가 필요하다.
