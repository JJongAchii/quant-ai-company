# 첫 연구 과제의 데이터 근거 공백

2026-09-29 12:45 KST에 승인된 프로그램 `e06537d3-fac3-5c8c-bf25-ddabb3c7e282`에서
자료 원문을 완독하고 인용 7건을 검증한 첫 `novel_hypothesis` 과제
`dae4bd01-7477-55c7-810e-9e4fbec59dfb`가 등록됐다. 선행 미션은 0건이다.
[연구자·데이터·director 단계 영수증](evidence/research-programs-20260929/first-task-independent-review.json).

독립 데이터 직원은 `point_in_time`, `coverage`, `executable_prices`,
`original_conditions`를 모두 미확인으로 두고 `blocked`를 기록했다. director는
이 판단을 받아들여 과제를 `waiting`으로 남겼다. 이는 데이터 부재나 전략 실패가
증명됐다는 뜻이 아니라, 현재 과제에 연결된 증거로 입력과 거래 조건을 검증하지
못했다는 뜻이다. 미션·자원 예약·과학 시행은 0건이다.
[데이터 심사](evidence/research-programs-20260929/first-task-data-assessment.json),
[director 최종 판단](evidence/research-programs-20260929/first-task-final-decision.json).

## 이미 있는 준비 기록과 심사에 필요한 기록

| 항목 | 현재 확인된 기록 | 현재 과제의 독립 심사 상태 |
| --- | --- | --- |
| 고정 입력 | 프로그램 명세에 ETF `warmup.json`·`development.json` 해시와 lake ID가 있다. [초안](evidence/research-programs-20260928/first-program-draft.json) | 심사 직원이 실제 입력 객체와 해시 일치를 읽어 확인한 영수증이 없다. |
| 수집·유니버스 | 이전 [실제 입력 준비](evidence/research-programs-20260928/real-inputs.json)에 qdata 커밋, S3 객체 메타데이터, 2022-12-29 선정 기준, 구간과 행 수가 기록됐다. | 당시 상장·상폐, ETF 분류, 원시 거래대금과 공개 가능 시각의 독립 재검증은 제시되지 않았다. |
| 실행 경로 | [3070 준비 구간 자격검사](evidence/research-programs-20260928/warmup-qualification.json)는 실제 샌드박스에서 통과했다. | 08:30 신호의 정보 이용 가능성과 당일 종가 체결, 정지·배당·상장폐지 처리의 현재 과제별 증명은 없다. |

현재 [자료 선택 코드](../../src/quant_company/research/library.py)는 프로그램의
`source_ids`, 검토 통과한 quant feed 원문, 이 프로그램의 검증된 출판물만 직원에게
제공한다. 승인된 프로그램의 `source_ids`에는 이전 연구 보고서 1건만 있다.
따라서 회사 저장소의 위 입력 준비 영수증은 파일로 존재하지만, 첫 데이터 직원의
읽기 가능한 과제 자료에는 포함되지 않았다. 이는 코드와 운영 심사 기록을 대조한
접근 경로 분석이며, 영수증 자체가 모든 데이터 요건을 충족한다는 판정은 아니다.

추가 읽기 전용 [ETF 입력 계약 대조](evidence/research-programs-20260929/etf-input-contract-readback.json)에서
준비된 두 JSON 파일의 SHA가 승인 명세·준비 영수증과 일치했다. 각 거래일의
10개 종목 행, 필수 열, 중복·결손 부재, 입력에 적힌 `available_at`이 다음 거래일
08:30보다 늦지 않은 것도 확인했다. 마지막 개발 거래일의 다음 신호 시각은 이
입력 밖이므로 확인하지 않았다. 이 검사는 **파일에 적힌 시각**을 대조한 것이며,
원천 공개 시각·당시 전체 ETF 모집단·종가 체결 가능성을 독립적으로 증명하지
않는다. 데이터 직원의 `blocked` 판단은 그대로 유지한다.
[재검증 스크립트](evidence/research-programs-20260929/verify-etf-input-contract.py)는
이 두 준비 입력을 읽기 전용으로 검사하며 성과나 봉인 구간을 열지 않는다.

## 고정 qdata API와 현재 레이크 재대조

