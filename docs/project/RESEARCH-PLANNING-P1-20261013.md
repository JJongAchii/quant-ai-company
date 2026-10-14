# 연구 계획 계층 P1 — 모델 호출 없는 기반 (2026-10-13)

- 상태: 코드와 테스트 완료. 운영 배포는 하지 않았다.
- `RESEARCH_PLANNING_ENABLED`의 기본값은 `false`다. 켜기 전까지 운영 동작은 그대로다.
- 설계: [RESEARCH-PLANNING-DESIGN-20261013.md](RESEARCH-PLANNING-DESIGN-20261013.md)
- 실행 계획: [RESEARCH-PLANNING-P1-PLAN-20261013.md](RESEARCH-PLANNING-P1-PLAN-20261013.md)

## 만든 것

| 위치 | 내용 |
| --- | --- |
| `research/planning_contracts.py` | 질문 명세(QuestionSpec), 계획 후보(PlanningCandidate)와 프로그램 과제로의 그대로 투영, 해결 과제 요청(GapRequest), triage 결과, 엔진 규칙 카드 |
| `research/planning_schema.sql` | `research_questions`, `research_question_revisions`, `research_evidence_sets`, `research_gaps`. 가산적 migration |
| `research/planning_store.py` | 질문 초안·소유자 승인·취소·상태, gap 기록(같은 문제의 열린 gap은 하나), 승인 어댑터 `research_question`, 총괄 도구 `planning_question_draft`·`planning_status` |
| `research/library.py` | `program_corpus`, `project_corpus`. 기존 `available_sources()`와 같은 권한 범위이고 최근 20건 제한만 없다 |
| `research/planning_retrieval.py` | evidence set 계산·고정. 아래 "근거 선택 규칙" 참고 |
| `research/programs.py`, `research/scientific_lineages.py` | triage와 기존 과제 등록·선정이 같은 검사 함수를 쓰도록 꺼냈다. 기존 검사 순서와 오류 문구는 그대로다 |
| `research/planning_triage.py` | 사전 판정 T1~T9, 과거 과제 replay |
| `research/planning_commands.py`, `cli.py` | `quant-company research-planning status·preview·replay-triage`. 읽기 전용이고 DB 작업은 rollback한다 |

**근거 선택 규칙**
- 토큰화: 영문은 단어, 한글은 문자 2-gram. 형태소 분석이 아니다.
- 점수: BM25(k1 1.2, b 0.75). 질문과 겹치는 단어가 있을 때만 시장·주제 가점을 준다.
- 최신성은 점수가 같을 때만 쓴다.
- 반대 근거와 고전 문헌은 있으면 한 자리씩 남겨 둔다.
- 카드는 12개 이하, 한 장에 3,000자 이하다. 카드는 탐색용이고 인용 근거가 아니다.
- 원문은 필수 출처 전부와 문헌 최대 5건, 합계 300,000자 이하다.
- 필수 출처만으로 한도를 넘으면 빼지 않는다. 대신 `oversize`로 표시하고 소유자 몫의 gap을 남긴다.

flag가 꺼져 있으면 다음이 모두 지켜진다. 테스트로 확인했다.
- 총괄 capability에 `research_planning`이 없다.
- `planning_` action은 거부된다.
- 질문 승인 대상이 없다.

## 검증

- **새 테스트 49개**: 계약 7, 질문 13, 근거 선택 11, triage·replay 18. 실제 PostgreSQL(로컬 14)에서 돌렸고 모델 호출은 0회다.
- **리팩터링 회귀**: 연구 프로그램·조건부·데이터 관련 테스트가 리팩터링 전후 모두 92개 통과, 3개 건너뜀으로 같았다.
- **전체 테스트**: CI와 같은 명령(`pytest -m 'not live' tests deploy/test_deployment_contract.py evals/test_financial_fixtures.py`)으로 2,612개 통과, 78개 건너뜀, 실패 0. 로컬 PostgreSQL 14에서 돌렸고, CI는 16을 쓴다.
- **lint**: ruff 통과.

## 과거 과제 replay (프로그램 `e06537d3`, 과제 12건)

입력은 커밋된 증거만 쓴다. 운영 DB는 조회하지 않았다.
- [조립 스크립트](evidence/research-planning-20261013/assemble_replay_bundles.py)
- [입력](evidence/research-planning-20261013/replay-bundle-e06537d3.json)
- [결과](evidence/research-planning-20261013/replay-report-e06537d3.json)

패킷 상태는 과제를 등록한 시점에 운영 중이던 레지스트리를 기준으로 붙였다. 레지스트리를 바꿀 때마다 남은 영수증에 이전·새 해시가 있다. 스크립트는 이 해시가 커밋된 레지스트리 파일과 같은지 확인한다.

