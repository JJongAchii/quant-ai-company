# 조건부 연구 프로그램의 실제 승인과 직원 검토

## 현재 상태

2026-10-06 운영 인수와 실제 직원 검토 기록. 실제 Slack 승인 이후 연구 담당자가 새 과제를 등록했다. 데이터 담당자의 네 차례 응답 형식 실패를 보존한 뒤, CI·3070 입력 호환성·서버 인수 검증을 마친 응답 프로토콜을 운영에 반영했다. 10:09 KST에 기술적 보류를 해제했다.

10:38:31 KST에 실제 데이터 담당자의 `conditional_ready` 판단이 검증을 통과해 PostgreSQL에 저장됐다. 10:45:42 KST에 연구 책임자의 독립 `revise` 결정이 저장됐고, 연구 담당자가 피드백을 반영하는 후속 제안을 시작했다. 10:48 KST 스냅샷에서 새 과학 시행은0회이며 연구 미션과 성과 수치는 없다. 초기 실제 직원 검토의 인수는 완료됐고, 가설의 수정 작업이 이어지고 있다.

## 실제 소유자 승인

사용자의 `너가 보내고 진행해줘` 위임에 따라 로그인된 소유자의 Slack 화면에서 기존 프로그램 취소를 전송하고 새 프로그램의 정확한 승인 버튼을 눌렀다. 사람이 직접 버튼을 눌렀다고 주장하지 않는다. 서명 검증을 거친 실제 inbound와 PostgreSQL 승인 기록을 확인했다.

