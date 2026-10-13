# Research Planning P1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 모델 호출 없이 동작하는 연구 계획 기반을 만든다. 소유자가 승인하는 연구 질문, 질문 기반 근거 묶음(evidence set), 기존 프로그램 규칙을 공유하는 결정적 triage, 과거 과제 replay를 포함한다.

**Architecture:** 새 모듈 다섯 개(`planning_contracts`, `planning_schema.sql`, `planning_store`, `planning_retrieval`, `planning_triage`)와 명령 모듈 하나를 `src/quant_company/research/`에 추가한다. triage는 `ProgramStore`에서 꺼낸 공통 helper를 쓴다. 기존 경로(`validate_proposal`, `_assessment`, `decide`, lineage `reserve`)도 같은 helper를 호출하게 바꾸되, 검사 순서와 오류 문구는 그대로 둔다. 모든 기능은 `research_planning_enabled`(기본 false) 뒤에 있다.

**Tech Stack:** Python 3.11+(CI 3.12), Pydantic v2, psycopg 3, PostgreSQL(테스트는 실제 일회용 클러스터), pytest, ruff, uv.

**Spec:** [RESEARCH-PLANNING-DESIGN-20261013.md](RESEARCH-PLANNING-DESIGN-20261013.md) 5·6·8·10·15·16장. 이 계획은 그중 P1만 다룬다.

## Global Constraints

- P1은 모델·provider·Slack 연구 메시지 호출이 0회다. 예외는 질문 승인 요청 1건이며, 기존 `publish_approval` outbox를 쓰고 flag가 켜졌을 때만 보낸다.
- `research_planning_enabled` 기본값은 `false`다. 꺼져 있으면 총괄 프롬프트, 도구, 승인 대상, 런타임 동작이 지금과 같아야 한다.
- migration은 가산적이다. 기존 테이블, 직렬화, digest(`fingerprint`, `manifest_digest`, 과제 digest)를 바꾸지 않는다.
- `ProgramStore` 리팩터링은 동작을 바꾸지 않는다. 검사 순서와 `PolicyError` 문구가 같아야 하고, 기존 연구 테스트로 증명한다.
- 테스트는 실제 PostgreSQL에서 돈다(`TEST_DATABASE_URL`). 운영 DB를 테스트 대상으로 쓰지 않는다.
- 다른 소유자·프로젝트의 출처를 corpus, evidence set, admission에 넣지 않는다.
- 필수 출처(predecessor·explicit)를 조용히 빼거나, 원문을 잘라 놓고 완독으로 표시하지 않는다. 넘치면 `oversize`와 gap이다.
- 카드(card)는 탐색용이고 인용 근거가 아니다. 카드 하나는 3,000자 이하, 카드는 최대 12개다.
- 기록되지 않은 사실을 추정하지 않는다. replay에서 모르는 값은 `not_declared`로 남긴다.
- 사용자에게 보이는 문구는 한국어, 코드 주석·식별자·커밋 메시지는 영어다.
- 커밋 메시지 끝: `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`

## Review Focus

1. flag가 꺼진 운영: 총괄 capability 목록에 `research_planning`이 나타나지 않고, `planning_` action은 거부되고, 질문 승인 대상이 없어야 한다. (Task 3 `test_disabled_planning_changes_nothing`)
2. brief가 없는 출처(연구 결과, 일반 source, JSON이 아닌 content)는 제목·발췌로 색인되고 카드가 만들어져야 한다. 예외로 멈추면 안 된다. (Task 5 `test_plain_sources_get_excerpt_cards`)
3. 질문 검색어가 corpus와 하나도 겹치지 않거나 corpus가 비어 있어도, 필수 출처는 남고 related는 비며 0으로 나누지 않는다. (Task 5 `test_no_matching_terms_keeps_required_members`)
4. 프로그램에 묶인 질문의 `source_refs` 중 프로그램 권한 밖 출처는 admission에 들어가지 않고 `notes.excluded_refs`에 드러나야 한다. (Task 5 `test_bound_question_reports_refs_outside_program`)
5. 같은 스레드에 대기 중인 질문이 둘 이상이면 짧은 "승인"이 하나를 임의로 고르면 안 된다. 기존 `apply_owner_command`의 단일 대상 규칙을 그대로 받는지 확인한다. (Task 3 `test_pending_questions_are_separate_targets`)

---

## 파일 구조

| 파일 | 책임 |
| --- | --- |
| Create `src/quant_company/research/planning_contracts.py` | QuestionSpec, PlanningCandidate(+투영), GapRequest, TriageCheck·TriageResult, 엔진 규칙 카드 |
| Create `src/quant_company/research/planning_schema.sql` | P1 테이블 4개 |
| Create `src/quant_company/research/planning_store.py` | 질문 생성·승인·상태, gap 기록, 승인 어댑터, 총괄 도구 |
| Create `src/quant_company/research/planning_retrieval.py` | 토큰화, BM25, 역할, 카드, evidence set 계산·고정 |
| Create `src/quant_company/research/planning_triage.py` | T1~T9, replay |
| Create `src/quant_company/research/planning_commands.py` | `status`·`preview`·`replay-triage` 읽기 전용 명령 |
| Modify `src/quant_company/research/library.py` | `program_corpus`, `project_corpus` 추가(legacy 함수 불변) |
| Modify `src/quant_company/research/programs.py` | 공통 helper 추출, 기존 메서드가 helper 사용 |
| Modify `src/quant_company/research/scientific_lineages.py` | `exhausted()`, `LINEAGE_EXHAUSTED` 추출 |
| Modify `src/quant_company/company.py` | 승인 어댑터 등록, `planning_` 도구 분기, flag가 켜졌을 때만 capability 표시 |
| Modify `src/quant_company/config.py` | `research_planning_enabled` |
| Modify `src/quant_company/db.py` | `planning_schema.sql` 등록 |
| Modify `src/quant_company/cli.py` | `research-planning` 명령 |
| Tests | `tests/test_research_planning_contracts.py`, `..._questions.py`, `..._retrieval.py`, `..._triage.py` |
| Docs | Task 9: replay 증거와 P1 요약, 설계 문서 5장 상태 |

P1에서 만들지 않는 것(P2 이후): run·후보·검토·결정·handoff·protocol·usage 테이블, 단계 행 `planning_run_id`, 직원 단계, 예산 hook, `maintenance/problems.py` 수정, playbook, 목적 검색.

## 공통 테스트 실행

모든 테스트 명령은 다음 환경을 쓴다. 클러스터는 일회용이다.

```bash
export UV_PYTHON=3.12 TEST_DATABASE_URL=postgresql://postgres@127.0.0.1:55491/postgres
```

---

### Task 1: 계획 계약 (`planning_contracts.py`)

**Files:**
- Create: `src/quant_company/research/planning_contracts.py`
- Test: `tests/test_research_planning_contracts.py`

**Interfaces:**
- Produces: `QuestionSpec`, `PlanningCandidate`, `task_proposal(candidate) -> ResearchTaskProposal`, `GapRequest`, `TriageCheck`, `TriageResult`, `summarize(checks, *, bound) -> str`, `OUTCOME_PRECEDENCE`, `EngineRuleCard`, `ENGINE_RULE_CARDS`, `engine_card_key(spec) -> str`, `engine_card(spec) -> EngineRuleCard | None`, 타입 별칭 `Estimand`, `ExecutionTiming`, `TOPICS`, `METRICS`.

- [ ] **Step 1: 실패하는 테스트 작성**

```python
"""Planning contracts are pure models; no database."""

from types import SimpleNamespace
from typing import get_args

import pytest
from pydantic import ValidationError

from quant_company.company import fingerprint
from quant_company.quant_feed.contracts import ResearchBrief
from quant_company.research.mission_contracts import Objective
from quant_company.research.planning_contracts import (
    ENGINE_RULE_CARDS,
    METRICS,
    TOPICS,
    Estimand,
    PlanningCandidate,
    QuestionSpec,
    TriageCheck,
    TriageResult,
    engine_card,
    summarize,
    task_proposal,
)
from quant_company.research.program_contracts import ResearchTaskProposal


def candidate(**changes):
    return {"title": "Fixture candidate", "question": "Does the fixture hold?", "mode": "novel_hypothesis",
            "original_claim": "Synthetic claim", "method": "Controlled fixture", "departures": "Synthetic data",
            "falsification": "Consumer rejects a broken identity", "source_ids": ["fixture:baseline"],
            "citations": [{"source_id": "fixture:baseline", "location": "char:0",
                           "quote": "Synthetic original: point-in-time fixture data"}],
            "predecessor_mission_ids": [], "envelope": "etf", "contribution_kind": "alpha_signal",
            "mechanism": "Fixture mechanism", "baseline_comparator": "Buy and hold",
            "estimand": "stress-net-absolute-cagr", "execution_timing": "prior_session_open_fill",
            "implementation_scope": "config_only",
            "contradiction_search": {"status": "searched_not_found", "scope_note": "Fixture corpus only"},
            "lineage_relation": {"status": "new"}, **changes}


def question(**changes):
    return {"question": "Does momentum survive costs in Korean ETFs?",
            "decision_to_inform": "Whether to register a cost-aware momentum task",
            "market": "kr_etf", "search_terms_en": ["momentum"], "search_terms_ko": ["모멘텀"], **changes}


def test_projection_is_verbatim_and_digest_stable():
    value = PlanningCandidate.model_validate(candidate())
    expected = {key: candidate()[key] for key in ResearchTaskProposal.model_fields}
    projected = task_proposal(value).model_dump(mode="json")
    assert projected == ResearchTaskProposal.model_validate(expected).model_dump(mode="json")
    assert fingerprint(projected) == fingerprint(ResearchTaskProposal.model_validate(expected).model_dump(mode="json"))


def test_candidate_without_envelope_has_no_experiment_path():
    with pytest.raises(ValueError, match="no experiment path"):
        task_proposal(PlanningCandidate.model_validate(candidate(envelope=None)))


def test_literals_follow_existing_contracts():
    assert set(get_args(Estimand)) == set(get_args(Objective.model_fields["metric"].annotation)) | {"other"}
    assert set(METRICS) == set(get_args(Objective.model_fields["metric"].annotation))
    assert set(TOPICS) == set(get_args(ResearchBrief.model_fields["topic"].annotation))


def test_negative_repeat_and_contradiction_states_are_explicit():
    with pytest.raises(ValidationError, match="stated difference"):
        PlanningCandidate.model_validate(candidate(lineage_relation={"status": "repeats_negative"}))
    with pytest.raises(ValidationError, match="found contradiction"):
        PlanningCandidate.model_validate(candidate(contradiction_search={"status": "found"}))
    refs = [{"source_id": "fixture:baseline", "location": "char:0", "quote": "Synthetic original text"}]
    with pytest.raises(ValidationError, match="found contradiction"):
        PlanningCandidate.model_validate(candidate(contradiction_search={"status": "not_searched", "refs": refs}))


def test_question_needs_terms_and_unique_lists():
    assert QuestionSpec.model_validate(question()).mandate == "unknown"
    with pytest.raises(ValidationError, match="search terms"):
        QuestionSpec.model_validate(question(search_terms_en=[], search_terms_ko=[]))
    with pytest.raises(ValidationError, match="repeat"):
        QuestionSpec.model_validate(question(search_terms_en=["momentum", "momentum"]))
    with pytest.raises(ValidationError):
        QuestionSpec.model_validate(question(topics=["not_a_feed_topic"]))


def test_triage_records_all_checks_and_most_terminal_outcome():
    checks = [TriageCheck(code=f"T{n}", status="pass") for n in range(1, 10)]
    assert summarize(checks, bound=True) == "eligible"
    assert summarize(checks, bound=False) == "unbound"
    checks[6] = TriageCheck(code="T7", status="fail", outcome="needs_data", message="packet")
    checks[3] = TriageCheck(code="T4", status="fail", outcome="out_of_scope", message="mode")
    assert summarize(checks, bound=True) == "out_of_scope"
    with pytest.raises(ValidationError, match="every check once"):
        TriageResult(outcome="eligible", checks=list(reversed(checks)), basis={})
    with pytest.raises(ValidationError, match="Only a failed check"):
        TriageCheck(code="T1", status="pass", outcome="stale")


def test_engine_card_follows_worker_transport_selection():
    assert engine_card(SimpleNamespace(schema_version=3, execution_profile="kr-etf-research-v2")).id == "kr-research-python-v2"
    assert engine_card(SimpleNamespace(schema_version=1, execution_profile="kr-etf-monthly-python-v1")) is None
    assert ENGINE_RULE_CARDS["kr-research-python-v2"].supported_execution_timings == ["prior_session_open_fill"]
```

- [ ] **Step 2: 실패 확인**

Run: `uv run --frozen pytest -q tests/test_research_planning_contracts.py`
Expected: FAIL — `ModuleNotFoundError: No module named 'quant_company.research.planning_contracts'`

- [ ] **Step 3: 구현**

