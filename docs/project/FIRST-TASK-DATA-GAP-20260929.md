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

다음 구현 단위는 **이미 승인된 입력 해시와 실행 프로필에 묶인 읽기 전용 데이터
증거 패킷**이다. 위 대조의 한계와 원천 공개시각·체결 조건을 함께 담고,
패킷의 해시와 출처를 서비스가 검증한 뒤 데이터 직원에게 제공해야 한다.
`real-inputs.json`의 집계나 현재 레이크 대조만으로 `ready`를 자동 부여해서는 안 된다.
프로그램의 허용 원문 목록이나 입력 계약을 바꾼다면 새 digest에 대한 소유자 승인이
필요하다. 그 전까지 현재 과제를 실행 대상으로 승격하지 않는다.

director 보류 직후 새 검토 자료 1편이 유입돼 다음 연구자 제안 단계가 열렸다.
[자료 버전 변경 영수증](evidence/research-programs-20260929/new-literature-trigger.json)은
새 자료가 암호화폐 변동성 예측 논문임을 기록한다. 이 유입은 국내 ETF 입력의
위 공백을 해소했다는 증거가 아니다. 뒤이어 등록된 두 번째 ETF 과제의
[독립 데이터 심사](evidence/research-programs-20260929/second-task-data-assessment.json)도
고정 ETF 입력과 실행 조건의 과제별 조회 근거가 없어 `blocked`를 기록했다.
