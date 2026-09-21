---
judge: leak-auditor
target: .
verdict: pass
issued: 2026-09-21
target_commit: f6a50e58a5eed6f4388f99556ef931c32f48533e
scope:
  - src/quant_company/research/adaptive_contracts.py
  - src/quant_company/research/adaptive_executor.py
  - src/quant_company/research/adaptive_report.py
  - src/quant_company/research/audit.py
  - src/quant_company/research/builds.py
  - src/quant_company/research/controller.py
  - src/quant_company/research/mission_backend.py
  - src/quant_company/research/mission_contracts.py
  - src/quant_company/research/missions.py
  - src/quant_company/research/releases.py
  - src/quant_company/research/sandbox.py
  - src/quant_company/research/worker.py
  - src/quant_company/research/workspace.py
  - scripts/prepare_research_runtime.py
  - tests/test_research_adaptive_report.py
  - tests/test_research_audit.py
  - tests/test_research_builds.py
  - tests/test_research_controller.py
  - tests/test_research_mission_backend.py
  - tests/test_research_missions.py
  - tests/test_research_releases.py
  - tests/test_research_sandbox.py
  - tests/test_research_workspace.py
  - tests/test_research_runtime_preparation.py
  - tests/test_research_adaptive_linux.py
  - tests/test_research_etf_linux.py
  - docs/project/evidence/autonomous-research-20260921/3070-protocol.json
  - docs/project/evidence/autonomous-research-20260921/3070-protocol-fixture.zip
scope_digest: da1a7573fba3
objective_digest: 03d58ee449e6
findings: []
---

# Adaptive 실행·보고 경로의 공학적 인과성 감사

## 판정과 범위

위 28개 파일의 승인 명세 → 격리 실행 → 산출물 검증 → 독립 감사 → 공개 경로에서 인과성 위반을 발견하지 않았다. 승인 설계 식별자는 `fcec7879dbf7`이다. 합성 fixture의 실행 계약과 검증 경계를 판정했으며, 실제 금융 성과·후보 채택·confirmation·paper/live 운용은 판정하지 않았다. 기존 P11 및 ETF 수식 감사의 수명주기는 변경하지 않는다.

최초 검토 pin `4804aa6a172e2d7e21823d010e8d4c08e4bcc148`과 최종 pin 사이의 scope 차이는 `test_research_etf_linux.py:48`의 Git bundle 참조를 `HEAD`로 바꾸고 동일 HEAD를 확인하는 단언을 추가한 것이다. 28개 파일의 현재 바이트가 최종 commit blob과 모두 일치함을 직접 대조했다. 요청에 있던 `tests/test_research_adaptive_contracts.py`는 존재하지 않아 제외했다.

`backtest-hygiene` 및 guards 참조와 회사 `AGENTS.md`를 적용했다. 허용된 store/executor/report/company/task-control/DB 경로, `ReportPublisher`, `finance_sources.prefetch`와 source 등록 경계, qlab 감사 API, 이전 ETF 판정 및 objective는 연결 확인용 read context이다. 이들은 위 scope의 별도 판정 대상이 아니다.

## 확인한 인과성 경로

- 명세·계획·프로파일은 개발/봉인 구간, 입력 SHA, 코드·설정·회사 pin에 묶인다. 변경은 허용 경로의 정확한 원문을 대상으로 하며 보호 evaluator를 변경할 수 없다. qualification에는 별도 warmup 입력 집합만 제공하고, executor가 표본 수·날짜/시간·형식과 개발 시작 이전 여부를 확인한 뒤 evaluation을 실행한다.
- sandbox는 명시된 코드 파일과 입력 파일만 읽기 전용으로 mount한다. `.git`·이전 결과·호스트 home·환경·network를 전달하지 않으며, 출력 외 쓰기 mount가 없다. 실행 뒤 코드·입력 해시를 재확인한다. runtime 준비기는 고정한 Python/패키지/ELF 의존성을 복사하고 `.pth`·인증 디렉터리·범위 밖 symlink를 배제한다.
- `adaptive_report.py:140`은 날짜별 base/stress 수익률에서 자산·낙폭·월별 분포·달력 기준 CAGR을 재계산한다. 최초 수익률 0, 개발구간, 날짜 순서, 동일 달력, 표본 수와 primary/MDD 일치를 검사한다. 파산과 관측 없는 달을 제거하거나 평탄한 수익률로 바꾸지 않는다. 이 회사 scope에는 피처·라벨·학습/정규화 함수나 롱 패널 분할이 없다. 보고용 회계 계산의 과거 자산·낙폭 및 완료된 월 수익률은 직접 절단/미래 교란으로 확인했다. 수식/evaluator 자체는 별도 ETF 감사 범위다.
- `audit.py`와 `mission_backend.py:402` 이후 경로는 보고할 모든 trial의 실제 ZIP·코드·계획·결과·전체 시행 이력에 감사 scope를 묶는다. 구현자와 다른 validator task/request, 전체 필수 텍스트 읽기, 고정 qlab의 실제 parse/validate 및 HTML receipt가 요구된다. 파일이 바뀌면 검증 객체가 만료된다. `adaptive_report.py:343`은 검증된 감사가 없으면 수치·차트·연구 해석을 숨긴다. 게시·approved source·director 전달은 실제 감사 검증 뒤에 연결된다. 연구 stage의 일반 메시지/도구 출구도 차단되어 있다.
- 결과와 기술 실패의 typed contract 및 영속 기록이 분리된다. 같은 job/plan의 재전달은 동일 결과를 유지하며, ambiguous 실행을 새 lease로 재실행하지 않는다. 같은 configuration의 이름·JSON 배치만 바꾼 중복 시행은 거절한다. 수리에는 새 근거 또는 실질 코드 변경이 필요하다. 매 결과의 plan/code와 누적 이력을 보존하고, 다음 제안은 직전 완료 결과를 참조한다. 이 개발구간 피드백을 봉인구간 검증으로 표시하지 않는다.