```python
"""Typed planning records. Employees propose; the service validates, ranks and decides."""

from typing import Annotated, Literal, get_args
from uuid import UUID

from pydantic import Field, model_validator

from ..quant_feed.contracts import ResearchBrief
from .mission_contracts import MissionModel, MissionSpec, Objective, SourceIds, Text
from .program_contracts import ResearchTaskProposal, SourceCitation

Term = Annotated[str, Field(min_length=1, max_length=80)]
Short = Annotated[str, Field(min_length=1, max_length=300)]
Ref = Annotated[str, Field(min_length=1, max_length=200)]
TOPICS = get_args(ResearchBrief.model_fields["topic"].annotation)
Topic = Literal[TOPICS]
METRICS = get_args(Objective.model_fields["metric"].annotation)
Estimand = Literal[METRICS + ("other",)]
ExecutionTiming = Literal["prior_session_open_fill", "same_day_close_fill", "intraday", "not_applicable", "other"]
ImplementationScope = Literal["config_only", "new_feature", "new_model", "new_data", "unknown"]
ContributionKind = Literal["alpha_signal", "portfolio_method", "execution_method", "data_method", "validation_method"]
GapKind = Literal["data_packet", "evaluator", "execution_timing", "data_policy", "program_missing", "program_budget",
                  "lineage_budget", "source_currency", "source_coverage", "oversize", "read_budget", "output_contract"]
TriageCode = Literal["T1", "T2", "T3", "T4", "T5", "T6", "T7", "T8", "T9"]
TriageOutcome = Literal["eligible", "unbound", "invalid", "stale", "out_of_scope", "duplicate", "needs_program",
                        "needs_evaluator", "needs_data"]
# Most terminal first: an out-of-scope candidate is not merely waiting for data.
OUTCOME_PRECEDENCE = ("invalid", "stale", "out_of_scope", "duplicate", "needs_program", "needs_evaluator",
                      "needs_data")


class QuestionSpec(MissionModel):
    """What the owner wants answered. Undetermined mandates stay 'unknown'; nothing is invented."""

    schema_version: Literal[1] = 1
    question: Annotated[str, Field(min_length=8, max_length=1000)]
    decision_to_inform: Annotated[str, Field(min_length=8, max_length=1000)]
    market: Literal["kr_etf", "kr_stock", "any"]
    mandate: Annotated[str, Field(min_length=1, max_length=600)] = "unknown"
    search_terms_en: Annotated[list[Term], Field(max_length=12)] = []
    search_terms_ko: Annotated[list[Term], Field(max_length=12)] = []
    counter_terms: Annotated[list[Term], Field(max_length=12)] = []
    topics: Annotated[list[Topic], Field(max_length=6)] = []
    required_data_fields: Annotated[list[Term], Field(max_length=20)] = []
    exclusions: Annotated[list[Short], Field(max_length=20)] = []
    source_refs: Annotated[list[Ref], Field(max_length=20)] = []
    envelope_hint: Annotated[str, Field(min_length=1, max_length=61)] | None = None
    estimand_hint: Estimand | None = None

    @model_validator(mode="after")
    def bounded(self):
        if not (self.search_terms_en or self.search_terms_ko):
            raise ValueError("A research question needs English or Korean search terms")
        for values in (self.search_terms_en, self.search_terms_ko, self.counter_terms, self.topics,
                       self.required_data_fields, self.exclusions, self.source_refs):
            if len(set(values)) != len(values):
                raise ValueError("Question lists cannot repeat a value")
        return self


class ContradictionSearch(MissionModel):
    status: Literal["found", "searched_not_found", "not_searched"]
    refs: Annotated[list[SourceCitation], Field(max_length=10)] = []
    scope_note: Annotated[str, Field(max_length=600)] = ""

    @model_validator(mode="after")
    def refs_match_status(self):
        if (self.status == "found") != bool(self.refs):
            raise ValueError("Only a found contradiction carries original citations")
        return self


class LineageRelation(MissionModel):
    status: Literal["new", "extends", "repeats_negative", "unknown"]
    lineage_refs: Annotated[list[Ref], Field(max_length=20)] = []
    difference: Annotated[str, Field(max_length=1200)] = ""

    @model_validator(mode="after")
    def stated_difference(self):
        if self.status == "repeats_negative" and not self.difference.strip():
            raise ValueError("Repeating a negative lineage result requires the stated difference")
        return self


class PlanningCandidate(MissionModel):
    # Same names and meaning as ResearchTaskProposal; a bound run registers these fields verbatim.
    title: Text
    question: Text
    mode: Literal["exact_replication", "market_transfer", "novel_hypothesis"]
    original_claim: Text
    method: Text
    departures: Text
    falsification: Text
    source_ids: SourceIds
    citations: Annotated[list[SourceCitation], Field(min_length=1, max_length=50)]
    predecessor_mission_ids: list[UUID]
    envelope: Text | None
    # Planning only; never projected into the program task.
    contribution_kind: ContributionKind
    mechanism: Text
    baseline_comparator: Text
    estimand: Estimand
    execution_timing: ExecutionTiming
    implementation_scope: ImplementationScope
    required_data_fields: Annotated[list[Term], Field(max_length=20)] = []
    unknowns: Annotated[list[Short], Field(max_length=10)] = []
    contradiction_search: ContradictionSearch
    lineage_relation: LineageRelation


def task_proposal(candidate: PlanningCandidate) -> ResearchTaskProposal:
    """The exact program task a bound candidate becomes; there is no re-proposal step."""
    if candidate.envelope is None:
        raise ValueError("A candidate without an envelope has no experiment path")
    return ResearchTaskProposal.model_validate(
        candidate.model_dump(mode="json", include=set(ResearchTaskProposal.model_fields)))


class GapRequest(MissionModel):
    kind: GapKind
    subject: Annotated[str, Field(min_length=1, max_length=300)]
    resolver: Literal["employee", "operator", "owner"]
    required_evidence: Annotated[str, Field(min_length=1, max_length=600)]
    recheck_on: Annotated[str, Field(min_length=1, max_length=300)]


class TriageCheck(MissionModel):
    code: TriageCode
    status: Literal["pass", "fail", "not_applicable", "not_declared"]
    outcome: TriageOutcome | None = None
    message: Annotated[str, Field(max_length=600)] = ""
    gap: GapRequest | None = None

    @model_validator(mode="after")
    def failure_shape(self):
        if (self.status == "fail") != (self.outcome is not None):
            raise ValueError("Only a failed check carries an outcome")
        if self.gap is not None and self.status != "fail":
            raise ValueError("Only a failed check opens a gap")
        return self


class TriageResult(MissionModel):
    schema_version: Literal[1] = 1
    outcome: TriageOutcome
    checks: Annotated[list[TriageCheck], Field(min_length=9, max_length=9)]
    basis: dict[str, str | int | None]

    @model_validator(mode="after")
    def ordered(self):
        if [check.code for check in self.checks] != [f"T{n}" for n in range(1, 10)]:
            raise ValueError("Triage records every check once, in order")
        return self


def summarize(checks, *, bound):
    failed = {check.outcome for check in checks if check.status == "fail"}
    for outcome in OUTCOME_PRECEDENCE:
        if outcome in failed:
            return outcome
    return "eligible" if bound else "unbound"


class EngineRuleCard(MissionModel):
    id: str
    version: int
    decision_information: str
    fill: str
    returns: str
    supported_execution_timings: list[ExecutionTiming]
    basis: list[str]


ENGINE_RULE_CARDS = {card.id: card for card in (
    EngineRuleCard(
        id="kr-research-python-v2", version=1,
        decision_information="Decisions use information available before 09:00 KST: the prior session and earlier.",
        fill="Same-day open.", returns="Open to next open, after the approved cost model.",
        supported_execution_timings=["prior_session_open_fill"],
        basis=["docs/adr/0037-literature-grounded-research-programs.md",
               "docs/project/evidence/research-programs-20260929/etf-data-evidence-note.json"]),
)}


def engine_card_key(spec: MissionSpec) -> str:
    # Same transport selection as worker job binding in MissionStore.
    return "kr-research-python-v2" if spec.schema_version >= 2 else spec.execution_profile


def engine_card(spec: MissionSpec) -> EngineRuleCard | None:
    return ENGINE_RULE_CARDS.get(engine_card_key(spec))
```

- [ ] **Step 4: 통과 확인**

Run: `uv run --frozen pytest -q tests/test_research_planning_contracts.py && uv run --frozen ruff check src/quant_company/research/planning_contracts.py tests/test_research_planning_contracts.py`
Expected: 7 passed, ruff `All checks passed!`

- [ ] **Step 5: 커밋**

```bash
git add src/quant_company/research/planning_contracts.py tests/test_research_planning_contracts.py
git commit -m "Add typed research planning contracts" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 2: P1 스키마

**Files:**
- Create: `src/quant_company/research/planning_schema.sql`
- Modify: `src/quant_company/db.py:41` (program_schema 다음 줄)
- Test: `tests/test_research_planning_questions.py` (이 파일은 Task 3에서 이어 쓴다)

**Interfaces:**
- Produces: 테이블 `research_questions`, `research_question_revisions`, `research_evidence_sets`, `research_gaps`. 부분 unique 인덱스 `research_gaps_open_subject`.

- [ ] **Step 1: 실패하는 테스트 작성**

```python
"""Research questions on real PostgreSQL; synthetic owner events only, no model calls."""

import psycopg
import pytest

from .test_research_missions import Harness

PLANNING_TABLES = ("research_questions", "research_question_revisions", "research_evidence_sets", "research_gaps")


def test_planning_schema_is_additive_and_migrates_twice(company):
    company.db.migrate()
    with company.db.transaction() as conn:
        for table in PLANNING_TABLES:
            assert conn.execute("SELECT to_regclass(%s) AS name", (table,)).fetchone()["name"]


def test_open_question_requires_owner_activation_event(company):
    h = Harness(company)
    with pytest.raises(psycopg.errors.CheckViolation):
        with company.db.transaction() as conn:
            conn.execute("""INSERT INTO research_questions(id,project_id,owner_user,project_revision,digest,title,
                state,origin,created_by) VALUES(gen_random_uuid(),%s,'UHUMAN',1,%s,'q','open','owner_request','director')""",
                (h.project["project_id"], "0" * 64))
