# 연구 계획 shadow run 절차서

- 작성: 2026-10-14
- 상태: P2b 코드 완료. 실제 모델 shadow run은 아래 2장의 소유자 결정 두 가지가 끝난 뒤에 한다.
- 근거: [설계](RESEARCH-PLANNING-DESIGN-20261013.md) 5·12.7·14·16.2·18장, [P2a 계획](RESEARCH-PLANNING-P2A-PLAN-20261014.md), [P2b 계획](RESEARCH-PLANNING-P2B-PLAN-20261014.md)

이 문서는 운영자가 따라 할 순서만 정한다. 실행 기록과 결과는 7장의 결과 보고서에 쓴다.
shadow run은 후보 비교와 결정 기록까지만 한다. 다음은 하지 않는다.

- 프로그램 과제 등록(handoff)
- 프로그램 단계 실행
- Slack 발신

P2b 코드에는 handoff 경로가 없다.

## 1 전제

- P2b가 main에 병합돼 있다. 배포할 commit은 원격에 푸시된 main의 정확한 SHA로 기록한다.
  푸시되지 않은 파일로 배포하지 않는다.
- **다른 운영 세션을 먼저 확인한다.**
  - 배포 잠금과 apply 스크립트를 확인한다([research-programs 운영 절차](../runbooks/research-programs.md), 설계 18장).
  - 운영 통합 조율(10-14 공지)을 따른다.
    - 운영 배포, 이미지 교체, overlay·apply 스크립트 적용, 컨테이너 재생성, flag 변경 전에 운영 통합 세션(sargassum-cf)에 서비스 이름과 source commit SHA를 알린다.
    - 공지된 cutover 시간대에는 배포와 flag 변경을 하지 않는다.
- `COMPANY_RESEARCH_ENABLED=true`는 research overlay(`deploy/research.compose.yaml`)로 이미 켜져 있다.
- 계획 테이블은 가산적 migration이다. 계획 flag 두 개는 기본값이 off다.
- **금지**
  - 운영 DB를 테스트 대상으로 쓰지 않는다.
  - 운영 프로그램 `f7deaf96`의 명세나 상태를 바꾸지 않는다.
  - 유료 API 자격 증명으로 바꾸지 않는다.

## 2 소유자 결정 (실행 전)

1. **첫 질문**
   - 무엇을 물을지 정한다.
   - 운영 프로그램 `f7deaf96`에 묶을지 정한다. 묶으면 설계 12.7의 shadow 비교가 된다.
2. **실제 모델 사용량 사용 동의**
   - 상한(설계 14장, `PLANNING_LIMITS`)
     - run당 요청 90회, 소유자당 하루 요청 120회
     - 단계 시도당 턴: slate 36, critique 28, decision 12
     - 단계당 시도 3회
   - 참고 추정: 실측 응답 1회의 입력은 약 2.6만 토큰이다(설계 2장). 상한 90회를 모두 쓰면 입력은 캐시 포함 약 230만 토큰이다.
     실제 값은 보고서의 `usage`로 확인한다.
   - 단계 모델은 각 직원의 현재 배정을 따른다([모델 배정](../runbooks/model-assignments.md)). 단계별 담당은 다음과 같다.
     - slate: 연구원
     - critique: 금융전략
     - decision: 총괄

## 3 켜는 순서

1. **배포**
   - 계획 flag를 모두 끈 상태로 P2b가 들어간 main commit을 배포한다.
   - `quant-company migrate`를 실행한다.
   - 기존 동작이 그대로인지 확인한다: 대화, 프로그램 단계, 피드.
2. **`RESEARCH_PLANNING_ENABLED=true`**
   - research overlay에서 `COMPANY_RESEARCH_ENABLED`를 가진 서비스 모두에 같은 값으로 넣는다: api, slack-socket, worker, data-watch-worker, dispatch.
   - 계획 전용 overlay는 아직 없다. 승인된 overlay나 서버 env에 넣고, 바꾼 파일과 값을 배포 기록에 남긴다.
   - 이 단계에서는 질문 초안, 상태 조회, 승인 버튼만 열린다. 모델 단계는 아직 돌지 않는다.
3. **질문 초안**
   - Slack에서 총괄에게 질문을 설명한다.
   - 총괄이 `research_control`의 `planning_question_draft`로 초안을 만든다. 프로그램에 묶을 때는 `program_id`를 넣는다.
   - 총괄이 승인 요청을 올린다.
4. **소유자 승인**
   - 소유자가 승인 버튼을 누르면 질문이 `open`이 된다.
   - 이 승인은 질문 범위의 근거 수집과 후보 비교만 허용한다. 실험 권한과 프로그램 권한은 포함하지 않는다.
   - `quant-company research-planning status`로 질문 `state`가 `open`인지 확인한다.
   - `quant-company research-planning preview --question <질문 ID>`로 evidence set을 미리 본다.
     - 모델 호출은 0회이고, DB 변경은 롤백된다.
     - `state`가 `oversize`이면 run이 시작되지 않는다. 질문 범위를 좁힌다.
