# 조건부 연구 경로 구현 명세 — 미구현 초안

이 명세는 계약의 구현 요구사항이다. 현재 서비스·DB·워커에 적용된 변경은 없다.
기존 `ResearchProgram` v1, `MissionSpec` v1/v2, `DataAssessment`와 프로필 v2의
해시·판정·기존 서명은 보존한다. 새 계약은 아래 버전과 거절 경로를 구현한 뒤
실제 자격검증과 새 서명을 받아야 한다. 구조 검토용 `program-schema-preview.json`은
실행용 제출 문서가 아니다.

## 1. 계약 필드와 일치 규칙

| 제안 계약 | 새 필드 | 필수 검증 |
| --- | --- | --- |
| `ResearchProgram` v2 | 각 envelope의 `data_policy`, `data_policy_digest` | 정책 전체를 서명 대상 program에 포함. digest는 service record encoding으로 재계산. 텍스트 SHA 참조만으로 실행 불가 |
| `ResearchDataPolicy` v1 | `mode`, `input_files`, `evidence_refs`, `availability_status`, `assumed_available_at_rule`, `result_scope`, `acknowledged_gap_codes` | typed enum·엄격한 필드 검증. 모드는 `historical_point_in_time` 또는 `frozen_vintage_retrospective`. 입력과 영수증은 실제 파일 SHA 검증 |
| `MissionSpec` v3 | 같은 정책·digest, `result_scope`, `scientific_lineage_id`, `originating_task_refs` | 승인된 envelope와 정책·입력·가격·비용·기간·프로필이 정확히 일치. 기존 버전으로 정책을 누락해 제출하면 거절 |
| `DataAssessment` v2 | `data_policy_digest`, `packet_digest`, `decision`, 기존 사실 플래그, `evaluation_price_contract_verified`, `acknowledged_gap_codes` | 사실과 실행 범위를 별도로 기록. `decision`은 `ready`, `conditional_ready`, `blocked`; 기존 `ready`를 재해석하지 않음 |
| `AdaptiveExecutionProfile`의 새 버전 | `data_policy_digest`, `result_scope` | 새 ID 사용. 서버·워커·manifest가 같은 정책을 고정. 기존 runtime mount/Python/엔진 해시는 검증 전까지 기존 값에 대한 제안일 뿐 |
| TrialPlan·결과·감사·보고 계약의 새 버전 | `data_policy_digest`, `result_scope`, `scientific_lineage_id` | producer→consumer→DB→감사→HTML 보고 전 구간에서 값 보존. 누락·불일치 거절 |

`availability_status`는 `verified_historical` 또는 `unverified_historical`이다.
이번 안은 `unverified_historical`, `result_scope=conditional_retrospective_development`,
`assumed_available_at_rule=trade_day_T23:59:00+09:00`으로 고정한다. 실제 source
publication time과 revision history는 `null`로 유지한다. 관측·수집·공개 시각을
같은 필드에 넣지 않는다. 수집 이후 관측된 수정본만으로 과거 최초 버전을 만들 수 없다.

## 2. 데이터 판정과 과제 수락

`programs.py::assess/decide`, `data_evidence.py::load_packets`, `builds.py::profile_for`,
`missions.py`의 계획·접수 경로가 함께 정책을 검사해야 한다.

- 기존 `ready`: 현행 PIT·coverage·executable_prices 조건과 실제 패킷의 blocking gap 거절을 유지한다.
- 새 `conditional_ready`: 승인 mode가 `frozen_vintage_retrospective`이고 과제는
  `novel_hypothesis`일 때만 가능하다. coverage 및 evaluation_price_contract_verified가
  참이고 숫자·키·원시 계보·코드 인과성·가격 estimand의 차단 공백이 없어야 한다.
- 이번 입력의 `point_in_time=false`와 과거 원본 미복구 사실은 조건부 수락 뒤에도 유지한다.
  `executable_prices`가 거짓이면 실제 체결 가능성을 참으로 올리지 않는다. 별도
  evaluation_price_contract_verified는 명시된 시가 간 이론적 평가에 대한 독립 판단이다.
- `original_conditions=true`는 exact replication에 여전히 필요하다. 조건부 경로는
  exact replication, market transfer 또는 live/paper 배치 권한을 부여하지 않는다.
- 조건부 director 수락은 기존 `data_assessment.decision == ready` 비교에 암묵적으로
  섞지 않는다. policy digest·scope와 실제 별도 data 직원 판정이 일치하는 명시적 분기를 둔다.

### 공백 코드

새 evidence packet은 보고서 원문·해시와 typed 공백을 함께 가진다. 기존 패킷의 공백을
삭제하거나 비어 있는 새 문자열 목록으로 치환해 통과시키지 않는다.

조건부 연구의 **주장 한계로만** 인정할 수 있는 코드는 다음 세 개뿐이다.

- `historical_publication_unverified`
- `historical_revision_vintage_unverified`
- `original_preparation_bytes_unavailable` — 이전 절대 조정 기준 미복구도 이 사실에 속함