2026-09-29 13:24 KST에 준비 영수증의 qdata 커밋
`d6d7d0ed066ec49541e9acdd657c9ec5692ffc52`에서 47개 패키지 파일을
검증한 격리 사본으로 S3 미러를 읽었다. 현재 ETF 시세·메타 객체의 크기·ETag·
수정시각은 준비 영수증의 객체와 다르고, 준비 영수증에는 객체 버전 ID가 없다.
따라서 **당시 객체를 지정해 다시 읽었다고 주장할 수 없다.**
[독립 qdata 대조 영수증](evidence/research-programs-20260929/etf-qdata-independent-readback.json),
[재현 스크립트](evidence/research-programs-20260929/verify-etf-against-qdata.py).
이후 읽기 전용 [S3 버전 이력 조회](evidence/research-programs-20260929/etf-s3-version-history-readback.json)에서도
두 객체 키마다 현재 `null` 버전 한 개만 확인됐다. 목록은 잘리지 않았고 버킷의
버전 관리 응답에는 활성 상태가 표시되지 않았다. 따라서 이 버킷의 VersionId로
준비 당시 객체 바이트를 복구할 수 없다.

현재 객체의 2022-12-29 당시 전체 ETF 일별 시세와 메타로 20거래일 거래대금 순위를
다시 계산한 결과, 승인된 10종목 cohort와 순서까지 같았다. 준비·개발 835거래일의
8,350개 종목 행에서 `open`·`close`·`value`와 거래 가능 플래그는 고정 입력과
일치했고 누락 행은 없었다. `adj_close`는 6,680행에서 현재 객체와 달랐지만,
각 종목의 현재값/고정값 비율은 기간 내 일정했다. qdata의 조정계열이 새 asof에
맞춰 재기준화된 결과와 **일치하는 양상**이다. 원래 수집 객체의 바이트나
각 날짜의 실제 공개시각을 복구했다는 뜻은 아니다.

고정 입력에 적힌 `available_at` 8,340행은 다음 거래일 08:30 이전이다. 마지막
거래일 10행은 이 입력에 다음 거래일이 없어서 제외했다. 보호된 실행 엔진의
해시는 준비 영수증의 `1984bf6b7fa5997a8b7ba446061ff663bdf78c683776be945da9130a067480cc`와
일치하며, 코드는 신호 시각 이전 원본만 후보에 넘기고 당일 시가부터 다음
거래일 시가까지의 가격을 사용하며 거래 비용을 적용한다. 실제 주문 호가·정지·
상폐 정산과 원천 공개시각은 이 검사로 확인되지 않는다. 성과·봉인 구간은 읽지 않았다.

05:27 UTC에 이미 승인된 입력 해시와 실행 프로필에 묶인 읽기 전용
[데이터 증거 패킷](evidence/research-programs-20260929/etf-data-evidence-registry.json)을
연구 워커에 연결했다. 서비스는 실제 `warmup.json`·`development.json`·보호된
엔진과 세 보고서의 바이트를 검증해 불변 단계 파일로 복사한다. 데이터 직원이
패킷 식별자, 엔진, 보고서를 이번 시도에서 끝까지 읽은 영수증이 없으면 심사를
제출할 수 없다. 패킷은 [미확인 사항 5건](evidence/research-programs-20260929/etf-data-evidence-note.json)을
명시하며, 남아 있는 동안 서비스가 실제 프로필의 `ready`를 거부한다.
[후보 실파일 검증](evidence/research-programs-20260929/data-evidence-worker-qualified.json),
[워커 전환](evidence/research-programs-20260929/data-evidence-worker-cutover.json),
[운영 읽기 확인](evidence/research-programs-20260929/data-evidence-worker-postcheck.json).
`real-inputs.json`의 집계나 현재 레이크 대조로 `ready`가 자동 부여되지는 않는다.
프로그램의 허용 원문 목록이나 입력 계약을 바꾼다면 새 digest에 대한 소유자 승인이
필요하다. 기존 두 과제는 `waiting`을 유지하며 새 패킷을 본 연구자 제안 단계가
시작됐다. 연구자는 패킷 식별자·보호된 엔진·보고서 세 건을 끝까지 읽고, 고정
입력 두 파일의 첫 청크를 읽었다. 입력 전체를 완독했다고 주장하지 않는다.
[직원 읽기 깊이](evidence/research-programs-20260929/data-evidence-researcher-read-depth.json).
미션·예약·과학 시행은 0건이다.

director 보류 직후 새 검토 자료 1편이 유입돼 다음 연구자 제안 단계가 열렸다.
[자료 버전 변경 영수증](evidence/research-programs-20260929/new-literature-trigger.json)은
새 자료가 암호화폐 변동성 예측 논문임을 기록한다. 이 유입은 국내 ETF 입력의
위 공백을 해소했다는 증거가 아니다. 뒤이어 등록된 두 번째 ETF 과제의
[독립 데이터 심사](evidence/research-programs-20260929/second-task-data-assessment.json)도
고정 ETF 입력과 실행 조건의 과제별 조회 근거가 없어 `blocked`를 기록했다.
director 역시 [두 번째 과제의 최종 판단](evidence/research-programs-20260929/second-task-final-decision.json)에서
입력 근거가 새로 확보되지 않은 설계 보완안으로 보고 `wait`를 기록했다.

