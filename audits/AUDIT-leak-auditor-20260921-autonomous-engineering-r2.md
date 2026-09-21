---
judge: leak-auditor
target: .
verdict: pass
issued: 2026-09-21
target_commit: 6d786b67c0da0c4ca05448536a6145147f9408d5
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
scope_digest: 0c6e83e09e69
objective_digest: 03d58ee449e6
findings: []
supersedes:
  - audits/AUDIT-leak-auditor-20260921-autonomous-engineering.md
---

# Adaptive 실행·보고 — 누적 과학 범위 종료 재감사

## 판정과 범위

**pass.** 승인 명세에 지정한 누적 과학 시행 한도를 적용하는 변경에서 인과성 위반을 발견하지 않았다. 기존 공학 감사와 동일한 28개 scope를 재사용하며, 현재 바이트가 기록한 commit의 blob과 모두 일치한다. 승인 설계 digest는 `fcec7879dbf7`이다. `backtest-hygiene`, guards 참조와 이전 pin 대비 동일한 회사 `AGENTS.md`를 적용했다.

이전 실제 검토 pin `f6a50e58a5eed6f4388f99556ef931c32f48533e`에서 scope의 변경은 `mission_contracts.py`, `missions.py`, `test_research_missions.py`의 54개 추가 줄뿐이다. 나머지 25개 파일은 byte-identical이다. 기존 입력·코드 pin, sealed 격리, 수익률 재계산, 독립 감사 전 공개 차단과 trial/attempt 기록 경로의 검토를 유지하고 변경된 종료 경계를 재검사했다. 같은 target의 이전 회사 공학 판정을 승계하며 P11 과학 판정과 ETF 수식 판정을 대체하지 않는다.

## 인과성 확인

- `mission_contracts.py:90`: `max_total_trials`는 명세에 선택적으로 지정하는 양의 strict 정수다. `None`은 직렬화에서 빠져 기존 승인 manifest의 내용 식별자가 유지된다. 지정된 한도는 MissionSpec 및 digest에 들어가므로 기존 승인 내용에 조용히 추가할 수 없다. 미지정 명세에는 새로운 누적 한도를 가정하지 않는다.
- `missions.py:284`: 종료 조건은 `cumulative_trials >= max_total_trials`이다. cycle 또는 technical attempt 수를 사용하지 않는다. `record_outcome:425`의 기존 경로는 새로운 `result`일 때만 누적 수를 한 번 증가시키며, 기술 실패와 동일 결과 재전달은 증가시키지 않는다. 주기 갱신은 누적 수를 초기화하지 않는다.
- `missions.py:318`, `:536`: 한도 소진 후 새 trial 선택과 새 cycle 갱신을 쓰기 전에 거절한다. 같은 식별자의 과거 요청은 내용 일치를 확인해 기존 기록을 반환할 뿐 새 trial이나 cycle을 만들지 않는다. 기존 단일 진행 trial 제약과 mission 잠금 경로는 그대로다.
- `missions.py:568`: 아직 보고되지 않은 마지막 trial의 `received → interpretation`, `interpreted → audit`가 한도 종료 검사보다 먼저 처리된다. 모든 결과의 보고 checkpoint가 끝난 뒤 `owner_review / scientific_scope_exhausted`를 반환한다. `controller.py:425`에서 해당 상태에 실행 actor가 없으므로 다음 proposal/selection으로 넘어가지 않는다. 결과를 숨기거나 마지막 독립 감사를 생략하는 조기 종료 경로를 추가하지 않았다.

이번 변경은 피처·라벨·모델 적합·시계열 분할 또는 신호 창을 만들거나 변경하지 않는다. 해당 회사 scope에는 롱 패널 분할 및 피처 생성 가드 누락에 해당하는 신규 경로가 없다. 개발 결과에 대한 선택/종료 정책 변경을 sealed 또는 confirmation 증거로 표시하지 않는다.

## 직접 수행한 검사

현재 회사 `.venv/bin/python -B`와 명시적 `PYTHONPATH=src`에서 파일 쓰기·subprocess·network를 거부하는 Python audit hook 아래 읽기 전용/메모리 검사를 수행했다.

1. 기존 protocol ZIP(`f1b43d3ebaf7056b0b3d8e78f75b5a1f1734fedc2a2f50606afef706fb4b4b33`)과 허용된 추가 ETF context ZIP(`9534c1a22741d1dc40126be2d27af0025c877ca7149429931d23960724e81f54`)을 현재 strict consumer로 다시 검증했다. 두 ZIP 모두 원 manifest/mission digest와 search 직렬화를 유지하고 `fixture_only=true`, 추가 과학 시행 0으로 통과했다. 실제 실행 pin을 현재 감사 pin으로 바꾸지 않았다.
2. 누적 한도 미지정과 명시적 `None`의 직렬화가 기존 payload와 같음을 확인했다. `0`, 음수, bool, 소수, 문자열의 **5개 잘못된 한도**를 거절했고, 유효한 한도를 추가하면 mission digest가 달라졌다.
3. 실제 `_scope_exhausted`를 한도 미만·동일·초과 및 서로 다른 cycle/technical count의 **5개 경계 조합**으로 검사했다. 기술 시도 수와 주기 변경으로 누적 한도를 우회하지 못했다.
4. 읽기 전용 SQL 응답 fixture로 실제 `next_stage`의 interpretation/audit/repair/owner_review 분기를 호출했다. 실제 `select`와 `advance_cycle`은 한도 소진 상태에서 쓰기 전에 거절했고, owner_review가 controller actor 목록에 없음을 확인했다. 이 메모리 fixture는 인증·PostgreSQL 잠금·영속성의 재현 증거로 사용하지 않는다.

최종 검사에서 부수효과 시도는 0건이었다. 호출자가 보고한 focused pytest **77 passed**는 별도 수행 결과이며 감사자가 직접 실행한 검사로 합산하지 않는다. 새 pin의 전체 회사 suite는 호출자가 별도로 수행한다.

## 한계와 파일 검사

범위는 공학적 준비의 명세 보존·종료·검증 경로다. 실제 시장 데이터/성과·sealed 접근·SSH·전략 실행은 수행하지 않았다. 특정 후속 brief의 한도 6 적용이나 신규 owner 승인을 발급하지 않는다. fixture의 현재 consumer 통과는 과거 실행 commit과 현재 validator commit을 구분한 호환성 증거다. 기존 감사에서 명시한 실제 Slack/Temporal·원격 lifecycle 확인의 한계를 유지한다.

scope digest는 동일 repoRoot에서 실제 `qlab.audits.compute_scope_digest(Path.cwd(), scope)`로 산출했다. objective는 주어진 `03d58ee449e6`이며 HTML/receipt 및 `qlab.control` 검증은 메인 스레드가 수행한다.

실제 `parse_audit` 성공과 `validate_audit(record, repoRoot) == []`, supersedes 대상의 동일 judge/target/scope를 확인했다. 같은 repoRoot에서 실행한 `make check-audits`는 해당 target 부재로 **exit 2** (`No rule to make target 'check-audits'`)였다. Make gate 통과로 기록하지 않는다.
