# 한계를 명시한 ETF 탐색 연구 준비

2026-09-30 조사 시작, 2026-10-01 KST 검토 명세 준비. 소유자는 **‘한계를 명시한 탐색 연구부터 준비’**를 선택했다.
이 문서와 PR은 새 연구의 운영 적용이나 서명된 프로그램 승인을 대신하지 않는다.

## 현재 상태

2026-10-01 07:50 KST 운영 읽기 확인: 기존 프로그램은 `active`, 최신 후보는 `waiting`이고,
자식 연구·과학 실행·예약·소모 계산시간은 모두 0이다.
[실제 기준 기록](evidence/etf-exploration-20260930/preparation-baseline.json)에 승인, 판정, 입력 및 배포 식별자를 보존했다.

새 명세와 증거 packet은 로컬 검토 파일로만 준비했다. 기존 서명, 입력, 평가기 및 운영 registry를 변경하지 않았다.
이전 history 증거 추가 시도는 다른 작업자의 registry 갱신을 감지해 쓰기 전에 중단됐다.
[중단 기록](evidence/etf-data-resolution-20260930/attachment-aborted.json)을 남겼으며, 새 raw 계보 보고서를 보존했다.

## 확인한 사실과 남은 한계

- [원천 계보 조사](ETF-RAW-LINEAGE-20260930.md)는 보관 원천 → 고정 qdata → 입력의 상대 가격 곡선과,
  2022년 자료만 사용한 동일한 10종목 선정을 재현했다. 준비 당시 clean 바이트·절대 조정 기준은 복구되지 않았다.
- [실제 history 검사](evidence/etf-data-resolution-20260930/approved-history-readback.json)는 보호된 평가기의
  `snapshot`·`history_at`만 호출했다. 730개 신호 날짜, 반복 조회 합계 3,420,050행에서 재기준 조정 후 가격 불일치 0,
  당일·미래 행 노출 0이었다. 모델·예측·평가·백테스트는 호출하지 않았다.
- 입력의 `available_at`은 **거래일 당일 23:59 KST라는 생성 가정**이다. 당시 실제 공개·수집 시각과 수정 이력은 미확인이다.
  이 검사는 독립 감사 판정이나 실제 역사적 시점성 증명이 아니다.
- ETF 조정가격은 KRX 기준가격 등락률 사슬이다. 현금 분배금 재투자 총수익이나 실제 체결을 입증하지 않는다.
  기존 P11의 더 넓은 기간 보고서 때문에 2026년을 모두 미관측이라고 주장하지도 않는다.

현재 보관본으로 가설을 검토할 수 있는 범위만 새 승인에 고정한다. 결측·가격 오류·미래 행 사용·코드 누수 등
추가 결함은 이 정책으로 면제되지 않는다.

## 정확한 연구 명세

[프로그램 JSON](evidence/etf-exploration-20260930/candidate-program.json),
[증거 packet](evidence/etf-exploration-20260930/candidate-evidence-packet.json),
[전체 식별자](evidence/etf-exploration-20260930/review-package.json).

| 항목 | 명세 |
|---|---|
| 새 승인 digest | `04cee0f99abab3dfb94b37756a195753960fffd5a3f623ce0dd3563bf776d162` |
| 데이터 정책 digest | `f3c28a83c7e45c615505dd24e858c58aa7a559a99a896b2feaf773e6f3c1af1d` |
| 범위 | `etf_strategy`만 허용; 국내 ETF의 후향적 가설 탐색 |
| 개발 / 준비 | 2023-01-02~2025-12-30 / 2022-08-01~2022-12-29 |
| 사용 금지 구간 | 2026-01-02~2026-09-23, 추가 행 접근 금지 |
| 종목 | 069500, 229200, 153130, 214980, 196230, 157450, 371460, 102110, 357870, 305720 |
| 평가 | 09:00 KST 이전 역사만, 조정 시가→다음 조정 시가, 현금 수익 0 |
| 비용 / 노출 | 기본 10bp, 스트레스 30bp, 총노출 상한 1 |
| 전체 연구 예산 | 24개 과학 trial, 36,000초, 6개 연구, 동시 1개 |
| 개별 연구 | cycle당 2 trial, 총 4 trial, patience 2, 최소 개선 0.001 |
| 입력 | 기존 `warmup.json` SHA `e3dc0ed5…`, `development.json` SHA `6482c63d…` 그대로 |
| 코드·프로필 | quant-lab `422e5da2…`, `kr-etf-research-v2` digest `942a9897…` 그대로 |
| 결과 용도 | 가설 생성 전용; 확증·운영 승격 근거로 인정하지 않음 |

