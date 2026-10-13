# ETF 데이터 차단 해소안과 실행 계약 초안 — 2026-10-01

**복구 조사와 해시가 고정된 계약 초안을 만들었다. 현재 서비스에서 실행 가능한
상태는 아니다.** 알려진 보관 경로에서는 과거 공개·수정 영수증과 준비 당시 정제
바이트를 찾지 못했다. 제안하는 다음 구현은 **고정된 현재 자료와 명시한 가정에
조건부인 개발 연구**를 별도 권한 경로로 지원하는 것이다. 기존 PIT 판정을 참으로
바꾸는 방식으로 실행할 수 없다.

## 1. 조사 결과와 복구 한계

| 항목 | 확인한 근거 | 판정과 처리 |
| --- | --- | --- |
| 원시 본문→정제→연구 입력 | 앞선 835객체의 실제 바이트 해시·고정 qdata 47파일·8,350행 대조 | 현재 바이트의 변환 계보 확보. 과거 빈티지 증명은 별도 |
| 과거 공개·수정 영수증 | S3의 이름이 특정된 7개 보관 prefix에 객체·하위 경로 없음. 알려진 로컬 receipt/vintage/archive 경로도 없음 | 이 조사 범위에서 미복구. 조사하지 않은 다른 백업의 존재는 미확인 |
| 준비 당시 정제 객체 | 가격·메타와 경계 원시 4객체, 총 6개 key의 버전 목록에 현재 `null` 버전만 있음 | 알려진 mirror의 VersionId로 옛 바이트 복구 불가 |
| 과거 pipeline 로그 | 로그 메타데이터 109개가 모두 2026년 수정; 날짜가 있는 pipeline key 108개도 2026년 | 과거 거래일 공개 영수증을 대신할 수 없음. 본문은 이번 조사에서 읽지 않음 |
| 공식 공개 시각 근거 | KRX 피드 안내 및 OpenAPI 서비스 설명·약관 검토 | 현재 정책 안내는 확보. 실제 수집 채널의 835일 공개·수정 시각은 미증명 |
| 평가 가격·거래 가능 플래그 | 고정 엔진의 시가 간 평가·중단·비용 규칙을 초안에 명시 | 새로운 독립 평가 가격 판정 필요. 실제 체결 가능성 승인은 미부여 |

[읽기 전용 보관 조사](evidence/etf-execution-contract-20260930/availability-survey.json)는
2026-09-30 22:47:28 UTC, 즉 10월 1일 07:47:28 KST의 관측이다. 가격 본문·봉인
가격 행을 읽거나 저장소·운영 상태를 변경하지 않았다. 앞선 원시 본문 조사는
[이전 결과](ETF-RAW-LINEAGE-20260930.md)로 구별한다.