각 코드는 원문 evidence reference, 적용 input SHA와 고정 정책의 acknowledged 목록을
필수로 가진다. `coverage_gap`, `input_hash_mismatch`, `source_lineage_unverified`,
`causal_code_violation`, `price_estimand_unverified`, `nontradable_or_missing_held_price` 등
그 밖의 공백은 차단한다. unknown code도 차단한다. 데이터 직원이 임의의 새로운
gap을 주장 한계로 바꿀 권한은 없다. legacy의 종가 체결 문제는 별도 과제의 판정이며
새 시가 계약으로 자동 해결되었다고 표시하지 않는다. 각 gap 적용성은 과제·엔진·입력에
결부된 새로운 실제 판정으로 기록한다.

## 3. 기존 후보와 예산 이력

새 program은 기존 서명을 대체하지 않는다. 현행 `predecessor_mission_ids`는 같은
program의 미션만 허용하므로 다른 program의 UUID를 이 목록에 넣어 우회할 수 없다.
현재 기존 program에는 미션·과학 시행이 0건이지만 대기 과제 이력은 존재한다.

- 새 `originating_task_refs`는 `{program_id, task_id, task_digest}` 목록이다. 같은 회사
  project의 실제 task·digest인지 service가 검증한다. 자료 재수집 자체는 새 가설이 아니다.
- 질문·방법·후보가 같은지 researcher와 독립 director가 판단하고 기존 scientific
  lineage에 연결한다. 새 가설이라고 주장하면 변경된 estimand와 반증 조건을 기록한다.
- 추가 `research_scientific_lineages` 및 task/trial 연결 레코드는 PostgreSQL이 소유한다.
  한 lineage의 실제 과학 trial 이력은 새 program을 만들거나 입력 해시를 바꾸어도 남는다.
- trial 예약은 lineage와 program을 같은 트랜잭션에서 잠그고 양쪽 잔여 한도를 검사한다.
  새 program의 4회는 lineage 한도의 새 reset이 아니다. 이전 결과·시행을 감사와 보고에 포함한다.
- 기술적 실패는 과학 성공 시행으로 세지 않는다. 계산 시간·실패·재시도 영수증은 남기고,
  현행 timeout/예약/복구 규칙을 유지한다. crash 후 미확인 실행을 새 시행으로 blind replay하지 않는다.
- 이번 제안은 1미션·최대 4과학 시행·7,200초·동시 1개·continuous=false이다.
  이 한도는 4회를 반드시 실행한다는 약속이 아니다. worker 자격검증·실패가 시간 한도를 쓸 수 있다.

## 4. 실행과 결과 전파

`domestic_profile.py`는 새 profile ID와 별도 정책·수치 품질 영수증을 지원해야 한다.
이번 staged `receipt.json`의 `review_draft_not_ready`는 현행 factory에서 거절되어야 한다.
새 수치 품질 `ready`만으로 conditional data assessment나 실행 서명을 대체하지 않는다.
runtime/profile qualified 상태는 실제 격리 worker producer→consumer 검사 후에만 기록한다.

`adaptive_contracts.py`, `adaptive_executor.py`, `mission_backend.py`, `missions.py`,
`audit.py`, `programs.py`의 의미 판정, `adaptive_report.py`에서 정책과 scope를 검증한다.
보고는 원래 데이터와 현재 조건부 자료의 결과를 같은 역사적 성과로 합치지 않는다.
meaning review의 supported는 명시된 조건부 대상에만 붙으며, 과거 실제 alpha·정확 재현·
현금 분배금 총수익·실제 체결·배치 준비·미노출 2026 확인으로 승격할 수 없다.
봉인 기간의 원시 가격은 이 작업과 개발·자격검증 입력에 포함하지 않는다.

## 5. 실제 검증 완료 조건

구현 시 필요한 검사는 현재 초안의 로컬 26개 검사와 별개다.

1. 기존 계약 bytes/digest·서명·PIT 판정의 회귀 검사.
2. 새 서명 누락, 정책·입력·프로필·scope 불일치, 미분류 gap, exact/live 요청 거절.
3. 실제 PostgreSQL/Temporal에서 conditional assessment→director selection→예약→접수→감사→보고,
   timeout·crash·recovery에도 stable turn/outcome 및 조건부 scope가 보존되는지 확인.
4. 격리 worker E2E: 입력 및 엔진 해시, qualification의 warmup 전용 mount, 시점 prefix,
   미래 상수 재조정 불변성, 거래대금·메타 조건, 비용·말일 평가 규칙을 실제 producer/consumer로 검증.
5. 신규 program 간 동일 후보의 과학 이력·예산·보고 연결과 중복 예약 거절.
6. 실제 독립 data/director 직원의 새 판정 및 구현된 canonical program에 대한 signed Slack 승인.

이 검증과 새 서명 전에는 실행 권한을 생성하지 않는다. 미래 데이터 영수증 수집도
별도 구체적 수집 계약이 필요하며, 현재 문서는 수집·배포를 예약하지 않는다.