주식과 ETF claim envelope는 이번 명세에서 제외했다. 새 과제는 원문을 읽은 뒤 독립적으로 제안·선정한다.
현재 보류된 ETF069500 GMM 노출조절 질문은 검토 후보이며, 이 문서가 특정 구현·매개변수·실험의 실행 승인은 아니다.
과거 거절 질문이나 보류 작업을 자동 재전송하지 않는다.

## 코드의 제한

1. `data.policy`는 생략 시 직렬화되지 않는다. 기존 승인·mission·ZIP 식별자는 유지된다.
2. `exploratory_only`는 정확한 정책 digest와 검증된 packet, 독립 coverage·이론적 평가가격 확인이 있어야 한다.
   `point_in_time`, `executable_prices`, `original_conditions`는 모두 **false**로 남긴다.
   일반 `ready`, exact replication, 직접 mission 승인으로 이 검토를 우회할 수 없다.
3. 다른 blocking gap이 남으면 시작하지 않는다. 감독자의 accept 직전에 증거를 다시 확인한다.
4. 같은 프로젝트·revision의 기존 active 프로그램을 취소해야 탐색 프로그램을 승인할 수 있다.
   현재 소모·예약 0 기준 예산을 옮기는 제안이다. 사용량이 늘거나 revision이 바뀌면 새 명세가 필요하다.
5. 자식 mission, worker manifest, 독립 감사 package, HTML, source 및 research-library에 동일한 정책을 보존한다.
   감사는 가정하의 코드·산출물 인과성만 판정한다. 추가 누수나 빠진 인과 증거는 fail/unverified로 남긴다.
6. 독립 해석은 `inconclusive` 또는 `not_supported`만 허용한다. `supported` 게시를 거부하며,
   공개 기록의 제목·한계·기계가 읽는 metadata에도 탐색 전용이라는 범위를 남긴다.

연구 예산은 이미 승인된 과학 범위다. Slack 일반 업무의 40건 제한을 다시 넣는 변경은 없다.

## 검증과 다음 적용

실제 PostgreSQL·Git·고정 qlab API를 사용해 승인 교체, 잘못된 packet/digest, 독립 차단, 변경된 증거,
자식 범위 보존과 producer→consumer→감사→해석→HTML/library 경로를 확인한다.
직원 의견·Slack·worker ZIP은 합성 fixture이며, 실제 연구나 배포로 표시하지 않는다.
최종 검사 결과는 [검증 기록](evidence/etf-exploration-20260930/validation.json)에 고정한다.

현재 운영 앱 `5defb8c…`의 이미 적용된 Quant Feed 수정을 연구 worker 계열과 통합했다.
새 공통 코드 기준은 `3c848af95dc33b7da444a55018fd41d374c10025`이다.
연구 입력·보호 평가기는 동일하다. 검토 PR과 [적용 명세](evidence/etf-exploration-20260930/release-plan.json)를
확인한 뒤 runbook에 따라 앱·고정 연구 worker·3070 소비자 코드의 호환성을 준비하고 운영 반영한다.

운영 코드 적용 뒤에도 연구는 자동 승인되지 않는다. research-center에서 기존 프로그램 취소와
**새 전체 명세·digest를 표시한 승인 요청**에 대한 서명된 소유자 승인이 필요하다.
그 다음 독립 데이터 평가 → 과제 선정 → 첫 실행 → 독립 감사까지 확인한다.
새 JSON 기록을 만든 뒤에는 이전 parser로 단순 rollback하지 않고, 호환 parser를 유지한 채 연구를 보류·복구한다.