## 직접 수행한 검사

회사 `.venv/bin/python -B`, `PYTHONDONTWRITEBYTECODE=1`, `PYTHONPATH=src`로 파일 쓰기·subprocess·network를 거부하는 Python audit hook 아래 읽기 전용/메모리 검사를 수행했다. 후보 코드는 실행하지 않았다.

1. 회수 ZIP SHA-256 `f1b43d3ebaf7056b0b3d8e78f75b5a1f1734fedc2a2f50606afef706fb4b4b33` 및 14개 member SHA를 공개 JSON과 대조했다. manifest/plan/profile/bundle/code/company 식별자, 두 sandbox receipt와 isolation JSON이 일치했고 현재 `validate_adaptive_bundle`이 통과했다. qualification의 실제 날짜는 개발 시작 이전이며 표본 수·입력 집합이 일치했다.
2. 메모리 ZIP에서 관련 내부 해시도 다시 맞춘 뒤 회사 pin, bool 정수, fixture 시행 수, sealed/performance 플래그, qualification 표본/날짜/시간/단위, primary/MDD/표본 수, runtime, sandbox 실패/시간/입력, 수익률 날짜·유한성·초기값, ZIP 구조 및 코드 변조의 **27개 음성 사례가 모두 거절**됐다.
3. 감사 없는 보고서는 `performance_visible=false`, 빈 trial 수치와 차트 없음으로 확인했다. boolean `audit=True`는 `verified_audit_required`로 거절됐다. 합성 수익률의 **4개 cutoff에서 절단 재계산과 미래 수익률 교란** 후 과거 자산·낙폭 및 완료된 월별 값이 동일했다. 부수효과 시도는 0건이었다.

처음 검사 스크립트의 예외 메시지 단언은 실제 메시지의 공통 접두어를 고려하지 않아 중단됐다. 단언만 정정한 전체 메모리 검사에서 위 결과를 확인했다. 제품 코드를 수정하지 않았다.

메인 스레드가 별도로 실행한 전체 suite의 `checks-f6a50e58.json`과 실제 로그 끝을 읽었다: 최종 pin에서 **983 passed, 5 skipped, 1 deselected**, lint exit 0. 해당 pytest를 감사자가 직접 수행한 것으로 계산하지 않는다.

## 증거의 한계

회수 protocol의 실제 실행 pin은 `dad140c27b99f45e12a454d79dac56cc951b12a7`, 연구 fixture pin은 `65ba2c971830fd2991811ba2b0b9c8e70ec2a102`이다. 실행기·sandbox·worker·consumer 핵심 파일의 해당 pin 대비 동일성을 확인했으며, 이후 추가된 runtime 준비 및 build/controller/backend 변경은 현재 코드로 검토했다. receipt는 실제 3070/bwrap 두 단계, 합성 입력, `fixture_only=true`, 추가 과학 시행 0을 기록한다. HTTP transport는 mock이고 실제 Slack/Temporal 증거는 없다. poller 객체 재구성·동일 upload 재시도 검사는 실제 poller 프로세스 강제 종료 검사가 아니다. 원격 lifecycle 파일은 공개 JSON에 SHA만 있으므로 해당 호스트 파일을 직접 확인했다고 주장하지 않는다.

추가로 허용된 read context `3070-etf.json` 및 `3070-etf-fixture.zip`을 읽었다. 출력 디렉터리 검사 충돌을 수리한 후의 ZIP SHA-256은 `9534c1a22741d1dc40126be2d27af0025c877ca7149429931d23960724e81f54`이며, 27개 member를 현재 strict consumer로 직접 검증했다. 회사 pin은 이 판정의 `f6a50e58a5eed6f4388f99556ef931c32f48533e`, 연구 base는 `21fa730a6c98d593143a530715b44c0d64cf74b5`, 실행 fixture는 `c721146e61966cc52bd617689ed05c79356ee3fb`이다. qualification/두 sandbox SHA·실행 profile·개발 이전 qualification 날짜가 일치했다. 이 context는 실제 ETF evaluator의 두 sandbox 단계 연결을 보강하며 `fixture_only=true`, 추가 과학 시행 0, 실제 snapshot 읽기 0을 유지한다. 변경된 lab adapter의 재판정은 별도 범위다. 실제 입력 snapshot, 시장 성과, 모델의 경제적 설득력과 일반 운영 배치는 확인하지 않았다.

scope digest는 동일 repoRoot에서 `qlab.audits.compute_scope_digest(Path.cwd(), scope)`로 산출했다. objective 파일의 SHA-256 앞 12자리도 주어진 `03d58ee449e6`과 일치했다. HTML/receipt 및 `qlab.control` 검증은 메인 스레드가 별도로 수행한다.

## 판정 파일 검사

동일 회사 repoRoot에서 실제 `parse_audit` 성공 및 `validate_audit(..., Path.cwd()) == []`를 확인했다. `make check-audits`도 실제 실행했으나 이 repo에 해당 target이 없어 exit 2 (`No rule to make target 'check-audits'`)였다. 이를 Make gate 통과로 기록하지 않는다.