KRX는 현재 피드 상품 안내에 주간 종가정보의 16:00·18:10 분배 시각을 기재한다.
그 안내는 pinned qdata가 사용하는 `pykrx/MDCSTAT04301` 조회 경로의 과거 개별
공개 영수증이 아니다. [KRX 데이터 상품 안내](https://openapi.krx.co.kr/contents/OPP/DATA/OPPDATA002.jsp).
ETF OpenAPI 설명의 2026-01-16 수정일도 서비스 페이지 메타데이터이며 개별 ETF
관측의 수정 시각으로 사용할 수 없다.
[KRX ETF 일별매매정보](https://openapi.krx.co.kr/contents/OPP/USES/service/OPPUSES003_S2.cmd?BO_ID=nrEpCLaZpoLCTzPUMxuF).
페이지별 사실·채널·한계는 [공식 근거 검토](evidence/etf-execution-contract-20260930/official-source-review.json)에 남겼다.

## 2. 고정한 입력과 실행 조건

감사 사본으로 재생성했던 두 파일을 회사의 새로운 로컬 초안 디렉터리에 그대로
복사했다. 정제·선정·가격 변환을 다시 수행하거나 기존 실행 입력을 교체하지 않았다.
입력의 `available_at=거래일 당일 23:59 KST`는 가정으로 유지한다.
실제 source publication time과 역사적 수정 이력은 `null`이다.

| 구분 | 계약 초안 |
| --- | --- |
| 종목 | 승인 목록과 같은 비레버리지·비인버스 ETF 10개. 선정은 2022-12-29 이전 20세션의 실제 거래대금 기준 |
| 준비 | 2022-08-01~2022-12-29, 104세션·1,040행 |
| 개발 | 2023-01-02~2025-12-30, 731세션·7,310행 |
| 준비 입력 SHA256 | `d7d240e4f325d89106ba0364f43370174792a6e4e5155489118b2f0e641f6882` |
| 개발 입력 SHA256 | `7ec748039094e171d17ddfa966c89ed5fb80f254516c0808346f850906ae9c2b` |
| 변환 코드 | qdata `d6d7d0ed066ec49541e9acdd657c9ec5692ffc52`와 builder·normalizer·회사 generator의 별도 SHA256 |
| 평가 엔진 | 기존 protected engine SHA256 `1984bf6b7fa5997a8b7ba446061ff663bdf78c683776be945da9130a067480cc` |
| 목적·비용 | 개발 구간 stress-net-absolute-cagr 최대화. 기본 10bps·stress 30bps. 롱온리·총노출 ≤1 |
| 신호·가격 | 당일 09:00 KST 전에 이용 가능한 전일 이전 이력. `open * adj_close / close`의 현재→다음 관측 시가 간 이론적 수익 |
| 평가 검정 | 월별 block, 최소 30개, confidence 0.95; 기존 개발 평가 계약 유지 |
| 한도 | 1미션, 최대 4과학 시행, 회차 최대 2, patience 2, 7,200초, 동시 1개, continuous=false |
| 프로필 제안 | 별도 ID `kr-etf-retrospective-v1`; 아직 준비·등록·worker 자격검증 미완료 |

조정가격은 KRX 기준가격 등락률 연결 계열이다. 감사 사본의 마지막 기준은
2025-12-30이라 기존 입력의 절대 조정값 8,350개와 다르며 종목별 상수배 관계와
기준을 맞춘 형태가 일치했다. 새 파일은 새 입력 해시이며 기존 승인에 포함되지 않는다.

`adjusted_open`은 당일 종가·등락률로 유도한 사후 평가 수준이다. 실제 개장 체결가나
현금 분배금 재투자 총수익이라고 부를 수 없다. 후보에게 노출하는 신호 이력은
전일 이전으로 제한하지만, 이것도 역사적 이용 가능성 가정에 조건부다. 엔진은
제안 종목과 이전 보유 종목의 합집합에 대해 현재·다음 가격과 거래 가능 플래그를
강제하며, 누락·비거래 상태에서 중단한다. 현금 수익은 0이고 2025-12-30은 마지막
평가 시가만 제공한다. 별도 마지막 청산 비용은 청구하지 않는 기존 엔진 규칙도 고정했다.

2026-01-02~2026-09-23 가격 행은 이번 작업에 포함하지 않았다. 앞선 P11 자료의
해당 기간 집계 성과를 직원들이 읽었으므로 완전히 미노출된 확인 구간이라는 주장도
허용하지 않는다. 프로그램 4회·7,200초는 상위 한도다. 이번 미션은 회차 최대 2회와
continuous=false로 첫 회차 최대 2과학 result에서 종료한다. 추가 회차는 새 계약·서명이 필요하다.

## 3. 초안 파일과 승인 경계

- [데이터·가격·주장 범위 계약](evidence/etf-execution-contract-20260930/data-policy-draft.json)
- [서비스 프로그램 구조 검토용 문서](evidence/etf-execution-contract-20260930/program-schema-preview.json)
- [공개 프로필 제안](evidence/etf-execution-contract-20260930/public-profile-draft.json)
- [필드·판정·예산 이력·결과 전파 구현 명세](evidence/etf-execution-contract-20260930/implementation-spec.md)
- [파일 해시와 검토 digest 패키지](evidence/etf-execution-contract-20260930/review-package.json)
- [digest 직렬화 규칙](evidence/etf-execution-contract-20260930/digest-encoding.json)
- [실제 초안 준비 영수증](evidence/etf-execution-contract-20260930/preparation-receipt.json)

최종 검토 digest는
`d8520114b1adb978fe672847ac97c08dcd6eee3446bfcd555b8f0f9fd920c503`이다.
서비스 record digest는 정렬된 JSON의 기본 separators, public profile digest는
compact separators, 파일 SHA는 실제 파일 바이트를 사용한다. 이 값은 서명이 아니다.

현행 서비스는 `point_in_time=false`인 `ready`를 거절한다. 기존 실제 패킷의 blocking
gap이 남아 있어도 거절한다. `original_conditions`는 exact replication의 추가 조건이며
모든 novel hypothesis에 공통으로 요구하는 `ready` 플래그가 아니다.
프로그램의 목적 텍스트에 정책 SHA를 적는 것만으로 정책을 강제할 수도 없다.
따라서 새 typed policy와 별도 `conditional_ready` 경로가 필요하다.

구현 명세는 세 공백 코드만 조건부 연구의 주장 한계로 인정할 수 있도록 제안한다.
과거 공개·수정 시점과 준비 당시 바이트 미확보다. 가격 estimand·원시 계보·키·커버리지·
인과성·비거래·입력 해시 등 나머지 문제는 계속 차단한다. 조건부 결과의 표시를 계획,
worker 결과, DB 접수, 감사, 보고, 채택 판단까지 보존한다. 다른 program으로 넘어가도
동일 후보의 과제·시행·예산 이력을 이어받는 필드와 트랜잭션 검증도 명세에 포함했다.

기존 서명된 digest `53392822414ca32e89fab0f3a9a1093a350196345bd13084654a45510297159b`,
기존 v2 입력 두 해시와 프로필은 유지했다. 초안 수치 영수증은
`review_draft_not_ready`이며, 기존 프로그램·패킷·직원 판정을 변경하지 않았다.
운영의 마지막 실제 조회는 9월 30일 12:03 UTC로, 데이터 `blocked`·director `wait`,
8개 대기 과제·미션/예약/과학 시행 0건이었다. 이번에 새 운영 조회를 했다는 의미는 아니다.

## 4. 검증과 다음 구현

현재 입력·계약·근거 해시와 원래 파일 보존, 현행 PIT·미지원 정책·프로필·수치 영수증의
실제 거절 동작을 확인한 로컬 검사 26개가 통과했다.
[검사 결과](evidence/etf-execution-contract-20260930/validation.json),
[검사 코드](evidence/etf-execution-contract-20260930/validate-draft.py).
이는 미래 조건부 경로의 구현 검사, 실제 PostgreSQL/Temporal·signed Slack 검사 또는
worker 실행 자격검증을 대신하지 않는다. 전략 성과와 과학 시행은 이번 단계에서 0건이다.

독립 데이터·권한 검토 두 건 모두 최종 digest의 **검토 가능한 초안**으로 판정했다.
초기 권한 검토의 명세 결함 네 건은 구체 필드·공백 코드·결과 wrapper·후보 이력 정의로
보강했다. 첫 회차 2결과 종료 규칙과 lineage 한도의 새 서명 요구도 추가했다.
초기 패키지·판정은 보존하고, 마지막 수정 뒤 두 검토자가 최종 바이트를 다시 대조했다.
[데이터 초안 검토](evidence/etf-execution-contract-20260930/data-draft-review.json),
[권한 초안 검토](evidence/etf-execution-contract-20260930/admission-draft-review.json),
[검토 파일·해시 검증](evidence/etf-execution-contract-20260930/review-validation.json).
이것은 **로컬 초안 검토**이며 운영 직원의 준비 승인과 구별한다. readiness·실행 허용·
과거 PIT 검증·소유자 승인은 부여하지 않았다.

다음 구현 슬라이스는 명세의 typed policy·별도 조건부 판정·후보 이력·결과 scope를
서비스에 연결하고 실제 PostgreSQL/Temporal 및 격리 worker E2E로 검증하는 것이다.
그 뒤 실제 data/director 직원의 판정과 **구현된 canonical program에 대한 새 signed
Slack 승인**을 받아야 첫 제한 실험을 진행할 수 있다. 초안 파일을 기존 승인으로
직접 제출하거나 실행하지 않는다.

과거 PIT가 검증된 결과를 원하면 실제 과거 빈티지 자료가 추가로 필요하다. 앞으로의
append-only 수집 영수증은 새 관측 기간의 근거를 만들 수 있지만 2022~2025의 과거
공개 시점을 소급 증명하지는 못한다. 미래 수집의 필수 시각·바이트·수정본 항목도
패키지에 명시했으며 아직 수집·배포를 시작하거나 예약하지 않았다.