| 기간 (UTC) | 운영 레지스트리 | 패킷 상태 |
| --- | --- | --- |
| 09-29 05:27 이전 | 없음 | 검증된 패킷 없음 |
| 09-29 05:27 ~ 09-30 07:26 | `etf-data-evidence-registry.json` (`3ff9e81e`) | blocking gap 5건 |
| 09-30 07:26 ~ 10:54 | `etf-provenance-evidence-registry.json` (`d3822b1d`) | blocking gap 5건 |
| 09-30 10:54 ~ 10-01 07:57 | `raw-lineage-evidence-registry.json` (`bdef7780`) | blocking gap 5건 |
| 10-01 07:57 이후 | `023aa88c` 등, 커밋되지 않음 | 기록 없음 |

| 과제 | 등록 (UTC) | 실제 상태 / 데이터 판정 / 총괄 판단 | replay 결과 | 걸린 검사 |
| --- | --- | --- | --- | --- |
| `dae4bd01` | 09-29 03:45 | waiting / blocked / wait | needs_data | T7 패킷 없음 |
| `dc0e9eac` | 09-29 04:06 | waiting / blocked / wait | needs_evaluator | T6 종가 체결, T7 패킷 없음 |
| `875a7d41` | 09-29 05:43 | waiting / blocked / wait | needs_data | T7 blocking gap |
| `fc6f31a6` | 09-29 07:02 | waiting / blocked / wait | needs_data | T7 blocking gap |
| `3bf21cca` | 09-29 15:14 | waiting / blocked / wait | needs_data | T7 blocking gap |
| `b5ce3cb7` | 09-30 06:44 | waiting / blocked / wait | needs_data | T7 blocking gap |
| `2733156b` | 09-30 07:52 | waiting / blocked / wait | needs_data | T7 blocking gap |
| `3487f863` | 09-30 11:04 | waiting / blocked / wait | needs_data | T7 blocking gap |
| `a226d13d` | 09-30 13:11 | waiting / blocked / wait | needs_data | T7 blocking gap |
| `1a9683ac` | 09-30 15:45 | waiting / blocked / wait | needs_data | T7 blocking gap |
| `8de706c6` | 10-02 10:13 | waiting / blocked / wait | 판정 보류 | 그 시점 패킷 기록 없음 |
| `3d965de7` | 10-03 02:02 | proposed / — / — | 판정 보류 | 그 시점 패킷 기록 없음 |

- 실제로 데이터 판정이 `blocked`였던 11건 가운데 10건은 사전 판정(triage)이 데이터 단계 전에 멈췄을 것이다.
  - 9건은 T7(패킷 없음 또는 blocking gap)에서 걸린다.
  - 1건(`dc0e9eac`)은 T6과 T7에서 걸린다. 08:30 결정 뒤 당일 종가 체결을 요구했지만 엔진은 시가→익일 시가로 평가한다.
- planning_v1 프로그램이었다면 이 10건은 데이터 직원 단계와 총괄 선정 단계로 넘어가지 않는다(설계 D3·D4).
- 결과 JSON에서 "판정 보류" 2건은 `eligible`로 표시된다. 이는 멈출 근거가 기록되지 않았다는 뜻이고, 통과했다는 뜻이 아니다.

**한계**
- T1·T2·T4는 등록 당시 기존 경로가 이미 강제했으므로 replay에서는 통과로 둔다.
- T5·T8·T9는 당시 기록이 없어 판정하지 않았다.
- T6은 증거가 과제 ID를 명시한 1건에만 적용했다.
  - [데이터 공백 기록](FIRST-TASK-DATA-GAP-20260929.md)이 "두 번째 과제"의 종가 체결을 서술하고, [그 과제의 데이터 심사 기록](evidence/research-programs-20260929/second-task-data-assessment.json)이 ID를 남겼다.
  - 12건의 제안문이 모두 "종가"를 언급하므로 문구 검색으로 과제를 고르지 않았다.
- 따라서 "멈췄을 과제 10건"은 하한이다.
- 프로그램 `f7deaf96`은 이번 replay 대상에서 뺐다. 커밋된 과제 기록이 1건뿐이고 패킷 사실이 정리돼 있지 않다.
- 검색 품질 평가는 아직 하지 않았다. 소유자가 질문별 필수 근거·반대 근거 정답을 정해 줘야 한다.
- 운영 corpus 규모 측정(P0 후반)은 운영 DB 조회가 필요해 소유자 확인 후에 한다.

## 다음 단계 (P2)

- run·후보·검토·결정 테이블과 단계 행의 `planning_run_id`.
- 직원 단계 3개(`planning_slate`·`planning_critique`·`planning_decision`, 네이티브 응답). 예산 hook, 장애 수집 연결, 직원 playbook도 함께 한다.
- 질문 중지·닫기.
- 운영 프로그램 질문으로 실제 모델 shadow run 1회. 질문은 소유자가 고른다.
