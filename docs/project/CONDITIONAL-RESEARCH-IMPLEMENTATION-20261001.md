# 조건부 연구 경로 구현·검증 — 2026-10-01

검토된 ETF 실행 명세를 서비스 코드에 구현하고 통합 검증을 수행했다. 최종 독립 검토와 CI를 진행 중이다.
이번 구현 작업에서는 운영 변경·실제 직원의 v3 데이터 판정·v3 프로그램의 Slack 승인·시장 실험을 하지 않았다.

## 구현 범위

| 경계 | 구현 |
| --- | --- |
| 서명 계약 | ResearchProgram v2의 envelope.template에 MissionSpec v3와 정책·누적 계보 권한 전체를 포함 |
| 데이터 | DataAssessment v2 `conditional_ready`, 실제 packet digest·파일 SHA·typed gap의 대조 |
| 권한 | signed owner program → 별도 data 판정 → director 수락 → 고정 child mission. 직접 v3 mission 승인 거절 |
| 예산 | PostgreSQL의 project/program/lineage 잠금, 누적 result 및 미확인 예약 차감, 정산된 기술 실패 보존 |
| 계보 | 같은 프로젝트의 실제 task/digest 연결, 기존 task 누락·lineage 재연결·동일 구성 재등록 거절 |
| 워커 | protected producer JSON v1의 원본 바이트 보존, trusted wrapper의 record v2 scope·producer SHA |
| 소비자 | original/wrapper 내용·SHA, profile/manifest/record scope, qualification warmup·sealed 제외 재검증 |
| 후속 판단 | outcome·interpretation·incumbent·audit·meaning·report·source/library의 같은 조건부 범위 유지 |

canonical `research_scope`는 서명된 MissionSpec의 `data_policy`와 `scientific_lineage`에서 계산한다.
data policy digest·mode·result scope·lineage ID·acknowledged gap 코드를 같은 typed object로 전달한다.
필드를 여러 번 선언해 불일치하는 계약을 만들지 않는다. 직원 context도 서비스가 계산한 값을 제공한다.
full policy·권한은 서명 대상에 실제로 포함되며, 텍스트 목적란의 SHA만으로 승인하지 않는다.

`conditional_ready`는 PIT·original_conditions·executable_prices를 거짓으로 유지하고, coverage와
이론적 평가 가격 계약 검증을 참으로 요구한다. 과제는 `novel_hypothesis`만 허용한다.
원래 공개·수정 빈티지와 준비 바이트의 세 한계 코드 외 공백 및 legacy blocking gap은 차단한다.
조건부 자료로 original replication/market transfer 조건을 증명하거나 replication evaluator를
선택하는 것도 거절한다. supported나 incumbent가 되어도 역사적 알파·실제 체결·배치·총수익·
미노출 2026 확인으로 승격되지 않는다.

기존 프로그램 digest `53392822414ca32e89fab0f3a9a1093a350196345bd13084654a45510297159b`의
JSON과 legacy assessment encoding은 회귀 검사에서 일치했다. protected engine SHA
`1984bf6b7fa5997a8b7ba446061ff663bdf78c683776be945da9130a067480cc`는 그대로다.
과거 조사·초안·기존 입력·서명 기록은 보존한다. 이전 회사 STATUS도 `STATUS-v6.json`에
바이트 그대로 남기고, 새 구현 작업은 INTENT v7에서 추적한다.

## 병행 변경과 정책 호환

원격 `76c8abc`에 별도로 승인·자격검증된 기존 버전의 ETF 탐색 기능이 추가되어,
새 구현을 먼저 `aa7c146`에 보존한 뒤 `4f3bdb7`에서 통합했다. 기존 승인·운영 근거를 보존했다.
그 탐색 기능의 승인이나 운영 상태를 새 v3 경로의 승인으로 사용하지 않는다.

| 계약 | 데이터 수락 | 결과 제한 |
| --- | --- | --- |
| MissionSpec v1/v2 `data.policy` | `exploratory_only`, 검증한 정책 digest | 가설 생성 전용, `supported` 거절 |
| MissionSpec v3 `data_policy`·`scientific_lineage` | record v2 `conditional_ready`, packet digest·scope | 누적 계보의 조건부 개발구간 근거; 역사적/PIT/체결/배치 승격 금지 |