- 기존 프로그램: `e06537d3-fac3-5c8c-bf25-ddabb3c7e282`, 실제 취소 기록 유지.
- 새 프로그램: `f7deaf96-e677-5afe-93d4-18ac387043bb`.
- 승인된 digest: `c3ba5268d71c87bd2c6226160bc720498137bd291396a054970a1f1e7ebcf2db`.
- [실제 승인 메시지](https://achiisquantresearch.slack.com/archives/C0C2B9EUEGM/p1791241501260409?thread_ts=1789633942.673909&cid=C0C2B9EUEGM).
- 실제 승인 시각: 2026-10-06 08:13:50 KST.

기존 열 개 과제 이력은 승인 대기 중 실제로 열두 개가 됐다. 새 제안은 실제 열두 개 origin과 실패·차단 이력을 포함한다. 상속 이력 digest는 `0d5b9d64115576998997ade21baf2fe13a73d2d9467f6762d81bc314ca502c58`이다. 과거의 승인이나 준비된 구 digest를 재사용하지 않았다.

한도는 누적 과학 trial 4개, 계산 시간 7,200초, 동시 미션 1개, 회차당 결과 2개로 유지된다. 별도로 게시된 legacy 초안에는 승인하지 않았다.

## 실제 직원의 연구 제안

연구 담당자 `researcher_kr`는 실제 Codex 응답과 원문 읽기 영수증을 통해 다음 과제를 등록했다.

> 분산예측 수준 보정이 ETF의 스트레스 비용 후 절대수익을 개선하는가

과제 ID는 `bc915792-d608-5710-a271-f4aa592e9ee1`이다. 069500 ETF와 현금의 노출 조절에서 분산예측 수준 보정을 비교하는 조건부 개발 가설이다. 과거 데이터만 사용하는 보정과 미보정 기준을 비교하도록 제안했다. 논문과의 차이, 구현 확인이 필요한 지점도 제안에 남아 있다. 구현 가능성이나 수익 개선이 입증된 상태는 아니다.

데이터 담당자는 별도의 `program_data` 단계에서 검토한다. 네 요청은 모두 `invalid_output` / `decision_contract:invalid_shape`로 확정 실패했다. 실패한 원시 출력은 저장되어 있지 않아 구체적인 잘못된 필드를 단정하지 않는다. 형식 예시를 전달한 후의 요청도 실패했으므로 그 조치는 해결로 기록하지 않았다.

네 실패 요청의 ID, 입력 binding, 영수증 hash와 원래 blocked 상태를 보존했다. 직원의 `DataAssessment`나 연구 책임자의 `TaskDecision`을 운영자가 작성하지 않았다.

데이터 담당자의 새 판단은 승인된 패킷 identity·입력·엔진·필수 보고서·누적 계보를 확인한 결과다. 커버리지와 이론적 평가가격 계약은 확인됐고, `point_in_time`, `executable_prices`, `original_conditions`는 모두 false로 유지됐다. 승인된 세 역사적 증거 공백과 `conditional_retrospective_development` 범위를 그대로 기록했다. 서비스는 원래의 source·전체 읽기·scope·packet 검증을 적용했다.

| 직원 단계 | 실제 판단 | 상태 |
| --- | --- | --- |
| 연구 담당자 | 분산예측 수준 보정에 관한 반증 가능한 새 가설 | 저장 완료 |
| 데이터 담당자 | `conditional_ready` | 10:38:31 KST 저장 완료 |
| 연구 책임자 | `revise`: 선행 과제와의 차이·동결 엔진 연결을 구체화 | 10:45:42 KST 저장 완료 |

책임자는 같은 문헌·종목·기간·월별 수준 보정이 선행 계보에 이미 있음을 지적했다. 수정안에는 신호와 라벨 변경의 경제적 이유·검증 대상·중복 범위를 직접 비교하고, 정보 경계·월별 완료 라벨·비중 적용·보유수익·기본 및 스트레스 비용·종료 처리를 고정 엔진에 맞춰 명시하도록 요청했다. 이 내용은 실제 책임자의 판단이며, 운영자가 과제의 신규성이나 수익성을 판정한 것이 아니다.

10:45:44 KST에 정상 controller가 후속 연구 제안 단계를 만들었다. 연구 담당자의 첫 실제 Codex 요청에 이전 과제의 전체 피드백 파일 navigation이 포함됐으며, source 파일은 immutable SHA-256으로 연결된다. 후속 연구는 같은 계보·예산 안에서 진행되고 있다. 10:48:05 KST에 3070의 인증된 polling과 실제 worker/tunnel 서비스의 active 상태를 확인했다.

## 기술 수리와 검증

수리 source는 `5c44ad07273adc780a56a47d7d35114fd0dc32c8`이다. [ADR 0038](../adr/0038-private-research-data-output.md)에 응답 계약과 적용 조건을 기록했다.

- 데이터 단계의 네이티브 구조화 응답을 `read` 또는 `complete`로 제한한다. 서비스가 검증한 후 기존 회사 결정 객체로 변환한다.
- 데이터 판단 본문은 기존 `DataAssessment` 검증과 읽기·권한 조건을 계속 통과해야 한다.
- 새 프로토콜의 형식 실패는 세 번에서 보류하며, 실제 실패 영수증과 요청을 남긴다.
- 긴 보고서를 읽은 후에도 보호된 평가 엔진과 입력 identity가 문맥에 남는다.
- 공식 ChatGPT 인증, 모델 배정, sandbox, 데이터 정책, 누적 예산과 보호 엔진은 유지한다.

| 검증 | 실제 관찰 | 범위 |
| --- | --- | --- |
| [source CI](https://github.com/JJongAchii/quant-ai-company/actions/runs/37393064952) | 1,752 passed, 47 skipped, 1 deselected | 실제 PostgreSQL·Temporal, Codex 프로세스 이벤트는 해당 테스트에서 simulated |
| 3070 warmup | 104 거래일, 1,040행, 실제 networkless bubblewrap | 입력 producer/consumer 호환성, 성과 계산·학습·sealed 입력 없음 |
| 네 종류 서버 이미지 | source 205개 파일과 네이티브 스키마 확인 | 정확한 기존 이미지에 코드만 추가, 네트워크·credential mount·모델 호출 없음 |
| 3070 release 등록 | 새 source를 정확히 지정한 작업에서 해석 가능 | 기존 supervisor·설정·토큰 경로·과거 release mapping 보존 |

입력 운송·이미지·CI 검증과 실제 직원 응답은 별도로 기록한다. 실제 독립 데이터 판단의 저장 증거는 `staff-state-native-10.json`에 있다.

서버 여섯 서비스(API, dispatch, Slack socket, company worker, account gateway, primary Codex runtime)의 실제 source origin과 205개 파일이 source5c44ad0과 일치한다. 그 외 아홉 컨테이너의 ID와 이미지는 보존했다. 실제 PostgreSQL 승인과 inbound, 현재 승인된 이력 한도, 데이터·정책·엔진 연결, Temporal RPC와 3070의 인증된 polling을 확인했다.

배포 후 다섯 번째 시도의 네이티브 요청 63개가 완료됐다. 마지막 판단 JSON은 구형 top-level `data_policy_digest`·`evaluation_prices`가 신형 schema_version2에 섞여 저장이 거부됐다. 해당 원본을 보존하고 오류 내용을 실제 데이터 담당자의 아직 고정되지 않은 후속 요청에 전달했다. 판단 필드를 운영자가 고쳐 저장하지 않았다.

여섯 번째 시도의 44번째 요청은 Codex 이벤트 파싱 단계에서 확정 실패했다. 원시 오류 이벤트는 보존되어 있지 않으므로 구체적 형태를 단정하지 않는다. 실패 영수증과 누적 실패 수2, 완료된43개 읽기를 보존한 채 새로운45번째 요청으로 같은 직원 시도를 이어갔다. 후속 요청과 직원의 새 판단이 정상 처리됐고, 10:38:31 KST에 데이터 검토가 완료됐다. 기존 요청은 재호출하거나 수정하지 않았다.

재발 방지 source `86aea8cdf03b5543666813c97689acb1e8158a6d`는 과제에 승인된 버전의 출력 스키마만 노출한다. 기존·신형 데이터 정책의 business 검증과 직원 판단 권한은 유지한다. 실제 PostgreSQL 기반 로컬 회귀 테스트78개와 전체 Ruff를 통과했다. [후속 CI](https://github.com/JJongAchii/quant-ai-company/actions/runs/37399944764)는 1,754 passed, 47 skipped, 1 deselected로 성공했다. 이 추가 source는 아직 운영에 배포하지 않았다. 현재 운영 source는 검증한5c44ad0이다.

## 데이터와 남은 단계

입력은 기존 835 거래일·10 ETF의 고정 빈티지 자료다. warmup 104일과 개발 731일로 나뉜다. 입력 bytes, 숫자 품질 영수증, 정책과 보호된 평가 엔진을 바꾸지 않았다.

허용된 결과 범위는 `conditional_retrospective_development`다. 역사적 공표 시각과 수정 빈티지 등이 미검증이라는 세 gap과 T일 23:59 KST 가정을 유지한다. 실제 point-in-time 자료나 체결·총수익 성과로 해석할 수 없다.

첫 전체 백업의 SHA-256은 `276a1ade77dff2db9211d3ede5a31035587ac003db557dd29dc7375e68073ec5`이며 S3 복사 완료 영수증을 확인했다. 첫 교체는 개별 Compose chain과 마운트 검증에서 멈췄으며 기존 서비스로 복귀했다. 후속 시도에서 각 서비스의 원래 chain을 그대로 사용했다. 후속 전체 백업 SHA-256은 `8331d198bba7996201ce79614138eea862bb86e167d9454ec4994d6dd4c12212`이며 S3 복사를 마쳤다. 두 백업과 중단 기록을 모두 보존한다.

교체 후 account gateway 안의 추가 full-import 확인 프로세스가 exit137로 종료됐고 한 번의 gateway restart가 관찰됐다. 정확한 종료 원인은 확정하지 않는다. live 컨테이너 안에서는 표준 라이브러리로 source origin과 파일 hash만 확인하고, backend 확인은 기존 company worker의 trusted loader에서 수행하여 인수 검증을 마쳤다. 이미지 준비 단계의 네이티브 스키마 검증은 유지되며, 메모리 한도와 Codex sandbox는 변경하지 않았다.

서버 교체는 모든 런타임 실행 lane을 확보하고 일관된 PostgreSQL·설정·영수증 백업을 성공한 뒤 수행했다. 기존 뉴스의 uncertain 요청과 과거 orphan 영수증은 그대로 보존했고 재실행하지 않았다. 교체 후 실제 source, 건강 상태, 승인·입력 binding, Temporal RPC와 인증된 worker polling을 확인했다.

초기 데이터 검토와 책임자의 수정 결정까지 실제 직원 응답으로 기록했다. 다음 단계는 피드백을 반영한 수정안을 다시 독립 검토하는 것이다. 과제 수락 후에 승인 범위 안의 가설·반론 처리·구현·3070 실행·유효성 및 의미 감사가 이어진다. 현재 수정 결정은 실험 실행 승인이나 연구 성과 판정이 아니다.

## 증거

[운영 증거 폴더](evidence/conditional-program-20261006/)에 실제 승인, 직원 진행 상태, 네 실패 영수증, 보류, CI, 이미지 준비, worker 검증·등록 및 검증용 operator helper를 남겼다. 모든 새로운 회사 기록은 이 저장소에만 저장한다.