두 번째 과제의 제안 방법은 다음 거래일 08:30 결정 뒤 **당일 종가 체결**을
요구한다. 반면 준비된 고정 실행 엔진은 거래일 09:00 전에 이용 가능한 이력으로
신호를 만들고 **당일 시가부터 다음 거래일 시가**의 가격 변화를 평가한다.
따라서 현재 제안 그대로는 두 체결 계약이 일치하지 않는다. 데이터 패킷에는
고정 실행 코드와 이 차이를 함께 제공해야 하며, 시가 체결로 과제를 재설계할지
종가 체결이 가능한 새 실행 계약을 소유자에게 승인받을지는 연구 설계 검토 뒤
결정해야 한다. 종가 체결을 현재 프로필이 지원한다고 가정해 과제를 실행하지 않는다.

## 패킷을 읽은 후속 과제

2026-09-29 14:43 KST에 연구자가 세 번째 `novel_hypothesis` ETF 과제를 제안했다.
제안 방법은 승인된 엔진과 같은 **당일 시가부터 다음 관측 시가**까지의 수익과 비용을
명시해 두 번째 과제의 종가 체결 불일치를 반복하지 않았다. 그러나 제안 자체는
데이터 적합성이나 실험 개시 승인이 아니다.
[첫 데이터 검토 영수증](evidence/research-programs-20260929/data-evidence-third-task-first-review.json).

독립 데이터 직원의 1·2회차 시도는 패킷 식별자, 보호된 엔진, 세 보고서를 끝까지
읽었지만 인용한 원문을 이번 시도에서 완독한 영수증 없이 제출해 차단됐다.
[두 번째 검토 영수증](evidence/research-programs-20260929/data-evidence-third-task-second-review.json).
3회차는 구조화된 최종 산출물 형식 오류로 대기했다. 따라서 세 번째 과제는
`proposed`이며 데이터 심사·director 선정은 아직 없다. 미션·자원 예약·과학 시행도
0건이다. [3회차 상태 영수증](evidence/research-programs-20260929/data-evidence-third-task-third-review.json).
위 출처 공개시각과 원본 객체 복구 공백은 계속 남아 있다.

4~6회차도 데이터 심사를 기록하지 못했다. 특히 6회차에는 필수 패킷 다섯 파일을
완독했지만 원문 출처 파일 읽기 기록은 0건이고 인용 검증에서 거절됐다.
[전환 직전 단계 영수증](evidence/research-programs-20260929/source-guidance-precutover-stage.json).
서비스는 미등록 출처 ID를 데이터 직원에게 한 번만 바로잡도록 안내하고, 이후에도
원문 완독 없이는 심사를 받지 않도록 수정됐다. 이 수정은 데이터 적합성을 승인하지 않는다.
[검증 결과](evidence/research-programs-20260929/source-guidance-validation.json),
[워커 전환](evidence/research-programs-20260929/source-guidance-worker-cutover.json),
[적용 후 코드·프로그램 확인](evidence/research-programs-20260929/source-guidance-worker-postcheck.json).
2026-09-29 15:27 KST 현재 과제는 `proposed`이고 다음 데이터 재시도를 기다린다.

새 워커의 7회차 요청에는 허용 출처 ID 안내가 실제로 포함됐다.
[모델 요청 확인](evidence/research-programs-20260929/source-guidance-live-prompt.json).
서비스가 미완독 원문을 지정하자 데이터 직원이 **같은 7회차**에서 보고서 원문을
끝까지 읽었고, 데이터 심사가 완료됐다.
[원문 읽기 영수증](evidence/research-programs-20260929/source-guidance-live-original-read.json),
[독립 데이터 판단](evidence/research-programs-20260929/third-task-data-assessment.json).
판단은 `blocked`다. 고정 10종목·835세션 입력의 관측 범위는 확인해 `coverage=true`로
두었지만, 원천 공개시각과 마지막 거래일의 다음 신호 시각이 검증되지 않아
`point_in_time=false`로 두었다. 실제 호가 체결·거래정지·상폐 정산·현금배당
처리가 확인되지 않아 `executable_prices=false`, 준비 당시 원본 객체가 없어
`original_conditions=false`로 두었다. director는 제안의 시가 체결 방법이
고정 엔진과 맞는다는 점을 인정하면서도 데이터 차단을 이유로 `wait`를 기록했다.
과거의 종가 체결 불일치를 현재안에 잘못 적용하지 않았고, 보류를 전략 실패로
취급하지 않았다. 세 과제 모두 `waiting`이며 미션·예약·과학 시행은 0건이다.
[director 원문·엔진 읽기](evidence/research-programs-20260929/third-task-director-read-depth.json),
[최종 선정 판단](evidence/research-programs-20260929/third-task-final-decision.json).