```

- [ ] **Step 2: 실패 확인**

Run: `uv run --frozen pytest -q tests/test_research_planning_questions.py`
Expected: FAIL — `to_regclass` 결과가 `None`이라 assert 실패, 두 번째는 `UndefinedTable`.

- [ ] **Step 3: 구현**

`src/quant_company/research/planning_schema.sql`:

```sql
CREATE TABLE IF NOT EXISTS research_questions (
 id uuid PRIMARY KEY, project_id uuid NOT NULL REFERENCES projects(id), owner_user text NOT NULL,
 project_revision integer NOT NULL, program_id uuid REFERENCES research_programs(id),
 current_revision integer NOT NULL DEFAULT 1 CHECK(current_revision>=1), digest text NOT NULL, title text NOT NULL,
 state text NOT NULL DEFAULT 'proposed' CHECK(state IN ('proposed','open','waiting','paused','closed')),
 origin text NOT NULL CHECK(origin IN ('owner_request','follow_up','gap')),
 parent_question_id uuid REFERENCES research_questions(id), priority integer NOT NULL DEFAULT 0,
 activated_event_id text UNIQUE REFERENCES inbound(event_key), created_by text NOT NULL,
 created_at timestamptz NOT NULL DEFAULT now(), updated_at timestamptz NOT NULL DEFAULT now(),
 CHECK(state NOT IN ('open','waiting') OR activated_event_id IS NOT NULL)
);
CREATE INDEX IF NOT EXISTS research_questions_project_state ON research_questions(project_id,state);
CREATE TABLE IF NOT EXISTS research_question_revisions (
 question_id uuid NOT NULL REFERENCES research_questions(id), revision integer NOT NULL CHECK(revision>=1),
 payload jsonb NOT NULL, digest text NOT NULL, created_by text NOT NULL,
 created_at timestamptz NOT NULL DEFAULT now(), PRIMARY KEY(question_id,revision)
);
CREATE TABLE IF NOT EXISTS research_evidence_sets (
 id uuid PRIMARY KEY, question_id uuid NOT NULL, question_revision integer NOT NULL,
 program_id uuid REFERENCES research_programs(id), program_manifest_digest text,
 corpus_digest text NOT NULL, corpus_watermark timestamptz, policy_digest text NOT NULL,
 members jsonb NOT NULL, cards jsonb NOT NULL, notes jsonb NOT NULL DEFAULT '{}',
 char_total integer NOT NULL CHECK(char_total>=0), digest text NOT NULL,
 state text NOT NULL CHECK(state IN ('ready','oversize')), created_at timestamptz NOT NULL DEFAULT now(),
 UNIQUE(question_id,question_revision,corpus_digest,policy_digest),
 FOREIGN KEY(question_id,question_revision) REFERENCES research_question_revisions(question_id,revision)
);
CREATE TABLE IF NOT EXISTS research_gaps (
 id uuid PRIMARY KEY, project_id uuid NOT NULL REFERENCES projects(id),
 question_id uuid REFERENCES research_questions(id), evidence_set_id uuid REFERENCES research_evidence_sets(id),
 kind text NOT NULL, subject text NOT NULL, subject_key text NOT NULL,
 resolver text NOT NULL CHECK(resolver IN ('employee','operator','owner')),
 required_evidence text NOT NULL, recheck_on text NOT NULL, dependency_fingerprint text NOT NULL,
 state text NOT NULL DEFAULT 'open' CHECK(state IN ('open','resolved','superseded','unresolvable')),
 resolution jsonb, root_gap_id uuid REFERENCES research_gaps(id),
 created_at timestamptz NOT NULL DEFAULT now(), updated_at timestamptz NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX IF NOT EXISTS research_gaps_open_subject ON research_gaps(project_id,subject_key)
 WHERE state='open';
```

`src/quant_company/db.py`: `program_schema.sql` 줄 바로 다음에 추가.

```python
            conn.execute(files("quant_company.research").joinpath("planning_schema.sql").read_text())
```

- [ ] **Step 4: 통과 확인**

Run: `uv run --frozen pytest -q tests/test_research_planning_questions.py`
Expected: 2 passed

- [ ] **Step 5: 커밋**

```bash
git add src/quant_company/research/planning_schema.sql src/quant_company/db.py tests/test_research_planning_questions.py
git commit -m "Add the research planning schema" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 3: 질문 저장소, 승인 어댑터, 총괄 도구, flag

**Files:**
- Create: `src/quant_company/research/planning_store.py`
- Modify: `src/quant_company/config.py` (연구 flag 근처, `company_autonomous_research_enabled` 다음 줄)
- Modify: `src/quant_company/company.py` (`__init__`의 어댑터 튜플, `_tool`의 `research_control` 분기, capability dict의 `autonomous_research` 다음)
- Test: `tests/test_research_planning_questions.py`

**Interfaces:**
- Consumes: Task 1 `QuestionSpec`, `GapRequest`; 기존 `MissionStore._owner`, `publish_approval`, `ApprovalTarget`, `typed`.
- Produces: `OPEN_QUESTION_LIMIT = 3`, `require_enabled(company)`, `QuestionStore(company)`의 `draft(conn, project, payload, *, actor, origin="owner_request", program_id=None, parent_question_id=None) -> dict`, `owner_command(conn, question_id, *, owner, revision, manifest_digest, event_key, action) -> dict`, `snapshot(conn, question_id) -> dict`, `status(conn, project) -> dict`; `open_gap(conn, project_id, request, *, dependency_fingerprint, question_id=None, evidence_set_id=None) -> str`; `QuestionApprovalAdapter(company)`(kind `research_question`); `planning_tool(company, conn, project, task, arguments)`.

- [ ] **Step 1: 실패하는 테스트 작성** (`tests/test_research_planning_questions.py`에 추가)

```python
from quant_company.company import PolicyError
from quant_company.research.approvals import targets
from quant_company.research.planning_store import QuestionApprovalAdapter, QuestionStore, planning_tool


def spec(**changes):
    return {"question": "Does momentum survive costs in Korean ETFs?",
            "decision_to_inform": "Whether to register a cost-aware momentum task",
            "market": "kr_etf", "search_terms_en": ["momentum"], "search_terms_ko": ["모멘텀"], **changes}


@pytest.fixture
def planning(company):
    company.settings.research_planning_enabled = True
    return Harness(company)


def draft(h, **changes):
    with h.company.db.transaction() as conn:
        project = h.company._project(conn, h.project["project_id"])
        return QuestionStore(h.company).draft(conn, project, spec(**changes), actor="director")


def approve(h, question, *, bound=True):
    event = h.owner_event("approve", bound=bound, target={"kind": "research_question", "target_id": question["id"],
                                                         "revision": 1, "manifest_digest": question["digest"]})
    with h.company.db.transaction() as conn:
        return QuestionStore(h.company).owner_command(conn, question["id"], owner="UHUMAN", revision=1,
            manifest_digest=question["digest"], event_key=event, action="approve")


def director_task(h, conn):
    return conn.execute("SELECT * FROM tasks WHERE id=%s", (h.project["task_id"],)).fetchone()


def test_disabled_planning_changes_nothing(company):
    h = Harness(company)
    assert "research_planning" not in company.runtime_context()["capabilities"]
    with company.db.transaction() as conn:
        project = company._project(conn, h.project["project_id"])
        assert QuestionApprovalAdapter(company).targets(conn, project) == []
        with pytest.raises(PolicyError, match="disabled"):
            planning_tool(company, conn, project, director_task(h, conn), {"action": "planning_status"})


def test_draft_is_idempotent_and_waits_for_the_owner(planning):
    first, second = draft(planning), draft(planning)
    assert first["id"] == second["id"]
    assert first["state"] == "proposed" and first["activated_event_id"] is None
    assert first["spec"]["mandate"] == "unknown"


def test_owner_approval_opens_question_with_inbound_event(planning):
    opened = approve(planning, draft(planning))
    assert opened["state"] == "open" and opened["activated_event_id"].startswith("fixture:authenticated:")


def test_unbound_approval_record_cannot_open_question(planning):
    with pytest.raises(PolicyError, match="bound authenticated approval record"):
        approve(planning, draft(planning), bound=False)


def test_cancel_closes_a_proposed_question(planning):
    question = draft(planning)
    event = planning.owner_event("cancel", target={"kind": "research_question", "target_id": question["id"],
                                                   "revision": 1, "manifest_digest": question["digest"]})
    with planning.company.db.transaction() as conn:
        closed = QuestionStore(planning.company).owner_command(conn, question["id"], owner="UHUMAN", revision=1,
            manifest_digest=question["digest"], event_key=event, action="cancel")
    assert closed["state"] == "closed"


def test_open_question_limit_keeps_extra_questions_proposed(planning):
    for n in range(3):
        approve(planning, draft(planning, question=f"Fixture question number {n} for limits"))
    fourth = draft(planning, question="Fixture question number 3 for limits")
    with pytest.raises(PolicyError, match="limit"):
        approve(planning, fourth)
    with planning.company.db.transaction() as conn:
        assert QuestionStore(planning.company).snapshot(conn, fourth["id"])["state"] == "proposed"


def test_question_sources_must_belong_to_the_project(planning):
    with pytest.raises(PolicyError, match="Unavailable or unapproved source"):
        draft(planning, source_refs=["other-project:source"])


def test_tool_drafts_once_and_publishes_one_approval(planning):
    company = planning.company
    for _ in range(2):
        with company.db.transaction() as conn:
            project = company._project(conn, planning.project["project_id"])
            snapshot = planning_tool(company, conn, project, director_task(planning, conn),
                                     {"action": "planning_question_draft", "spec": spec()})
    with company.db.transaction() as conn:
        bindings = conn.execute("""SELECT count(*) AS n FROM research_approval_bindings
            WHERE target_kind='research_question' AND target_id=%s""", (snapshot["id"],)).fetchone()["n"]
        status = planning_tool(company, conn, company._project(conn, planning.project["project_id"]),
                               director_task(planning, conn), {"action": "planning_status"})
    assert bindings == 1
    assert [q["id"] for q in status["questions"]] == [snapshot["id"]]
    assert "planning_question_draft" in company.runtime_context()["capabilities"]["research_planning"]["usage"]


def test_tool_rejects_owner_authority_actions(planning):
    with planning.company.db.transaction() as conn:
        project = planning.company._project(conn, planning.project["project_id"])
        with pytest.raises(PolicyError, match="owner activates"):
            planning_tool(planning.company, conn, project, director_task(planning, conn),
                          {"action": "planning_question_approve", "question_id": "x"})


def test_pending_questions_are_separate_targets(planning):
    first = draft(planning)
    second = draft(planning, question="A second fixture question for targets")
    with planning.company.db.transaction() as conn:
        project = planning.company._project(conn, planning.project["project_id"])
        pending = [t for t in targets(planning.company, conn, project) if t.state == "pending_approval"]
    # apply_owner_command explains instead of choosing when more than one target is pending.
    assert {str(t.target_id) for t in pending} == {first["id"], second["id"]}
```

- [ ] **Step 2: 실패 확인**

Run: `uv run --frozen pytest -q tests/test_research_planning_questions.py`
Expected: FAIL — `ModuleNotFoundError: quant_company.research.planning_store`

- [ ] **Step 3: 구현**

`src/quant_company/config.py` (`company_autonomous_research_enabled` 다음 줄):

```python
    research_planning_enabled: bool = False
```

`src/quant_company/research/planning_store.py`:

```python
"""Owner-activated research questions and service-recorded gaps. No model calls."""

import json
from uuid import uuid4

from psycopg.types.json import Jsonb

from ..company import PolicyError, as_json, fingerprint, stable
from .approvals import ApprovalTarget, publish_approval
from .missions import MissionStore, typed
from .planning_contracts import GapRequest, QuestionSpec

OPEN_QUESTION_LIMIT = 3


def require_enabled(company):
    if not company.settings.research_planning_enabled:
        raise PolicyError("Research planning is disabled")


class QuestionStore:
    def __init__(self, company):
        self.company = company

    def draft(self, conn, project, payload, *, actor, origin="owner_request", program_id=None,
              parent_question_id=None):
        require_enabled(self.company)
        spec = typed(QuestionSpec, payload)
        if project["status"] != "active":
            raise PolicyError("Research questions require an active project")
        self.company._check_sources(conn, project["id"], spec.source_refs)
        if program_id is not None:
            found = conn.execute("SELECT project_id FROM research_programs WHERE id=%s", (program_id,)).fetchone()
            if not found or found["project_id"] != project["id"]:
                raise PolicyError("Question program is outside this project")
        value = spec.model_dump(mode="json")
        digest = fingerprint(value)
        identity = stable(f"research-question:{project['id']}:{project['revision']}:{program_id}:{digest}")
        if not conn.execute("SELECT 1 FROM research_questions WHERE id=%s", (identity,)).fetchone():
            conn.execute("""INSERT INTO research_questions(id,project_id,owner_user,project_revision,program_id,digest,
                title,origin,parent_question_id,created_by) VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                (identity, project["id"], project["owner_user"], project["revision"], program_id, digest,
                 spec.question[:200], origin, parent_question_id, actor))
            conn.execute("""INSERT INTO research_question_revisions(question_id,revision,payload,digest,created_by)
                VALUES(%s,1,%s,%s,%s)""", (identity, Jsonb(value), digest, actor))
            self.company._event(conn, "research_question_proposed", {"question_id": identity, "digest": digest,
                "origin": origin}, project["id"])
        return self.snapshot(conn, identity)

    def owner_command(self, conn, question_id, *, owner, revision, manifest_digest, event_key, action):
        require_enabled(self.company)
        row = conn.execute("SELECT * FROM research_questions WHERE id=%s FOR UPDATE", (question_id,)).fetchone()
        if not row:
            raise PolicyError("Unknown research question")
        project = self.company._project(conn, row["project_id"])
        identity = {"id": row["id"], "owner_user": row["owner_user"], "revision": row["project_revision"],
                    "manifest_digest": row["digest"]}
        MissionStore(self.company)._owner(conn, project, identity, owner=owner, revision=revision,
            manifest_digest=manifest_digest, event_key=event_key, action=action, target_kind="research_question")
        if action == "approve":
            if row["state"] == "open" and row["activated_event_id"] == event_key:
                return self.snapshot(conn, question_id)
            if row["state"] != "proposed":
                raise PolicyError("Question activation requires a proposed question")
            active = conn.execute("""SELECT count(*) AS n FROM research_questions
                WHERE project_id=%s AND state IN ('open','waiting')""", (row["project_id"],)).fetchone()["n"]
            if active >= OPEN_QUESTION_LIMIT:
                raise PolicyError("Open research question limit reached; close a question first")
            conn.execute("UPDATE research_questions SET state='open',activated_event_id=%s,updated_at=now() WHERE id=%s",
                         (event_key, question_id))
        elif action == "cancel":
            if row["state"] != "closed":
                conn.execute("UPDATE research_questions SET state='closed',updated_at=now() WHERE id=%s", (question_id,))
        else:
            raise PolicyError("Unsupported research question control")
        self.company._event(conn, "research_question_" + action, {"question_id": str(question_id),
            "digest": manifest_digest, "owner_event_id": event_key}, row["project_id"])
        return self.snapshot(conn, question_id)

    def snapshot(self, conn, question_id):
        row = conn.execute("SELECT * FROM research_questions WHERE id=%s", (question_id,)).fetchone()
        if not row:
            raise PolicyError("Unknown research question")
        revision = conn.execute("""SELECT payload FROM research_question_revisions WHERE question_id=%s
            AND revision=%s""", (question_id, row["current_revision"])).fetchone()
        sets = conn.execute("""SELECT id,question_revision,state,char_total,digest,created_at FROM research_evidence_sets
            WHERE question_id=%s ORDER BY created_at DESC,id DESC LIMIT 3""", (question_id,)).fetchall()
        gaps = conn.execute("""SELECT id,kind,subject,resolver,required_evidence,recheck_on,state FROM research_gaps
            WHERE question_id=%s AND state='open' ORDER BY created_at,id""", (question_id,)).fetchall()
        return as_json({key: row[key] for key in ("id", "project_id", "program_id", "state", "origin", "priority",
            "current_revision", "digest", "title", "activated_event_id", "created_at")}
            | {"spec": revision["payload"], "evidence_sets": sets, "open_gaps": gaps})

    def status(self, conn, project):
        rows = conn.execute("SELECT id FROM research_questions WHERE project_id=%s ORDER BY created_at,id",
                            (project["id"],)).fetchall()
        return {"questions": [self.snapshot(conn, row["id"]) for row in rows],
                "limits": {"open_questions": OPEN_QUESTION_LIMIT},
                "authority": "Read-only. Questions open only through authenticated owner approval."}


def open_gap(conn, project_id, request: GapRequest, *, dependency_fingerprint, question_id=None,
             evidence_set_id=None):
    """One open gap per subject; repeated detection links to it instead of duplicating."""
    request = typed(GapRequest, request)
    key = fingerprint({"kind": request.kind, "subject": request.subject})
    conn.execute("""INSERT INTO research_gaps(id,project_id,question_id,evidence_set_id,kind,subject,subject_key,
        resolver,required_evidence,recheck_on,dependency_fingerprint) VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
        ON CONFLICT DO NOTHING""", (uuid4(), project_id, question_id, evidence_set_id, request.kind, request.subject,
        key, request.resolver, request.required_evidence, request.recheck_on, dependency_fingerprint))
    return str(conn.execute("SELECT id FROM research_gaps WHERE project_id=%s AND subject_key=%s AND state='open'",
                            (project_id, key)).fetchone()["id"])


class QuestionApprovalAdapter:
    kind = "research_question"

    def __init__(self, company):
        self.company = company

    def targets(self, conn, project):
        if not self.company.settings.research_planning_enabled:
            return []
        return [ApprovalTarget(kind=self.kind, target_id=row["id"], revision=row["project_revision"],
            manifest_digest=row["digest"], title=row["title"],
            state="pending_approval" if row["state"] == "proposed" else row["state"], created_at=row["created_at"])
            for row in conn.execute("SELECT * FROM research_questions WHERE project_id=%s ORDER BY created_at,id",
                                    (project["id"],))]

    def apply(self, conn, project, task, event_key, action, target):
        QuestionStore(self.company).owner_command(conn, target.target_id, owner=project["owner_user"],
            revision=target.revision, manifest_digest=target.manifest_digest, event_key=event_key, action=action)
        text = ("연구 질문을 열었습니다. 이 질문 범위에서 근거를 모으고 후보를 비교합니다. "
                "실험이나 프로그램 과제 등록은 별도 승인 경로를 따릅니다."
                if action == "approve" else "연구 질문 제안을 닫았습니다.")
        conn.execute("UPDATE tasks SET status='completed',result=%s WHERE id=%s", (text, task["id"]))
        self.company._message(conn, project, task["id"], "director", "status", text)


def planning_tool(company, conn, project, task, arguments):
    if task["agent"] != "director":
        raise PolicyError("Planning tools require the director")
    require_enabled(company)
    store = QuestionStore(company)
    if arguments == {"action": "planning_status"}:
        return store.status(conn, project)
    if (arguments.get("action") != "planning_question_draft"
            or not {"action", "spec"} <= set(arguments) <= {"action", "spec", "program_id"}):
        raise PolicyError("Planning tools only draft questions or read status; the owner activates questions")
    snapshot = store.draft(conn, project, arguments["spec"], actor="director", program_id=arguments.get("program_id"))
    target = next(t for t in QuestionApprovalAdapter(company).targets(conn, project) if str(t.target_id) == snapshot["id"])
    if target.state == "pending_approval" and not conn.execute("""SELECT 1 FROM research_approval_bindings
            WHERE target_kind='research_question' AND target_id=%s AND manifest_digest=%s""",
            (target.target_id, target.manifest_digest)).fetchone():
        publish_approval(company, conn, project, task["id"], target,
            "연구 질문 승인 요청: " + snapshot["spec"]["question"]
            + "\n승인하면 이 질문으로 근거를 모으고 후보를 비교합니다. 실험·프로그램 권한은 포함하지 않습니다.\n"
            + json.dumps(snapshot["spec"], ensure_ascii=False, indent=2))
    return snapshot
```

`src/quant_company/company.py` 변경 세 곳:

1. `__init__` 어댑터 튜플:

```python
        from .research.controller import MissionApprovalAdapter
        from .research.planning_store import QuestionApprovalAdapter
        from .research.program_controller import ProgramApprovalAdapter

        self.research_approval_adapters = (MissionApprovalAdapter(self), ProgramApprovalAdapter(self),
                                           QuestionApprovalAdapter(self))
```

2. `_tool`의 `research_control` 분기, `mission_` 분기 다음:

```python
            if arguments.get("action", "").startswith("planning_"):
                from .research.planning_store import planning_tool

                return planning_tool(self, conn, self._project(conn, project_id, lock=False), task, arguments)
```

3. `runtime_context()`가 반환하는 `capabilities` dict의 `autonomous_research` 항목 다음에, flag가 켜졌을 때만 넣는다:

```python
                **({"research_planning": {
                    "enabled": True,
                    "usage": 'Director research_control: {action:"planning_question_draft",spec:<QuestionSpec>'
                             '[,program_id:<uuid>]} proposes a research question for owner approval; '
                             '{action:"planning_status"} reads questions, evidence sets and open gaps. '
                             'A question carries no experiment or program authority. Leave undetermined mandates, '
                             'budgets and data availability as unknown; never invent them.'}}
                   if self.settings.research_planning_enabled else {}),
```

flag가 꺼진 상태의 capability dict가 그대로인지 `test_disabled_planning_changes_nothing`과 기존 `tests/test_runtime_context.py`로 확인한다.

- [ ] **Step 4: 통과 확인**

Run: `uv run --frozen pytest -q tests/test_research_planning_questions.py tests/test_research_approvals.py tests/test_research_programs.py -k "not slow"`
Expected: 새 테스트 12개 포함 모두 통과. 기존 승인·프로그램 테스트 회귀 없음.

- [ ] **Step 5: 커밋**

```bash
git add src/quant_company/research/planning_store.py src/quant_company/config.py src/quant_company/company.py tests/test_research_planning_questions.py
git commit -m "Let the director draft owner-activated research questions" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 4: 제한 없는 corpus 함수 (`library.py`)

**Files:**
- Modify: `src/quant_company/research/library.py` (`available_sources` 아래에 추가, 기존 함수 불변)
- Test: `tests/test_research_planning_retrieval.py` (Task 5에서 이어 쓴다)

**Interfaces:**
- Produces: `program_corpus(company, conn, project, spec, program_id) -> list[dict]`, `project_corpus(company, conn, project, source_refs) -> list[dict]`. 각 행은 `id,title,uri,content,available_at,synthetic,work_id,literature_created_at,authority`이고 `authority`는 `explicit|predecessor|literature`.

- [ ] **Step 1: 실패하는 테스트 작성**

```python
"""Planning corpus and evidence sets on real PostgreSQL; synthetic originals, no feed or model calls."""

import json
from datetime import timedelta
from types import SimpleNamespace

from psycopg.types.json import Jsonb

from quant_company.company import fingerprint, now
from quant_company.research.library import available_sources, project_corpus

from .test_research_missions import Harness


def literature(company, project_id, key, *, title, idea="", market="", topic="research_validity",
               vintage="recent", kind="empirical", change="new", authors=("Example Author",), at=None,
               work=None, text=None):
    """Register a reviewed original in the same shape sync_library stores; no feed calls."""
    created = at or now()
    doc, work_id, url = "doc-" + key, work or "work-" + key, "https://example.org/" + key
    pages = [{"location": "PDF p.1", "text": text or f"Original text for {key}."}]
    brief = {"disposition": "publish", "reason": "fixture", "title": title, "authors": list(authors),
             "published_on": "2026-01-01", "kind": kind, "maturity": "working_paper", "market": market,
             "topic": topic, "vintage": vintage, "idea": idea, "data_period": "", "validation": "",
             "author_results": "", "costs_turnover": "", "limitations": [], "application": "", "change": change}
    digest = fingerprint(pages)
    content = json.dumps({"document_id": doc, "metadata": {"url": url}, "receipt": {}, "pages": pages,
                          "brief": brief, "critique": {"disposition": "pass"}, "content_digest": digest},
                         ensure_ascii=False)
    source_id = "literature:" + fingerprint([str(project_id), doc])
    with company.db.transaction() as conn:
        conn.execute("""INSERT INTO quant_feed_sources(id,config,config_digest,enabled)
            VALUES('fixture','{}','fixture',false) ON CONFLICT DO NOTHING""")
        conn.execute("INSERT INTO quant_feed_works(id) VALUES(%s) ON CONFLICT DO NOTHING", (work_id,))
        conn.execute("""INSERT INTO quant_feed_candidates(id,source_id,url,title,source_digest)
            VALUES(%s,'fixture',%s,%s,'fixture')""", (doc, url, title))
        conn.execute("""INSERT INTO quant_feed_documents(id,work_id,candidate_id,source_digest,content_digest,
            metadata,receipt,pages,brief,critique,created_at) VALUES(%s,%s,%s,'fixture',%s,%s,'{}',%s,%s,%s,%s)""",
            (doc, work_id, doc, digest, Jsonb({"url": url}), Jsonb(pages), Jsonb(brief),
             Jsonb({"disposition": "pass"}), created))
        conn.execute("""INSERT INTO sources(id,title,uri,content,available_at,project_id,approved,synthetic,metadata)
            VALUES(%s,%s,%s,%s,%s,%s,true,true,%s)""", (source_id, title, url, content, created, project_id,
            Jsonb({"kind": "literature", "document_id": doc, "work_id": work_id})))
        conn.execute("""INSERT INTO research_literature(source_id,project_id,document_id,work_id,content_digest,
            state,created_at) VALUES(%s,%s,%s,%s,%s,%s,%s)""", (source_id, project_id, doc, work_id, digest,
            "retracted" if change == "retraction" else "current", created))
    return source_id


def test_corpus_keeps_owner_and_project_boundaries(company):
    mine, other = Harness(company), company.ingest(event_key="fixture:other", text="Other", owner="UOTHER",
                                                     channel="COTHER", thread_ts="9.0")
    kept = literature(company, mine.project["project_id"], "mine", title="Momentum after costs")
    hidden = literature(company, other["project_id"], "other", title="Momentum after costs elsewhere")
    with company.db.transaction() as conn:
        rows = project_corpus(company, conn, company._project(conn, mine.project["project_id"]), [])
    assert {row["id"] for row in rows} == {kept}
    assert hidden not in {row["id"] for row in rows}


def test_corpus_has_no_recency_cap(company):
    h = Harness(company)
    old = literature(company, h.project["project_id"], "old", title="Momentum after costs",
                     at=now() - timedelta(days=40))
    for n in range(20):
        literature(company, h.project["project_id"], f"new{n}", title=f"Unrelated topic {n}",
                   at=now() - timedelta(days=1, minutes=n))
    with company.db.transaction() as conn:
        project = company._project(conn, h.project["project_id"])
        legacy = {row["id"] for row in available_sources(company, conn, project,
                  SimpleNamespace(source_ids=[], include_quant_feed=True))}
        corpus = {row["id"] for row in project_corpus(company, conn, project, [])}
    assert old not in legacy and old in corpus and len(corpus) == 21
```

- [ ] **Step 2: 실패 확인**

Run: `uv run --frozen pytest -q tests/test_research_planning_retrieval.py`
Expected: FAIL — `ImportError: cannot import name 'project_corpus'`

- [ ] **Step 3: 구현** (`library.py`, `available_sources` 다음)

```python
def _literature(company, conn, project, roles):
    sync_library(company, conn, project)
    for row in conn.execute("SELECT source_id FROM research_literature WHERE project_id=%s AND state='current'",
                            (project["id"],)):
        roles.setdefault(row["source_id"], "literature")


def _corpus_rows(conn, project, roles):
    rows = conn.execute("""SELECT s.id,s.title,s.uri,s.content,s.available_at,s.synthetic,l.work_id,
        l.created_at AS literature_created_at FROM sources s LEFT JOIN research_literature l ON l.source_id=s.id
        WHERE s.id=ANY(%s) AND s.approved AND s.available_at<=now() AND (s.project_id IS NULL OR s.project_id=%s)
        ORDER BY s.id""", (sorted(roles), project["id"])).fetchall()
    return [dict(row) | {"authority": roles[row["id"]]} for row in rows]


def program_corpus(company, conn, project, spec, program_id):
    """available_sources() authority without recency caps. Planning only; legacy callers are unchanged."""
    company._check_sources(conn, project["id"], spec.source_ids)
    roles = dict.fromkeys(spec.source_ids, "explicit")
    for row in conn.execute("""SELECT DISTINCT p.payload->>'source_id' AS source_id FROM research_mission_publications p
        JOIN research_mission_trials t ON t.id=p.trial_id JOIN research_missions m ON m.id=t.mission_id
        WHERE m.program_id=%s""", (program_id,)):
        roles.setdefault(row["source_id"], "predecessor")
    lineage_ids = [envelope.template.scientific_lineage.id for envelope in spec.envelopes
                   if envelope.template.scientific_lineage is not None]
    if lineage_ids:
        for row in conn.execute("""SELECT DISTINCT p.payload->>'source_id' AS source_id
            FROM research_mission_publications p JOIN research_scientific_lineage_trials l ON l.trial_id=p.trial_id
            JOIN research_scientific_lineages s ON s.id=l.lineage_id WHERE s.project_id=%s AND s.id=ANY(%s)""",
            (project["id"], lineage_ids)):
            roles.setdefault(row["source_id"], "predecessor")
    if spec.include_quant_feed:
        _literature(company, conn, project, roles)
    return _corpus_rows(conn, project, roles)


def project_corpus(company, conn, project, source_refs):
    company._check_sources(conn, project["id"], source_refs)
    roles = dict.fromkeys(source_refs, "explicit")
    _literature(company, conn, project, roles)
    return _corpus_rows(conn, project, roles)
```

- [ ] **Step 4: 통과 확인**

Run: `uv run --frozen pytest -q tests/test_research_planning_retrieval.py tests/test_research_library.py`
Expected: 모두 통과

- [ ] **Step 5: 커밋**

```bash
git add src/quant_company/research/library.py tests/test_research_planning_retrieval.py
git commit -m "Add uncapped planning corpus functions to the research library" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 5: evidence set 계산·고정 (`planning_retrieval.py`)

**Files:**
- Create: `src/quant_company/research/planning_retrieval.py`
- Test: `tests/test_research_planning_retrieval.py`

**Interfaces:**
- Consumes: Task 4 corpus 함수, Task 3 `open_gap`, Task 1 `QuestionSpec`·`GapRequest`.
- Produces: `POLICY`, `POLICY_DIGEST`, `tokens(text) -> list[str]`, `bm25(query, documents) -> dict[str, float]`, `compute(company, conn, question_id) -> dict`(쓰기는 `sync_library` 등록뿐), `freeze(company, conn, question_id) -> dict`(open·waiting 질문만, 멱등). 반환 dict 키: `question_id, question_revision, program_id, program_manifest_digest, corpus_digest, corpus_watermark, policy_digest, members, cards, notes, char_total, state, digest`(freeze는 `id` 추가).

- [ ] **Step 1: 실패하는 테스트 작성** (`tests/test_research_planning_retrieval.py`에 추가)

```python
import pytest

from quant_company.company import PolicyError
from quant_company.research.planning_retrieval import POLICY, compute, freeze, tokens
from quant_company.research.planning_store import QuestionStore

from .test_research_planning_questions import approve, spec
from .test_research_programs import program  # noqa: F401


@pytest.fixture
def planning(company):
    company.settings.research_planning_enabled = True
    return Harness(company)


def question(h, **changes):
    with h.company.db.transaction() as conn:
        return QuestionStore(h.company).draft(conn, h.company._project(conn, h.project["project_id"]),
                                              spec(**changes), actor="director")


def evidence(h, question_id):
    with h.company.db.transaction() as conn:
        return compute(h.company, conn, question_id)


def test_korean_bigrams_and_english_stopwords():
    assert tokens("The momentum of 모멘텀 전략") == ["momentum", "모멘", "멘텀", "전략"]


def test_related_old_literature_beats_unrelated_recent(planning):
    pid = planning.project["project_id"]
    related = literature(planning.company, pid, "rel", title="ETF momentum after trading costs",
                         idea="Momentum premium in Korean ETF", market="한국 ETF", at=now() - timedelta(days=60))
    for n in range(25):
        literature(planning.company, pid, f"noise{n}", title=f"Chinese housing credit {n}", idea="Housing")
    result = evidence(planning, question(planning)["id"])
    literature_members = [m for m in result["members"] if m["role"] != "explicit"]
    # Housing documents share no question term, so none is selected as related evidence.
    assert [m["source_id"] for m in literature_members] == [related]
    assert len(result["cards"]) <= POLICY["max_cards"]
    assert all(len(json.dumps(card, ensure_ascii=False, sort_keys=True)) <= POLICY["card_chars"]
               for card in result["cards"])


def test_counter_and_foundation_slots_are_reserved_when_present(planning):
    pid = planning.project["project_id"]
    for n in range(14):
        literature(planning.company, pid, f"mom{n}", title=f"Momentum ETF study {n}", idea="momentum momentum")
    counter = literature(planning.company, pid, "counter", title="Momentum fails after costs",
                         idea="replication failure", kind="replication")
    classic = literature(planning.company, pid, "classic", title="Classic momentum", idea="momentum",
                         vintage="classic")
    result = evidence(planning, question(planning, counter_terms=["failure", "replication"])["id"])
    roles = {m["source_id"]: m["role"] for m in result["members"]}
    assert roles[counter] == "counter" and roles[classic] == "foundation"
    assert result["notes"]["counter_reserved"] and result["notes"]["foundation_reserved"]


def test_required_sources_over_budget_make_oversize_and_one_gap(planning):
    company, pid = planning.company, planning.project["project_id"]
    refs = []
    for n in range(4):
        refs.append(f"fixture:big{n}")
        company.put_source(source_id=refs[-1], title=f"Large original {n}", uri=f"fixture://big{n}",
                           content=("x" * 99_000) + f" {n}", available_at=now() - timedelta(days=1),
                           project_id=pid, approved=True, synthetic=True)
    opened = approve(planning, question(planning, source_refs=refs))
    for _ in range(2):
        with company.db.transaction() as conn:
            frozen = freeze(company, conn, opened["id"])
    assert frozen["state"] == "oversize"
    assert {m["source_id"] for m in frozen["members"] if m["full_text"]} == set(refs)
    with company.db.transaction() as conn:
        gaps = conn.execute("SELECT kind,resolver FROM research_gaps WHERE question_id=%s", (opened["id"],)).fetchall()
    assert [(g["kind"], g["resolver"]) for g in gaps] == [("oversize", "owner")]


def test_same_input_same_digest_and_frozen_set_survives_new_literature(planning):
    pid = planning.project["project_id"]
    literature(planning.company, pid, "a", title="Momentum ETF costs", idea="momentum")
    opened = approve(planning, question(planning))
    first = evidence(planning, opened["id"])
    assert evidence(planning, opened["id"])["digest"] == first["digest"]
    with planning.company.db.transaction() as conn:
        frozen = freeze(planning.company, conn, opened["id"])
    literature(planning.company, pid, "b", title="Unrelated housing", idea="housing")
    assert evidence(planning, opened["id"])["corpus_digest"] != frozen["corpus_digest"]
    with planning.company.db.transaction() as conn:
        again = conn.execute("SELECT digest,members FROM research_evidence_sets WHERE id=%s", (frozen["id"],)).fetchone()
    assert again["digest"] == frozen["digest"] and again["members"] == frozen["members"]


def test_freeze_requires_an_owner_activated_question(planning):
    with pytest.raises(PolicyError, match="owner-activated"):
        with planning.company.db.transaction() as conn:
            freeze(planning.company, conn, question(planning)["id"])


def test_plain_sources_get_excerpt_cards(planning):
    planning.company.put_source(source_id="fixture:plain", title="Plain momentum note", uri="fixture://plain",
        content="Momentum note without a structured brief.", available_at=now() - timedelta(days=1),
        project_id=planning.project["project_id"], approved=True, synthetic=True)
    result = evidence(planning, question(planning, source_refs=["fixture:plain"])["id"])
    card = next(c for c in result["cards"] if c["source_id"] == "fixture:plain")
    assert card["excerpt"].startswith("Momentum note") and card["role"] == "explicit"


def test_no_matching_terms_keeps_required_members(planning):
    planning.company.put_source(source_id="fixture:only", title="Required note", uri="fixture://only",
        content="Required.", available_at=now() - timedelta(days=1), project_id=planning.project["project_id"],
        approved=True, synthetic=True)
    literature(planning.company, planning.project["project_id"], "z", title="Housing", idea="housing")
    result = evidence(planning, question(planning, source_refs=["fixture:only"], search_terms_en=["zebra"],
                                         search_terms_ko=[])["id"])
    assert [m["source_id"] for m in result["members"]] == ["fixture:only"]
    assert result["state"] == "ready"


def test_bound_question_reports_refs_outside_program(program):  # noqa: F811
    h = program
    h.company.settings.research_planning_enabled = True
    h.company.put_source(source_id="fixture:outside", title="Project note", uri="fixture://outside",
        content="Project note.", available_at=now() - timedelta(days=1), project_id=h.project["project_id"],
        approved=True, synthetic=True)
    with h.company.db.transaction() as conn:
        drafted = QuestionStore(h.company).draft(conn, h.company._project(conn, h.project["project_id"]),
            spec(source_refs=["fixture:outside"]), actor="director", program_id=h.program_id)
        result = compute(h.company, conn, drafted["id"])
    assert result["notes"]["excluded_refs"] == ["fixture:outside"]
    assert "fixture:outside" not in {m["source_id"] for m in result["members"]}
    assert {m["source_id"] for m in result["members"]} == {"fixture:baseline"}
    assert result["program_manifest_digest"] == h.digest
```

- [ ] **Step 2: 실패 확인**

Run: `uv run --frozen pytest -q tests/test_research_planning_retrieval.py`
Expected: FAIL — `ModuleNotFoundError: quant_company.research.planning_retrieval`

- [ ] **Step 3: 구현**

```python
"""Question-driven evidence selection under a fixed, digested policy. No model calls."""

import json
import math
import re
from collections import Counter

from psycopg.types.json import Jsonb

from ..company import PolicyError, as_json, fingerprint, stable
from .library import program_corpus, project_corpus
from .missions import typed
from .planning_contracts import GapRequest, QuestionSpec
from .planning_store import open_gap
from .program_contracts import ResearchProgram

POLICY = {
    "version": 1,
    "bm25": {"k1": 1.2, "b": 0.75},
    "bonus": {"market": 0.5, "topic": 0.5, "counter_kind": 0.5},
    "max_cards": 12, "card_chars": 3000, "chunk_chars": 12000,
    "full_text": {"literature_max": 5, "chars": 300000},
    "brief_fields": ["title", "idea", "market", "data_period", "validation", "author_results",
                     "costs_turnover", "limitations", "application"],
    "market_terms": {"kr_etf": ["한국", "국내", "krx", "kospi", "korea", "korean", "etf"],
                     "kr_stock": ["한국", "국내", "krx", "kospi", "kosdaq", "korea", "korean", "주식", "stock",
                                  "equit"]},
}
POLICY_DIGEST = fingerprint(POLICY)
STOPWORDS = frozenset("""a an and are as at be by for from has have in into is it its of on or that the their
    this to was were which with we our using use based via than then these those""".split())
WORD = re.compile(r"[a-z0-9]+")
HANGUL = re.compile(r"[가-힣]+")
CARD_FIELDS = ("title", "kind", "maturity", "market", "topic", "vintage", "published_on", "idea", "data_period",
               "validation", "author_results", "costs_turnover", "limitations", "application", "change")
NOTE = "Navigation only. Cite the original file, never this card."


def tokens(text):
    """English words without stopwords; Hangul as character bigrams (not morphological analysis)."""
    text = (text or "").lower()
    result = [word for word in WORD.findall(text) if len(word) > 1 and word not in STOPWORDS]
    for run in HANGUL.findall(text):
        result.extend([run] if len(run) == 1 else [run[i:i + 2] for i in range(len(run) - 1)])
    return result


def bm25(query, documents):
    k1, b = POLICY["bm25"]["k1"], POLICY["bm25"]["b"]
    if not documents:
        return {}
    average = sum(len(value) for value in documents.values()) / len(documents) or 1
    frequency = Counter(term for value in documents.values() for term in set(value))
    scores = {}
    for key, value in documents.items():
        terms, score = Counter(value), 0.0
        for term in set(query):
            if term in terms:
                idf = math.log(1 + (len(documents) - frequency[term] + 0.5) / (frequency[term] + 0.5))
                score += idf * terms[term] * (k1 + 1) / (terms[term] + k1 * (1 - b + b * len(value) / average))
        scores[key] = score
    return scores


def _brief(row):
    try:
        content = json.loads(row["content"])
    except ValueError:
        return None
    brief = content.get("brief") if isinstance(content, dict) else None
    return brief if isinstance(brief, dict) else None


def _text(value):
    return "; ".join(map(str, value)) if isinstance(value, list) else str(value or "")


def _index(row, brief):
    if brief:
        return " ".join(_text(brief.get(name)) for name in POLICY["brief_fields"])
    return (row["title"] or "") + " " + row["content"][:4000]


def _fit(value, limit):
    while len(json.dumps(value, ensure_ascii=False, sort_keys=True)) > limit:
        key = max((k for k, v in value.items() if isinstance(v, str) and k not in {"source_id", "role", "use"}),
                  key=lambda k: (len(value[k]), k), default=None)
        if key is None or len(value[key]) <= 1:
            raise PolicyError("Evidence card cannot fit its limit")
        value[key] = value[key][: len(value[key]) * 4 // 5]
    return value


def _question(conn, question_id):
    row = conn.execute("""SELECT q.*,r.payload AS spec FROM research_questions q JOIN research_question_revisions r
        ON r.question_id=q.id AND r.revision=q.current_revision WHERE q.id=%s""", (question_id,)).fetchone()
    if not row:
        raise PolicyError("Unknown research question")
    return row


def _corpus(company, conn, question, spec):
    project = company._project(conn, question["project_id"], lock=False)
    if question["program_id"] is None:
        return None, project_corpus(company, conn, project, spec.source_refs), []
    program = conn.execute("SELECT * FROM research_programs WHERE id=%s", (question["program_id"],)).fetchone()
    program_spec = typed(ResearchProgram, program["spec"])
    if fingerprint(program_spec.model_dump(mode="json")) != program["manifest_digest"]:
        raise PolicyError("Program specification changed")
    rows = program_corpus(company, conn, project, program_spec, program["id"])
    # Question references can only point inside the program authority; outside ones are reported, not admitted.
    known = {row["id"]: row for row in rows}
    for ref in spec.source_refs:
        if ref in known and known[ref]["authority"] == "literature":
            known[ref]["authority"] = "explicit"
    return program, rows, sorted(set(spec.source_refs) - known.keys())


def compute(company, conn, question_id):
    question = _question(conn, question_id)
    spec = QuestionSpec.model_validate(question["spec"])
    program, rows, excluded = _corpus(company, conn, question, spec)
    briefs = {row["id"]: _brief(row) for row in rows}
    documents = {row["id"]: tokens(_index(row, briefs[row["id"]])) for row in rows}
    related = bm25(tokens(" ".join([spec.question, *spec.search_terms_en, *spec.search_terms_ko,
                                    *spec.required_data_fields])), documents)
    counter = bm25(tokens(" ".join(spec.counter_terms)), documents) if spec.counter_terms else {}
    market_terms = POLICY["market_terms"].get(spec.market, [])
    scores = {}
    for row in rows:
        brief = briefs[row["id"]] or {}
        base = round(related.get(row["id"], 0.0), 6)
        market = POLICY["bonus"]["market"] if base and any(t in _text(brief.get("market")).lower()
                                                           for t in market_terms) else 0.0
        topic = POLICY["bonus"]["topic"] if base and brief.get("topic") in spec.topics else 0.0
        against = round(counter.get(row["id"], 0.0), 6)
        if against and (brief.get("kind") == "replication" or brief.get("change") in {"correction", "retraction"}):
            against = round(against + POLICY["bonus"]["counter_kind"], 6)
        scores[row["id"]] = {"bm25": base, "market": market, "topic": topic,
                             "total": round(base + market + topic, 6), "counter": against}

    def order(row):
        stamp = row["literature_created_at"] or row["available_at"]
        return (-scores[row["id"]]["total"], -stamp.timestamp(), row["id"])

    required = sorted((row for row in rows if row["authority"] in {"predecessor", "explicit"}),
                      key=lambda row: (row["authority"] != "predecessor", row["id"]))
    latest = {}
    for row in sorted((r for r in rows if r["authority"] == "literature"),
                      key=lambda r: (r["literature_created_at"], r["id"]), reverse=True):
        latest.setdefault(row["work_id"] or row["id"], row)
    ranked = sorted(latest.values(), key=order)
    slots, roles, chosen = max(0, POLICY["max_cards"] - len(required)), {}, []
    counter_pick = next((row for row in sorted(ranked, key=lambda r: (-scores[r["id"]]["counter"], order(r)))
                         if scores[row["id"]]["counter"] > 0), None)
    foundation_pick = next((row for row in ranked if (briefs[row["id"]] or {}).get("vintage") == "classic"
                            and scores[row["id"]]["total"] > 0), None)
    for row, role in ((counter_pick, "counter"), (foundation_pick, "foundation")):
        if row is not None and row["id"] not in roles and len(chosen) < slots:
            roles[row["id"]] = role
            chosen.append(row)
    for row in ranked:
        if len(chosen) >= slots:
            break
        if row["id"] not in roles and scores[row["id"]]["total"] > 0:
            roles[row["id"]] = "related"
            chosen.append(row)
    chosen.sort(key=order)
    budget, full = POLICY["full_text"]["chars"], {row["id"] for row in required}
    used = sum(len(row["content"]) for row in required)
    taken = 0
    for row in chosen:
        if taken < POLICY["full_text"]["literature_max"] and used + len(row["content"]) <= budget:
            full.add(row["id"])
            used += len(row["content"])
            taken += 1
    authors = {row["id"]: {str(a).strip().lower() for a in (briefs[row["id"]] or {}).get("authors", [])[:20]
                           if str(a).strip()} for row in [*required, *chosen]}
    members, cards = [], []
    for rank, row in enumerate([*required, *chosen], start=1):
        chars = len(row["content"])
        member = {"source_id": row["id"], "work_id": row["work_id"], "content_fingerprint": fingerprint(row["content"]),
                  "role": row["authority"] if row["authority"] != "literature" else roles[row["id"]],
                  "rank": rank, "score": scores[row["id"]], "chars": chars,
                  "chunks": math.ceil(chars / POLICY["chunk_chars"]), "full_text": row["id"] in full,
                  "file": ("planning/sources/" + fingerprint(row["id"])[:24] + ".txt") if row["id"] in full else None}
        members.append(member)
        if len(cards) < POLICY["max_cards"]:
            brief = briefs[row["id"]]
            card = {"source_id": row["id"], "role": member["role"], "rank": rank, "full_text": member["full_text"],
                    "chars": chars, "chunks": member["chunks"], "use": NOTE}
            if brief:
                card |= {name: _text(brief.get(name)) for name in CARD_FIELDS if brief.get(name)}
                card["authors"] = _text((brief.get("authors") or [])[:3])
            else:
                card |= {"title": row["title"] or row["id"], "excerpt": row["content"][:1500]}
            shared = sorted(other for other, names in authors.items()
                            if other != row["id"] and names & authors[row["id"]])
            if shared:
                card["shared_authors_with"] = ", ".join(shared[:5])
            cards.append(_fit(card, POLICY["card_chars"]))
    required_chars = sum(len(row["content"]) for row in required)
    value = {"question_id": str(question_id), "question_revision": question["current_revision"],
             "program_id": str(program["id"]) if program else None,
             "program_manifest_digest": program["manifest_digest"] if program else None,
             "corpus_digest": fingerprint(sorted([row["id"], fingerprint(row["content"]), row["authority"]]
                                                 for row in rows)),
             "corpus_watermark": max((row["available_at"] for row in rows), default=None),
             "policy_digest": POLICY_DIGEST, "members": members, "cards": cards,
             "notes": {"excluded_refs": excluded, "counter_reserved": counter_pick is not None and
                       roles.get(counter_pick["id"]) == "counter",
                       "foundation_reserved": foundation_pick is not None and
                       roles.get(foundation_pick["id"]) == "foundation",
                       "literature_full_text": taken, "required_chars": required_chars},
             "char_total": sum(m["chars"] for m in members if m["full_text"]),
             "state": "oversize" if required_chars > budget else "ready"}
    value = as_json(value)
    value["digest"] = fingerprint(value)
    return value


def freeze(company, conn, question_id):
    question = conn.execute("SELECT * FROM research_questions WHERE id=%s FOR UPDATE", (question_id,)).fetchone()
    if not question or question["state"] not in {"open", "waiting"}:
        raise PolicyError("Evidence sets are frozen only for owner-activated questions")
    value = compute(company, conn, question_id)
    identity = stable(f"evidence-set:{question_id}:{value['question_revision']}:{value['corpus_digest']}:"
                      f"{value['policy_digest']}")
    conn.execute("""INSERT INTO research_evidence_sets(id,question_id,question_revision,program_id,
        program_manifest_digest,corpus_digest,corpus_watermark,policy_digest,members,cards,notes,char_total,digest,state)
        VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING""",
        (identity, question_id, value["question_revision"], value["program_id"], value["program_manifest_digest"],
         value["corpus_digest"], value["corpus_watermark"], value["policy_digest"], Jsonb(value["members"]),
         Jsonb(value["cards"]), Jsonb(value["notes"]), value["char_total"], value["digest"], value["state"]))
    row = conn.execute("SELECT digest FROM research_evidence_sets WHERE id=%s", (identity,)).fetchone()
    if row["digest"] != value["digest"]:
        raise PolicyError("Evidence set changed under the same corpus and policy")
    if value["state"] == "oversize":
        open_gap(conn, question["project_id"], GapRequest(kind="oversize",
            subject=f"question:{question_id}/revision:{value['question_revision']}", resolver="owner",
            required_evidence="Narrow the question or split required sources to fit 300,000 characters",
            recheck_on="A new question revision or program source list"),
            dependency_fingerprint=value["corpus_digest"], question_id=question_id, evidence_set_id=identity)
    return value | {"id": identity}
```

- [ ] **Step 4: 통과 확인**

Run: `uv run --frozen pytest -q tests/test_research_planning_retrieval.py && uv run --frozen ruff check src/quant_company/research/planning_retrieval.py tests/test_research_planning_retrieval.py`
Expected: 모두 통과, ruff 통과

- [ ] **Step 5: 커밋**

```bash
git add src/quant_company/research/planning_retrieval.py tests/test_research_planning_retrieval.py
git commit -m "Select question-driven evidence sets with a digested policy" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 6: `ProgramStore` 공통 helper 추출 (동작 불변)

**Files:**
- Modify: `src/quant_company/research/programs.py` (`validate_proposal`, `_assessment`, `decide`)
- Modify: `src/quant_company/research/scientific_lineages.py` (`reserve`)
- Test: 기존 연구 테스트 + `tests/test_research_planning_triage.py`의 helper 단위 테스트

**Interfaces:**
- Produces (programs.py): 상수 `MISSING_PACKET`, `CONDITIONAL_SOURCE`, `DUPLICATE_TASK`, `BUDGET_EXHAUSTED`, `PARALLEL_LIMIT`; 함수 `proposal_mode_error(envelope, mode) -> str | None`; 메서드 `ProgramStore.conditional_source(conn, source_ids) -> bool`, `ProgramStore.task_identity(program_id, payload) -> (str, str)`(staticmethod), `ProgramStore.packet_required(envelope, *, exploratory) -> bool`, `ProgramStore.envelope_packet(program, envelope) -> DataEvidencePacket | None`, `ProgramStore.capacity_error(conn, program_id, spec) -> str | None`.
- Produces (scientific_lineages.py): `LINEAGE_EXHAUSTED`, `exhausted(conn, row, authority) -> bool`.

- [ ] **Step 1: 리팩터링 전 기준선 기록**

Run: `uv run --frozen pytest -q tests/test_research_programs.py tests/test_research_conditional.py tests/test_research_data_scope.py tests/test_research_policy_compatibility.py tests/test_research_program_e2e.py tests/test_research_program_cancel.py tests/test_research_data_output.py tests/test_research_data_response.py tests/test_research_program_temporal.py 2>&1 | tail -1`
Expected: 통과 수를 기록한다(예: `N passed, M skipped`). 이 숫자가 Step 4의 기준이다.

- [ ] **Step 2: helper 단위 테스트 작성** (`tests/test_research_planning_triage.py` 새 파일)

```python
"""Planning triage on real PostgreSQL; synthetic programs, packets and employees. No model calls."""

from quant_company.research.programs import (
    BUDGET_EXHAUSTED,
    CONDITIONAL_SOURCE,
    DUPLICATE_TASK,
    MISSING_PACKET,
    ProgramStore,
    proposal_mode_error,
)
from quant_company.research.scientific_lineages import LINEAGE_EXHAUSTED

from .test_research_conditional import conditional  # noqa: F401
from .test_research_programs import program  # noqa: F401


def test_shared_messages_are_the_legacy_messages():
    assert MISSING_PACKET == "Real data readiness requires a verified input evidence packet"
    assert CONDITIONAL_SOURCE == "Conditional source evidence cannot establish original replication or transfer conditions"
    assert DUPLICATE_TASK == "Task already considered; use prior evidence to change the question"
    assert BUDGET_EXHAUSTED == "Program budget exhausted"
    assert LINEAGE_EXHAUSTED == "Cumulative scientific lineage budget exhausted"


def test_mode_rule_follows_the_signed_policy(conditional):  # noqa: F811
    envelope = conditional.program_spec.envelopes[0]
    assert proposal_mode_error(envelope, "novel_hypothesis") is None
    assert proposal_mode_error(envelope, "market_transfer") == "Conditional retrospective research admits novel hypotheses only"


def test_capacity_and_packet_helpers_read_without_side_effects(conditional):  # noqa: F811
    store = ProgramStore(conditional.company)
    with conditional.company.db.transaction() as conn:
        _, row, spec = store.locked(conn, conditional.program_id, active=True)
        envelope = spec.envelopes[0]
        assert store.capacity_error(conn, conditional.program_id, spec) is None
        assert store.packet_required(envelope, exploratory=False) is True
        assert store.envelope_packet(row, envelope) is not None
        before = conn.execute("SELECT count(*) AS n FROM research_program_tasks").fetchone()["n"]
        identity, digest = store.task_identity(conditional.program_id, {"title": "x"})
        assert conn.execute("SELECT count(*) AS n FROM research_program_tasks").fetchone()["n"] == before
        assert len(digest) == 64 and identity
```

- [ ] **Step 3: 구현**

`programs.py` 모듈 상단(import 다음):

```python
MISSING_PACKET = "Real data readiness requires a verified input evidence packet"
CONDITIONAL_SOURCE = "Conditional source evidence cannot establish original replication or transfer conditions"
DUPLICATE_TASK = "Task already considered; use prior evidence to change the question"
BUDGET_EXHAUSTED = "Program budget exhausted"
PARALLEL_LIMIT = "Program parallel mission limit reached"


def proposal_mode_error(envelope, mode):
    """Signed data-policy limits on the task mode; shared with planning triage (T4)."""
    policy = envelope.template.data_policy
    if policy is not None and policy.mode == "frozen_vintage_retrospective" and mode != "novel_hypothesis":
        return "Conditional retrospective research admits novel hypotheses only"
    if envelope.template.data.policy is not None and mode == "exact_replication":
        return "Retrospective exploration cannot claim exact replication"
    return None
```

`ProgramStore`에 메서드 추가:

```python
    def conditional_source(self, conn, source_ids):
        return conn.execute("""SELECT 1 FROM sources WHERE id=ANY(%s)
            AND (metadata->'research_scope'->>'result_scope'='conditional_retrospective_development'
                 OR metadata->'data_policy'->>'result_use'='hypothesis_generation_only')""",
            (list(source_ids),)).fetchone() is not None

    @staticmethod
    def task_identity(program_id, payload):
        digest = fingerprint(payload)
        return stable(f"program-task:{program_id}:{digest}"), digest

    def packet_required(self, envelope, *, exploratory):
        spec = envelope.template
        profile = profile_for(self.company, spec)
        return spec.schema_version == 3 or exploratory or not profile.public_profile.fixture_only

    def envelope_packet(self, program, envelope):
        found = load_packets(self.company, program, {envelope.name: envelope}).get(envelope.name)
        return None if found is None else found[0]

    def capacity_error(self, conn, program_id, spec):
        """Program budget and concurrency; shared by decide and planning triage (T9)."""
        used = self.usage(conn, program_id)
        count = conn.execute("SELECT count(*) AS n FROM research_missions WHERE program_id=%s",
                             (program_id,)).fetchone()["n"]
        if (count >= spec.max_missions or used["trials"] >= spec.max_total_trials
                or used["compute_seconds"] >= spec.max_compute_seconds):
            return BUDGET_EXHAUSTED
        active = [m for m in self.snapshot(conn, program_id)["missions"]
                  if m["stage"] not in {"owner_review", "cancelled"}]
        if len(active) >= spec.max_parallel_missions:
            return PARALLEL_LIMIT
        return None
```

`validate_proposal` 본문을 다음으로 바꾼다(순서·문구 동일):

```python
    def validate_proposal(self, conn, program_id, value, *, actor):
        project, row, spec = self.locked(conn, program_id, active=True)
        MissionStore(self.company)._actor(actor, "researcher_kr")
        proposal = typed(ResearchTaskProposal, value)
        envelope = next((e for e in spec.envelopes if e.name == proposal.envelope), None)
        if envelope is None:
            raise PolicyError("Task selects an unapproved envelope")
        if (error := proposal_mode_error(envelope, proposal.mode)) is not None:
            raise PolicyError(error)
        allowed = {s["id"] for s in available_sources(self.company, conn, project, spec, program_id)}
        if not set(proposal.source_ids) <= allowed:
            raise PolicyError("Task cites sources outside the program library")
        require_current(conn, proposal.source_ids)
        if self.conditional_source(conn, proposal.source_ids) and proposal.mode != "novel_hypothesis":
            raise PolicyError(CONDITIONAL_SOURCE)
        verify_citations(conn, proposal)
        for prior in proposal.predecessor_mission_ids:
            if not conn.execute("SELECT 1 FROM research_missions WHERE id=%s AND program_id=%s",
                                (prior, program_id)).fetchone():
                raise PolicyError("Predecessor is outside this program")
        payload = proposal.model_dump(mode="json")
        identity, digest = self.task_identity(program_id, payload)
        if conn.execute("SELECT 1 FROM research_program_tasks WHERE id=%s", (identity,)).fetchone():
            raise PolicyError(DUPLICATE_TASK)
        return identity, payload, digest
```

`_assessment`의 packet 블록(`profile = profile_for(...)`부터 `require_admissible_gaps(packet, spec)`까지)을 다음으로 바꾼다:

```python
        if self.packet_required(envelope, exploratory=value.decision == "exploratory_only"):
            packet = self.envelope_packet(program, envelope)
            if packet is None:
                raise PolicyError(MISSING_PACKET)
            if spec.schema_version == 3 and value.packet_digest != fingerprint(packet.model_dump(mode="json")):
                raise PolicyError("Independent data assessment cites a different evidence packet")
            require_admissible_gaps(packet, spec)
```

`decide`의 예산·동시 실행 블록(`used = self.usage(...)`부터 parallel `raise`까지)을 다음으로 바꾼다:

```python
        if (error := self.capacity_error(conn, program_id, spec)) is not None:
            raise PolicyError(error)
```

`scientific_lineages.py`:

```python
LINEAGE_EXHAUSTED = "Cumulative scientific lineage budget exhausted"


def exhausted(conn, row, authority):
    """No signed trial capacity left; shared by reservations and planning triage (T8)."""
    return usage(conn, authority.id) >= min(row["trial_limit"], authority.max_total_trials)
```

`reserve`의 마지막 검사를 바꾼다:

```python
    if exhausted(conn, row, authority):
        raise PolicyError(LINEAGE_EXHAUSTED)
```

- [ ] **Step 4: 회귀 확인**

Run: Step 1과 같은 명령 + `uv run --frozen pytest -q tests/test_research_planning_triage.py`
Expected: Step 1과 같은 통과·건너뜀 수, 새 테스트 3개 통과

- [ ] **Step 5: 커밋**

```bash
git add src/quant_company/research/programs.py src/quant_company/research/scientific_lineages.py tests/test_research_planning_triage.py
git commit -m "Share program admission checks with planning triage" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 7: triage T1~T9 (`planning_triage.py`)

**Files:**
- Create: `src/quant_company/research/planning_triage.py`
- Test: `tests/test_research_planning_triage.py`

**Interfaces:**
- Consumes: Task 1 계약, Task 6 helper, `library.require_current`·`verify_citations`, `data_evidence.require_admissible_gaps`, `scientific_lineages.require_authority`·`usage`·`exhausted`.
- Produces: `triage(company, conn, value, *, admission, program_id=None, evidence_set_digest=None) -> TriageResult`. 쓰기 없음(호출자가 잡은 행 잠금만).

- [ ] **Step 1: 실패하는 테스트 작성** (같은 파일에 추가)

```python
import json

import pytest

from quant_company.company import PolicyError, fingerprint
from quant_company.research.data_evidence import DataEvidencePacket
from quant_company.research.planning_triage import triage

from .test_research_conditional import conditional_ready, conditional_task
from .test_research_planning_contracts import candidate
from .test_research_programs import task_proposal as legacy_proposal


def run(h, admission=("fixture:baseline",), **changes):
    with h.company.db.transaction() as conn:
        return triage(h.company, conn, candidate(**changes), admission=set(admission), program_id=h.program_id)


def failed(result):
    return {check.code: check for check in result.checks if check.status == "fail"}


def test_conditional_candidate_is_eligible(conditional):  # noqa: F811
    result = run(conditional)
    assert result.outcome == "eligible", failed(result)
    assert result.basis["engine_card"] == "kr-research-python-v2@1" and result.basis["packet_digest"]


@pytest.mark.parametrize("change,code,outcome,kind", [
    ({"mode": "market_transfer"}, "T4", "out_of_scope", "data_policy"),
    ({"estimand": "mean-effect"}, "T5", "needs_evaluator", "evaluator"),
    ({"execution_timing": "same_day_close_fill"}, "T6", "needs_evaluator", "execution_timing"),
    ({"envelope": "missing"}, "T3", "needs_program", "program_missing"),
])
def test_each_rule_violation_has_outcome_and_gap(conditional, change, code, outcome, kind):  # noqa: F811
    result = run(conditional, **change)
    check = failed(result)[code]
    assert (check.outcome, check.gap.kind) == (outcome, kind)
    assert result.outcome == outcome


def test_outside_admission_is_invalid_and_unbound_candidates_have_no_experiment_path(conditional):  # noqa: F811
    assert run(conditional, admission=()).outcome == "invalid"
    with conditional.company.db.transaction() as conn:
        unbound = triage(conditional.company, conn, candidate(envelope=None), admission={"fixture:baseline"})
    assert unbound.outcome == "unbound"
    assert all(c.status == "not_applicable" for c in unbound.checks[2:])


def test_packet_gaps_match_the_data_assessment_message(conditional):  # noqa: F811
    from quant_company.research.programs import ProgramStore

    registry = conditional.company.settings.research_data_evidence_file
    packet = json.loads(registry.read_text())["packets"][0] | {"blocking_gaps": ["Synthetic unresolved source"]}
    registry.write_text(json.dumps({"packets": [packet]}))
    check = failed(run(conditional))["T7"]
    assert check.outcome == "needs_data" and check.gap.resolver == "operator"
    # The data employee cites the same (changed) packet, so the legacy path reaches the same gap rule.
    ready = conditional_ready(conditional, packet_digest=fingerprint(
        DataEvidencePacket.model_validate(packet).model_dump(mode="json")))
    store = ProgramStore(conditional.company)
    with pytest.raises(PolicyError) as legacy:
        with conditional.company.db.transaction() as conn:
            task_id = store.propose(conn, conditional.program_id, legacy_proposal(mode="novel_hypothesis"),
                                    actor="researcher_kr")
            store.assess(conn, conditional.program_id, task_id, ready, actor="data")
    assert str(legacy.value) == check.message == "Data evidence packet still records blocking gaps"


def test_duplicate_and_budget_match_the_selection_messages(conditional):  # noqa: F811
    from quant_company.research.planning_contracts import PlanningCandidate, task_proposal
    from quant_company.research.programs import ProgramStore

    value = candidate()
    with conditional.company.db.transaction() as conn:
        ProgramStore(conditional.company).propose(conn, conditional.program_id,
            task_proposal(PlanningCandidate.model_validate(value)).model_dump(mode="json"), actor="researcher_kr")
    assert failed(run(conditional))["T8"].outcome == "duplicate"
    conditional_task(conditional)  # max_missions=1 is now used
    check = failed(run(conditional, title="Another fixture candidate"))["T9"]
    assert check.message == "Program budget exhausted" and check.gap.kind == "program_budget"


def test_lineage_exhaustion_matches_reservation_rule(conditional, monkeypatch):  # noqa: F811
    import quant_company.research.scientific_lineages as lineages

    monkeypatch.setattr(lineages, "usage", lambda conn, lineage_id: 4)
    check = failed(run(conditional))["T8"]
    assert check.message == "Cumulative scientific lineage budget exhausted"
    assert check.gap.kind == "lineage_budget"


def test_stale_source_after_correction(conditional):  # noqa: F811
    with conditional.company.db.transaction() as conn:
        conn.execute("""INSERT INTO quant_feed_sources(id,config,config_digest,enabled)
            VALUES('fixture','{}','fixture',false) ON CONFLICT DO NOTHING""")
        conn.execute("INSERT INTO quant_feed_works(id) VALUES('w') ON CONFLICT DO NOTHING")
        conn.execute("""INSERT INTO quant_feed_candidates(id,source_id,url,title,source_digest)
            VALUES('d','fixture','https://example.org/d','d','x')""")
        conn.execute("""INSERT INTO quant_feed_documents(id,work_id,candidate_id,source_digest,content_digest,metadata,
            receipt,pages) VALUES('d','w','d','x','y','{}','{}','[]')""")
        conn.execute("""INSERT INTO research_literature(source_id,project_id,document_id,work_id,content_digest,state)
            VALUES('fixture:baseline',%s,'d','w','y','superseded')""", (conditional.project["project_id"],))
    check = failed(run(conditional))["T1"]
    assert check.outcome == "stale" and check.gap.resolver == "employee"
```

- [ ] **Step 2: 실패 확인**

Run: `uv run --frozen pytest -q tests/test_research_planning_triage.py`
Expected: FAIL — `ModuleNotFoundError: quant_company.research.planning_triage`

- [ ] **Step 3: 구현**

```python
"""Deterministic candidate pre-checks (T1-T9). Shares ProgramStore rules; no model calls, no writes."""

from ..company import PolicyError, fingerprint
from .data_evidence import require_admissible_gaps
from .library import require_current, verify_citations
from .planning_contracts import (
    GapRequest,
    PlanningCandidate,
    TriageCheck,
    TriageResult,
    engine_card,
    engine_card_key,
    summarize,
    task_proposal,
)
from .programs import CONDITIONAL_SOURCE, DUPLICATE_TASK, MISSING_PACKET, ProgramStore, proposal_mode_error
from .scientific_lineages import LINEAGE_EXHAUSTED, exhausted, require_authority, usage


def passed(code, message=""):
    return TriageCheck(code=code, status="pass", message=message[:600])


def failed(code, outcome, message, gap=None):
    return TriageCheck(code=code, status="fail", outcome=outcome, message=str(message)[:600], gap=gap)


def skipped(code, message, status="not_applicable"):
    return TriageCheck(code=code, status=status, message=message[:600])


def gap(kind, subject, resolver, required, recheck):
    return GapRequest(kind=kind, subject=subject[:300], resolver=resolver, required_evidence=required,
                      recheck_on=recheck)


def result(checks, basis, *, bound):
    ordered = [checks[f"T{n}"] for n in range(1, 10)]
    return TriageResult(outcome=summarize(ordered, bound=bound), checks=ordered, basis=basis)


def triage(company, conn, value, *, admission, program_id=None, evidence_set_digest=None):
    candidate = PlanningCandidate.model_validate(value)
    checks, basis = {}, {"evidence_set_digest": evidence_set_digest, "triage_version": 1}
    outside = sorted(set(candidate.source_ids) - set(admission))
    if outside:
        checks["T1"] = failed("T1", "invalid", "Candidate cites sources outside the frozen evidence set: "
                              + ", ".join(outside[:5]))
    else:
        try:
            require_current(conn, candidate.source_ids)
            checks["T1"] = passed("T1")
        except PolicyError as exc:
            checks["T1"] = failed("T1", "stale", exc, gap("source_currency", "sources:" + ",".join(
                sorted(candidate.source_ids)), "employee", "Current versions of every cited original",
                "A new run on the corrected originals"))
    try:
        verify_citations(conn, candidate)
        checks["T2"] = passed("T2")
    except PolicyError as exc:
        checks["T2"] = failed("T2", "invalid", exc)
    if program_id is None or candidate.envelope is None:
        for code in ("T3", "T4", "T5", "T6", "T7", "T8", "T9"):
            checks[code] = skipped(code, "No bound program envelope; no experiment path")
        return result(checks, basis, bound=False)
    store, subject = ProgramStore(company), f"program:{program_id}/envelope:{candidate.envelope}"
    try:
        project, program, spec = store.locked(conn, program_id, active=True)
        envelope = next((e for e in spec.envelopes if e.name == candidate.envelope), None)
        if envelope is None:
            raise PolicyError("Task selects an unapproved envelope")
    except PolicyError as exc:
        checks["T3"] = failed("T3", "needs_program", exc, gap("program_missing", subject, "owner",
            "An active owner-approved program containing this envelope", "Owner approval of such a program"))
        for code in ("T4", "T5", "T6", "T7", "T8", "T9"):
            checks[code] = skipped(code, "Program authority unavailable")
        return result(checks, basis, bound=True)
    checks["T3"] = passed("T3")
    basis["program_manifest_digest"] = program["manifest_digest"]
    template = envelope.template
    error = proposal_mode_error(envelope, candidate.mode)
    if error is None and store.conditional_source(conn, candidate.source_ids) and candidate.mode != "novel_hypothesis":
        error = CONDITIONAL_SOURCE
    checks["T4"] = failed("T4", "out_of_scope", error, gap("data_policy", subject + "/mode:" + candidate.mode, "owner",
        "A program whose signed data policy admits this mode", "Owner approval of a new data policy")) \
        if error else passed("T4")
    metric = template.objective.metric
    checks["T5"] = passed("T5", metric) if candidate.estimand == metric else failed(
        "T5", "needs_evaluator", f"Candidate estimand {candidate.estimand} differs from the envelope objective {metric}",
        gap("evaluator", subject + "/estimand:" + candidate.estimand, "owner",
            "An approved envelope whose evaluator measures this estimand", "Owner approval of that evaluator"))
    if template.kind != "strategy":
        checks["T6"] = skipped("T6", "Non-strategy evaluations have no fill rule")
    elif (card := engine_card(template)) is None:
        checks["T6"] = failed("T6", "needs_evaluator", f"No verified engine rule card for {engine_card_key(template)}",
            gap("execution_timing", subject + "/engine:" + engine_card_key(template), "operator",
                "A versioned engine rule card from the frozen evaluator source", "A new engine rule card version"))
    elif candidate.execution_timing not in card.supported_execution_timings:
        checks["T6"] = failed("T6", "needs_evaluator", f"Engine {card.id} supports "
            f"{', '.join(card.supported_execution_timings)}, not {candidate.execution_timing}",
            gap("execution_timing", subject + "/timing:" + candidate.execution_timing, "owner",
                "An approved evaluator with this execution timing", "Owner approval of that evaluator"))
    else:
        checks["T6"] = passed("T6", f"{card.id}@{card.version}")
        basis["engine_card"] = f"{card.id}@{card.version}"
    try:
        if store.packet_required(envelope, exploratory=template.data.policy is not None):
            packet = store.envelope_packet(program, envelope)
            if packet is None:
                raise PolicyError(MISSING_PACKET)
            basis["packet_digest"] = fingerprint(packet.model_dump(mode="json"))
            require_admissible_gaps(packet, template)
            checks["T7"] = passed("T7")
        else:
            checks["T7"] = skipped("T7", "Fixture profile requires no packet")
    except PolicyError as exc:
        checks["T7"] = failed("T7", "needs_data", exc, gap("data_packet", subject + f"/packet:{basis.get('packet_digest')}",
            "operator", "A verified packet without blocking or unacknowledged gaps", "A new packet digest for this envelope"))
    identity, _ = store.task_identity(program_id, task_proposal(candidate).model_dump(mode="json"))
    authority = template.scientific_lineage
    if conn.execute("SELECT 1 FROM research_program_tasks WHERE id=%s", (identity,)).fetchone():
        checks["T8"] = failed("T8", "duplicate", DUPLICATE_TASK)
    else:
        try:
            require_authority(conn, project["id"], program_id, envelope)
            if authority is not None:
                row = conn.execute("SELECT * FROM research_scientific_lineages WHERE id=%s", (authority.id,)).fetchone()
                if row is None:
                    raise PolicyError("Scientific lineage is not authorized")
                basis["lineage_usage"] = usage(conn, authority.id)
                if exhausted(conn, row, authority):
                    raise PolicyError(LINEAGE_EXHAUSTED)
            checks["T8"] = passed("T8")
        except PolicyError as exc:
            checks["T8"] = failed("T8", "needs_program", exc, gap("lineage_budget",
                subject + f"/lineage:{authority.id if authority else None}", "owner",
                "A signed lineage authority with remaining trials", "Owner approval of a new lineage authority"))
    error = store.capacity_error(conn, program_id, spec)
    checks["T9"] = failed("T9", "needs_program", error, gap("program_budget", f"program:{program_id}/capacity", "owner",
        "Program budget or concurrency capacity", "A finished mission or an approved budget")) if error else passed("T9")
    return result(checks, basis, bound=True)
```

`test_lineage_exhaustion_matches_reservation_rule`가 `usage`를 monkeypatch하므로, `exhausted()`는 모듈 전역 `usage`를 호출해야 한다(Task 6 구현 그대로). triage가 import한 `usage`는 basis 기록용이며 monkeypatch 대상이 아니다. 테스트에서 basis의 `lineage_usage` 값은 확인하지 않는다.

- [ ] **Step 4: 통과 확인**

Run: `uv run --frozen pytest -q tests/test_research_planning_triage.py && uv run --frozen ruff check src/quant_company/research/planning_triage.py tests/test_research_planning_triage.py`
Expected: 모두 통과

- [ ] **Step 5: 커밋**

```bash
git add src/quant_company/research/planning_triage.py tests/test_research_planning_triage.py
git commit -m "Triage planning candidates with shared program rules" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 8: legacy replay와 읽기 전용 명령

**Files:**
- Modify: `src/quant_company/research/planning_triage.py` (replay 추가)
- Create: `src/quant_company/research/planning_commands.py`
- Modify: `src/quant_company/cli.py` (`quant-feed` 파서 다음, dispatch는 `slack-socket` 앞)
- Test: `tests/test_research_planning_triage.py`

**Interfaces:**
- Produces: `ReplayBundle`(schema_version 1: `program_id`, `program_manifest_digest`, `program`, `packets[]`, `tasks[]`, `provenance[]`), `replay(value) -> dict`(순수 함수). `command(settings, action, *, question=None, bundle=None, company=None) -> dict`(company는 테스트 주입용). CLI `quant-company research-planning status|preview|replay-triage [--question ID] [--bundle FILE] [--output FILE]`.

- [ ] **Step 1: 실패하는 테스트 작성** (같은 파일에 추가)

```python
from quant_company.company import fingerprint as digest_of
from quant_company.research.planning_triage import replay


def bundle_for(h, *, blocking=("Unverified source timing",), declared=None):
    spec = h.program_spec.model_dump(mode="json")
    proposal = {key: value for key, value in candidate().items()
                if key in {"title", "envelope", "question", "mode", "original_claim", "method", "departures",
                           "falsification", "source_ids", "citations", "predecessor_mission_ids"}}
    return {"program_id": h.program_id, "program_manifest_digest": digest_of(spec), "program": spec,
            "packets": [{"envelope": "etf", "packet_present": True, "blocking_gaps": list(blocking),
                         "provenance": "fixture packet"}],
            "tasks": [{"id": "00000000-0000-0000-0000-000000000001", "state": "waiting", "proposal": proposal,
                       "data_decision": "blocked", "selection_decision": "wait", "created_at": "2026-09-29T00:00:00Z",
                       **(declared or {})}],
            "provenance": ["synthetic fixture"]}


def test_replay_is_deterministic_and_marks_unknowns(conditional):  # noqa: F811
    first, second = replay(bundle_for(conditional)), replay(bundle_for(conditional))
    assert first == second
    task = first["tasks"][0]
    assert task["replay_outcome"] == "needs_data" and task["stopped_before_data_stage"]
    statuses = {c["code"]: c["status"] for c in task["checks"]}
    assert statuses["T5"] == "not_declared" and statuses["T9"] == "not_declared"
    assert first["summary"] == {"tasks": 1, "stopped_before_data_stage": 1, "actual_data_blocked": 1,
                                "blocked_and_stopped": 1, "by_outcome": {"needs_data": 1}}


def test_replay_uses_only_recorded_declarations(conditional):  # noqa: F811
    declared = {"declared_execution_timing": "same_day_close_fill", "declaration_provenance": "fixture note"}
    task = replay(bundle_for(conditional, blocking=(), declared=declared))["tasks"][0]
    t6 = next(c for c in task["checks"] if c["code"] == "T6")
    assert t6["status"] == "fail" and task["replay_outcome"] == "needs_evaluator"


def test_replay_rejects_a_changed_program(conditional):  # noqa: F811
    value = bundle_for(conditional)
    value["program_manifest_digest"] = "0" * 64
    with pytest.raises(PolicyError, match="manifest digest"):
        replay(value)


def test_commands_are_read_only(tmp_path, conditional):  # noqa: F811
    from quant_company.research.planning_commands import command

    path = tmp_path / "bundle.json"
    path.write_text(json.dumps(bundle_for(conditional)))
    settings, company = conditional.company.settings, conditional.company
    assert command(settings, "replay-triage", bundle=path)["summary"]["tasks"] == 1
    assert command(settings, "status", company=company) == {"projects": []}
    with company.db.transaction() as conn:
        before = conn.execute("SELECT count(*) AS n FROM research_literature").fetchone()["n"]
    with pytest.raises(PolicyError, match="Unknown research question"):
        command(settings, "preview", question="00000000-0000-0000-0000-000000000000", company=company)
    with company.db.transaction() as conn:
        assert conn.execute("SELECT count(*) AS n FROM research_literature").fetchone()["n"] == before
```

- [ ] **Step 2: 실패 확인**

Run: `uv run --frozen pytest -q tests/test_research_planning_triage.py -k replay`
Expected: FAIL — `ImportError: cannot import name 'replay'`

- [ ] **Step 3: 구현**

`planning_triage.py`에 추가:

```python
from collections import Counter
from typing import Literal
from uuid import UUID

from .mission_contracts import MissionModel
from .planning_contracts import Estimand, ExecutionTiming
from .program_contracts import ResearchProgram


class ReplayPacket(MissionModel):
    envelope: str
    packet_present: bool | None = None
    blocking_gaps: list[str] | None = None
    provenance: str


class ReplayTask(MissionModel):
    id: UUID
    state: str
    proposal: dict
    data_decision: str | None = None
    selection_decision: str | None = None
    created_at: str
    declared_estimand: Estimand | None = None
    declared_execution_timing: ExecutionTiming | None = None
    declaration_provenance: str = ""


class ReplayBundle(MissionModel):
    schema_version: Literal[1] = 1
    program_id: UUID
    program_manifest_digest: str
    program: dict
    packets: list[ReplayPacket]
    tasks: list[ReplayTask]
    provenance: list[str]


def replay(value):
    """Re-judge recorded legacy tasks from recorded facts only. Unrecorded facts stay not_declared."""
    bundle = ReplayBundle.model_validate(value)
    spec = ResearchProgram.model_validate(bundle.program)
    if fingerprint(spec.model_dump(mode="json")) != bundle.program_manifest_digest:
        raise PolicyError("Replay program differs from its manifest digest")
    envelopes, packets = {e.name: e for e in spec.envelopes}, {p.envelope: p for p in bundle.packets}
    rows = []
    for task in sorted(bundle.tasks, key=lambda item: (item.created_at, str(item.id))):
        proposal, checks = task.proposal, {}
        checks["T1"] = passed("T1", "Source currency was enforced when the task was registered")
        checks["T2"] = passed("T2", "Citations were verified when the task was registered")
        envelope = envelopes.get(proposal.get("envelope"))
        if envelope is None:
            checks["T3"] = failed("T3", "needs_program", "Task selects an unapproved envelope")
            for code in ("T4", "T5", "T6", "T7", "T8", "T9"):
                checks[code] = skipped(code, "Program authority unavailable")
        else:
            template = envelope.template
            checks["T3"] = passed("T3", "Program was active when the task was registered")
            error = proposal_mode_error(envelope, proposal["mode"])
            checks["T4"] = failed("T4", "out_of_scope", error) if error else passed("T4")
            if task.declared_estimand is None:
                checks["T5"] = skipped("T5", "Legacy proposals do not declare an estimand", "not_declared")
            elif task.declared_estimand == template.objective.metric:
                checks["T5"] = passed("T5", task.declaration_provenance)
            else:
                checks["T5"] = failed("T5", "needs_evaluator", f"Declared estimand {task.declared_estimand} differs "
                                      f"from {template.objective.metric} ({task.declaration_provenance})")
            card = engine_card(template)
            if template.kind != "strategy":
                checks["T6"] = skipped("T6", "Non-strategy evaluations have no fill rule")
            elif task.declared_execution_timing is None:
                checks["T6"] = skipped("T6", "Legacy proposals do not declare execution timing", "not_declared")
            elif card is None or task.declared_execution_timing not in card.supported_execution_timings:
                checks["T6"] = failed("T6", "needs_evaluator", f"Declared {task.declared_execution_timing} is not "
                                      f"supported by {engine_card_key(template)} ({task.declaration_provenance})")
            else:
                checks["T6"] = passed("T6", task.declaration_provenance)
            fact = packets.get(envelope.name)
            if fact is None or fact.packet_present is None:
                checks["T7"] = skipped("T7", "Packet state at proposal time is not recorded", "not_declared")
            elif not fact.packet_present:
                checks["T7"] = failed("T7", "needs_data", f"{MISSING_PACKET} ({fact.provenance})")
            elif fact.blocking_gaps:
                checks["T7"] = failed("T7", "needs_data", "Data evidence packet still records blocking gaps: "
                                      + "; ".join(fact.blocking_gaps) + f" ({fact.provenance})")
            else:
                checks["T7"] = passed("T7", fact.provenance)
            checks["T8"] = skipped("T8", "Registration rejected duplicates; lineage usage then is not reconstructed",
                                   "not_declared")
            checks["T9"] = skipped("T9", "Program capacity at proposal time is not reconstructed", "not_declared")
        judged = result(checks, {"replay": 1}, bound=True)
        rows.append({"task_id": str(task.id), "title": str(proposal.get("title", ""))[:200], "actual_state": task.state,
                     "data_decision": task.data_decision, "selection_decision": task.selection_decision,
                     "replay_outcome": judged.outcome, "stopped_before_data_stage": judged.outcome != "eligible",
                     "checks": [check.model_dump(mode="json") for check in judged.checks]})
    stopped = [row for row in rows if row["stopped_before_data_stage"]]
    return {"schema_version": 1, "program_id": str(bundle.program_id), "manifest_digest": bundle.program_manifest_digest,
            "summary": {"tasks": len(rows), "stopped_before_data_stage": len(stopped),
                        "actual_data_blocked": sum(row["data_decision"] == "blocked" for row in rows),
                        "blocked_and_stopped": sum(row["data_decision"] == "blocked" for row in stopped),
                        "by_outcome": dict(sorted(Counter(row["replay_outcome"] for row in rows).items()))},
            "tasks": rows, "provenance": bundle.provenance,
            "scope": "Deterministic replay of recorded facts. Undeclared or unrecorded facts are not inferred."}
```

`eligible`이 아닌 결과는 모두 "데이터 단계 전에 멈췄을 것"으로 센다. 다만 `not_declared`만 있는 과제는 `eligible`로 남으므로 멈춘 수에 들어가지 않는다. 이것이 의도다(모르는 사실로 멈췄다고 주장하지 않는다).

`src/quant_company/research/planning_commands.py`:

```python
"""Read-only planning commands. Never calls models; database work is rolled back."""

import json
from pathlib import Path

from ..company import Company, as_json
from .planning_retrieval import compute
from .planning_store import QuestionStore
from .planning_triage import replay


def command(settings, action, *, question=None, bundle=None, company=None):
    if action == "replay-triage":
        return replay(json.loads(Path(bundle).read_text()))
    company = company or Company(settings)
    with company.db.transaction() as conn:
        try:
            if action == "status":
                projects = conn.execute("SELECT DISTINCT project_id FROM research_questions ORDER BY project_id").fetchall()
                return as_json({"projects": [QuestionStore(company).status(conn, company._project(
                    conn, row["project_id"], lock=False)) for row in projects]})
            if action == "preview":
                return compute(company, conn, question)
            raise ValueError("Unknown research planning action")
        finally:
            conn.rollback()
```

`cli.py` 파서(`quant_feed` 파서 다음):

```python
    planning = sub.add_parser("research-planning")
    planning.add_argument("action", choices=["status", "preview", "replay-triage"])
    planning.add_argument("--question")
    planning.add_argument("--bundle", type=Path)
    planning.add_argument("--output", type=Path)
```

dispatch(`elif args.command == "slack-socket":` 앞):

```python
    elif args.command == "research-planning":
        from .research.planning_commands import command

        if args.action == "preview" and not args.question:
            parser.error("preview requires --question")
        if args.action == "replay-triage" and not args.bundle:
            parser.error("replay-triage requires --bundle")
        result = command(settings, args.action, question=args.question, bundle=args.bundle)
        rendered = json.dumps(result, default=str, ensure_ascii=False, indent=2) + "\n"
        if args.output:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(rendered)
        else:
            print(rendered)
```

- [ ] **Step 4: 통과 확인**

Run: `uv run --frozen pytest -q tests/test_research_planning_triage.py && uv run --frozen ruff check .`
Expected: 모두 통과

- [ ] **Step 5: 커밋**

```bash
git add src/quant_company/research/planning_triage.py src/quant_company/research/planning_commands.py src/quant_company/cli.py tests/test_research_planning_triage.py
git commit -m "Replay legacy program tasks through triage and add planning commands" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 9: P1 인수 — 커밋된 증거로 replay, 보고, 전체 검증

**Files:**
- Create: `docs/project/evidence/research-planning-20261013/assemble_replay_bundles.py`
- Create: `docs/project/evidence/research-planning-20261013/replay-bundle-e06537d3.json`, `replay-report-e06537d3.json` (스크립트·명령 산출물)
- Create: `docs/project/RESEARCH-PLANNING-P1-20261013.md`
- Modify: `docs/project/RESEARCH-PLANNING-DESIGN-20261013.md` (5장 P1 행 상태)

**입력 증거(커밋된 파일만, 운영 DB 조회 없음)**
- 프로그램 `e06537d3`의 명세와 과제 12건: `evidence/conditional-program-20261006/server-baseline-before.json`의 `database.programs[0]`, `database.tasks`.
- 같은 프로그램 digest(`53392822414c…`)의 데이터 판정 노트: `evidence/research-programs-20260929/etf-data-evidence-note.json`(`decision`, `unresolved` 5건).
- 프로그램 `f7deaf96`의 명세: `evidence/etf-exploration-20260930/approved-canonical-program-20261006.json`. 과제 기록은 `conditional-program-20261006/staff-review-state.json`의 `actual_tasks`(1건).

- [ ] **Step 1: 조립 스크립트 작성**

```python
"""Assemble replay bundles from committed evidence only. No database, network or model access.

Facts that the evidence does not record stay unset, so the replay reports them as not_declared.
"""

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = Path(__file__).resolve().parent


def load(name):
    return json.loads((ROOT / name).read_text())


def bundle_e06537d3():
    base = load("conditional-program-20261006/server-baseline-before.json")["database"]
    program = base["programs"][0]
    assert program["id"].startswith("e06537d3"), program["id"]
    note = load("research-programs-20260929/etf-data-evidence-note.json")
    assert note["program_digest"] == program["manifest_digest"], "evidence note belongs to another program"
    tasks = [{"id": t["id"], "state": t["state"], "proposal": t["proposal"],
              "data_decision": (t.get("data_assessment") or {}).get("decision"),
              "selection_decision": (t.get("decision") or {}).get("decision"), "created_at": t["created_at"]}
             for t in base["tasks"]]
    packets = [{"envelope": "etf_strategy", "packet_present": True, "blocking_gaps": note["unresolved"],
                "provenance": "research-programs-20260929/etf-data-evidence-note.json unresolved "
                              f"(decision {note['decision']})"}]
    return {"program_id": program["id"], "program_manifest_digest": program["manifest_digest"],
            "program": program["spec"], "packets": packets, "tasks": tasks,
            "provenance": ["conditional-program-20261006/server-baseline-before.json",
                           "research-programs-20260929/etf-data-evidence-note.json"]}


if __name__ == "__main__":
    value = bundle_e06537d3()
    (OUT / "replay-bundle-e06537d3.json").write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n")
    print(len(value["tasks"]), "tasks", file=sys.stderr)
```

- [ ] **Step 2: 실행과 사실 확인**

Run:
```bash
python3 -I docs/project/evidence/research-planning-20261013/assemble_replay_bundles.py
uv run --frozen quant-company research-planning replay-triage \
  --bundle docs/project/evidence/research-planning-20261013/replay-bundle-e06537d3.json \
  --output docs/project/evidence/research-planning-20261013/replay-report-e06537d3.json
```
Expected: `12 tasks`. 보고서의 `summary.tasks == 12`.
확인할 것:
- `unresolved` 5건이 패킷의 `blocking_gaps`와 같은 것인지 증거 문서(`FIRST-TASK-DATA-GAP-20260929.md`, `packet-version-postcutover-no-loop.json`)로 대조한다. 같다고 확인되지 않으면 provenance 문구를 "운영자 데이터 노트의 미해결 항목(패킷 blocking_gaps와 동일성 미확인)"으로 바꾸고, 보고서에도 그대로 적는다.
- 종가 체결 과제: 증거가 과제 ID를 명시할 때만 그 과제에 `declared_execution_timing`을 넣는다. 제안문 12건이 모두 "종가"를 언급하므로 문구 검색으로 특정하지 않는다. 특정할 수 없으면 선언 없이 두고, 보고서에 노트의 문장만 인용한다.
- `f7deaf96`는 과제 기록이 1건뿐이고 패킷 사실이 증거에 정리돼 있지 않다. 이 프로그램은 별도 bundle로 만들지 않고 보고서에 "replay 대상 아님, 사유"를 적는다. 총 대상은 12건으로 설계의 "12건 이상"을 채운다.

- [ ] **Step 3: 전체 검증**

Run:
```bash
uv run --frozen ruff check .
uv run --frozen pytest -q -m 'not live' tests deploy/test_deployment_contract.py evals/test_financial_fixtures.py
```
Expected: ruff 통과. 전체 테스트 실패 0(로컬 PostgreSQL 14, CI는 16). 실패가 있으면 원인을 고치고 다시 돌린다. 일시적 인프라 오류(Temporal 개발 서버 시작 지연 등)는 단독 재실행 결과와 함께 기록한다.

- [ ] **Step 4: P1 요약 문서와 설계 상태 갱신**

`docs/project/RESEARCH-PLANNING-P1-20261013.md` 내용:
- 구현 범위(질문·승인, evidence set, triage, replay, 명령)와 flag 기본값 off.
- 테스트 결과(파일별 수, 전체 수, PostgreSQL 버전, 모델 호출 0회).
- replay 결과 표: 과제 12건의 실제 상태·데이터 판정·replay 결과·실패 check. 요약 수치.
- 정직한 한계: T5·T6·T8·T9는 기록이 없어 `not_declared`. replay는 "멈췄을 것"의 하한이다.
- 남은 일: P2(직원 단계·예산 hook·장애 수집), 운영 corpus 측정(소유자 확인 후), 검색 평가(소유자 정답 집합).

설계 문서 5장 P1 행 마지막 열에 `완료(10-13, 요약: RESEARCH-PLANNING-P1-20261013.md)`를 붙인다.

- [ ] **Step 5: 커밋**

```bash
git add docs/project/evidence/research-planning-20261013 docs/project/RESEARCH-PLANNING-P1-20261013.md docs/project/RESEARCH-PLANNING-DESIGN-20261013.md
git commit -m "Record P1 legacy triage replay and acceptance" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

## Self-Review

**Spec coverage (P1 범위)**
- 스키마(6장 중 P1 테이블): Task 2. run·후보·검토·결정·handoff·protocol·usage·단계 열은 P2·P3로 미룬다고 파일 구조 절에 적었다.
- 질문과 승인 어댑터(15장): Task 3. pause·close는 run이 생기는 P2에서 의미가 있으므로 P2로 미룬다. P1의 close는 proposed 질문의 취소뿐이다.
- 검색·evidence set·카드(8.1~8.5): Task 4·5. 8.6 목적 검색은 P4.
- triage와 공통 helper(10장): Task 6·7. 엔진 규칙 카드는 근거가 확인된 `kr-research-python-v2`만 둔다. v1 프로필은 카드가 없어 T6이 `needs_evaluator`가 된다(근거 없는 카드를 만들지 않음).
- status·CLI(15장): Task 8. `evaluate-retrieval`은 소유자 정답 집합이 생긴 뒤에 만든다.
- PT01~PT10: PT01·PT02 Task 4, PT03~PT05 Task 5, PT06 Task 3, PT07·PT08 Task 7, PT09 Task 8, PT10 Task 2와 Task 9 전체 테스트.
- legacy triage replay 보고서(16.2): Task 9.

**Placeholder scan:** 각 코드 단계에 실제 코드가 있다. Task 9의 증거 대조(Step 2)는 파일을 읽고 판단하는 단계라 판단 기준과 결과별 처리를 적었다.

**Type consistency:** `triage(company, conn, value, *, admission, program_id=None, evidence_set_digest=None)`, `compute(company, conn, question_id)`, `freeze(company, conn, question_id)`, `open_gap(conn, project_id, request, *, dependency_fingerprint, question_id=None, evidence_set_id=None)`, `QuestionStore.draft(conn, project, payload, *, actor, origin="owner_request", program_id=None, parent_question_id=None)`를 모든 task에서 같은 이름으로 쓴다.

**Review Focus:** 다섯 항목 모두 담당 task에 테스트가 있다.