5. **`RESEARCH_PLANNING_STAGES_ENABLED=true`**
   - 2번과 같은 서비스에 넣는다.
   - 다음 research tick에서 질문 하나에 shadow run 하나가 시작된다. 소유자당 동시 run은 1개다.

## 4 관찰

- **상태**: `quant-company research-planning status`
  - 질문마다 최근 run 3개의 `id`, `state`, `outcome`, `hold_reason`이 나온다.
- **단계 흐름**
  - slate(연구원) → critique(금융전략) → decision(총괄) → `completed`
  - `held`이면 `hold_reason`과 open gap을 본다. held는 실패 판정이 아니라 운영자 대사가 필요한 보류다.
- **보고서**: `quant-company research-planning run-report --run <run ID>`
  - 컨테이너 안에서 `--output`을 쓰면 파일이 컨테이너에 남는다. 표준 출력을 받아 이 저장소의 `docs/project/evidence/research-planning-YYYYMMDD/run-report.json`에 저장한다.
  - 커밋 전에 비밀값, 접속 정보, 민감한 원문 발췌가 없는지 검토한다.

## 5 확인 항목

| 항목 | 기준 | 보고서 위치 |
| --- | --- | --- |
| run 결과 | `shadow_recorded`. 후보가 없으면 `no_candidate`도 정상 종료다 | `run.outcome` |
| 부수효과 0건 | 네 값이 모두 0: `messages`, `outbox`, `delegated_tasks`, `registered_program_tasks` | `side_effects` |
| 후속 질문 | 모두 `proposed`. 승인 요청 메시지 없음 | `follow_up_questions` |
| 비용 | 요청 수 90회 이하. 입력·캐시·출력 토큰 기록 | `usage` |
| 결정 카드 | experiment 경로는 triage `eligible` 후보에만 있다. 순서를 바꾸면 `override`와 사유가 있다 | `decision`, `candidates` |
| 사람 대조 표본 | 근거 없는 핵심 수치 0건, 실험 완료 주장 0건 | `candidates`, `reviews`, `decision` |

- `side_effects`는 이 run의 단계 task에 귀속된 행만 센다.
  운영 DB 전체의 행 수 비교는 쓰지 않는다. 다른 세션과 운영 프로그램이 같은 테이블에 계속 쓰기 때문이다.
- 사람 대조 표본은 후보, 반론, 결정의 핵심 수치와 인용을 원문과 맞춰 본다.

## 6 끄기와 롤백

- **끄는 시점**: run이 `completed`나 `held`로 끝난 뒤가 가장 안전하다.
  진행 중에 끄면 대기 중인 계획 turn은 준비 단계에서 거부되고 run이 멈춘다. 다시 켜기 전에 운영자가 해당 run의 단계와 turn을 대사한다.
- **새 run과 단계 생성 중지**: `RESEARCH_PLANNING_STAGES_ENABLED=false`.
  이미 보낸 provider 요청은 완료, 취소, uncertain 중 하나로 대사한다. uncertain 요청은 자동으로 다시 보내지 않는다.
- **질문 초안과 승인 버튼까지 닫기**: `RESEARCH_PLANNING_ENABLED=false`도 끈다.
- **기록**: 질문, evidence set, run, 후보, 검토, 결정, gap은 지우지 않는다. 테이블을 drop하지 않는다.
- **이미지 롤백**: flag를 먼저 끈다. 그 뒤 이전 이미지로 앱 서비스만 되돌린다.

## 7 결과 보고

`docs/project/RESEARCH-PLANNING-SHADOW-<날짜>.md`에 쓴다. 근거 파일은 `docs/project/evidence/research-planning-YYYYMMDD/`에 둔다.

- **비용**: 요청 수, 토큰, 단계별 시도와 턴, 14장 상한과의 비교.
- **결정 카드**: 비교한 후보, 서비스 순서, 총괄의 선택과 이유, 남은 gap, 후속 질문 제안.
- **운영 프로그램 비교**(설계 12.7, 프로그램에 묶은 경우)
  - 후보 집합 ↔ 계보 이력의 실제 과제와 10-13에 선정·실행된 후속 가설
  - triage 결과 ↔ 실제 데이터 판단과 총괄 판단
  - 실측 비용 ↔ 14장 상한
  - 이 프로그램은 남은 trial이 적어 전환 실익이 작을 수 있다. 실제 효과는 다음 프로그램에서 기대한다고 적는다.
- **검증 구분**
  - 실제 모델(Codex 런타임)과 실제 운영 DB 읽기로 확인한 항목
  - fixture 테스트로만 확인한 항목

  두 가지를 나눠 적는다. 합성 결과를 실제 인수로 표시하지 않는다.
- **다음 결정**: P3 전환(planning_v1, handoff)을 할지는 이 보고를 본 소유자가 정한다.