한 v3 미션·packet·assessment에 두 계약을 섞으면 거절한다. 기존 탐색 프로그램의 JSON과
digest `04cee0f99abab3dfb94b37756a195753960fffd5a3f623ce0dd3563bf776d162`도 보존했다.

## 독립 검토와 수정

기존 독립 데이터·권한 검토자에게 구현 검토를 이어 맡겼다. 이들은 운영 직원이나 실제
시장 결과의 판정자가 아니다. 처음 본 파일 해시와 재검토 해시를 별도로 기록한다.

데이터 검토자는 qualification 원본·wrapper·receipt SHA를 모두 새로 계산한 뒤 날짜만 합성
sealed 기간으로 바꿨을 때, worker는 거절하지만 consumer가 수락하는 경계 결함을 재현했다.
consumer에 sample 수·typed schema·warmup 날짜 및 sealed 제외 검사를 추가했다.
현재 동일 변조는 양쪽에서 거절한다. 원래 합성 fixture의 qualification 날짜도 warmup으로
바로잡았다. 실제 봉인 가격을 읽거나 시장 누출을 확인했다는 뜻은 아니다.

정산된 terminal 기술 실패는, 미션이 종료/취소되고 job terminal 상태·typed failure·정산
영수증이 확인될 때 후속 프로그램에 승계할 수 있다. 미확인 예약이나 실행 중 job이 남으면
계속 차단한다. 실패의 구성·이력·계산 charge는 삭제하지 않는다.

통합 데이터 검토에서는 기존 탐색 보고서의 `metadata.data_policy`를 strict 프로그램에서
원조건 재현/시장 이전 근거로 재사용할 수 있는 결함을 지적했다. 실제 PostgreSQL의 합성
거절 검사 두 개가 실패하고, 신규 scope의 거절 및 양쪽 가설 생성 대조 네 개는 통과했다.
두 metadata 형식을 같은 재사용 제한에 포함하도록 수정했다. 재현 전후 입력과 근거는
별도 파일에 보존하며, 수정 후 관련 검사 108개가 모두 통과했다.

## 검증과 활성화 경계

- 실제 PostgreSQL에서 계약·수락·예약·정산·새 프로그램의 부정 결과 상속을 검사한다.
- 실제 Temporal에서 합성 직원의 원문/패킷 읽기→조건부 판정→director 수락을 실행하고,
  worker 재시작과 workflow history replay를 검사한다. 모델·Slack ingress는 fixture다.
- 실제 3070의 bubblewrap producer→trusted wrapper→strict archive consumer 검증은
  고정된 코드 커밋과 생성한 소형 합성 입력으로 수행한다. 시장 자료·봉인 가격은 입력에 없다.
- qlab 파일 검증 API와 synthetic audit receipt 변환 검사는 실제 로컬 pinned 코드를 사용한다.
  fixture pass 파일은 금융 연구의 독립 판단을 뜻하지 않는다.

`4f3bdb7`의 전체 non-live 회귀는 1,512개 통과·선택적 검사 13개 생략이었다.
actual 3070 격리 sandbox 검사는 1개 통과했고, 수집한 원본 ZIP의 SHA를 대조한 뒤
로컬 strict consumer도 통과했다. 최종 수정 커밋의 실제 호스트 검사와 CI도 별도로 기록한다.
dirty 코드 상태의 초기 회귀에서 committed-source 자격검증이 거절한 결과도 보존했다.
이를 통과로 바꾸거나 자격검증 조건을 완화하지 않았다.

검토 입력과 각 판정·검사 영수증은
[evidence 디렉터리](evidence/conditional-research-20261001)에 보존한다.
아직 실행하지 않은 검사는 완료로 표시하지 않는다.

다음 운영 단계는 릴리스 인수, 실제 입력/별도 숫자 품질 및 새 정책 패킷 준비,
실제 data/director 직원의 새 판정, 최종 canonical 프로그램의 인증된 Slack 승인이다.
이번 구현 승인은 연구 실행 권한을 생성하지 않는다. 이전 `review_draft_not_ready` 영수증도
변경하거나 readiness로 승격하지 않았다. 운영에서는 승인 전 첫 시장 실험을 실행할 수 없다.
