# 직원 전문 절차와 계속되는 교육·평가

## 현재 제공하는 구조

- 11개 직무의 버전/digest가 있는 전문 업무 절차를 실제 모델 요청에 전달한다.
- 금융전략/연구/개발/검증/리스크에 금융 계산, 데이터/개발/검증에 표본 품질 검사를 연결한다.
- 기존 활성 직원 4명과 개선 서비스의 운영 상태는 유지한다. 비활성 6명도 격리 교육 평가에 참여하지만
  그 결과로 Slack 직원·실행 권한이 켜지지는 않는다.
- PostgreSQL이 문제·요청·응답·도구·점수·피드백을 보관하고 Temporal이 예약과 재시도를 담당한다.
- 피드백은 다음 업무와 개선 BOT 관찰에 연결한다. 일반 사실 기억의 자동 승인은 없다.

## 일정과 비용 제어

`COMPANY_STAFF_DEVELOPMENT_ENABLED=true`일 때 KST 03시 이후 하루 `STAFF_DAILY_EXERCISES=2`개를
가장 적게 연습한 직원부터 배정한다. `STAFF_MAX_CALLS_PER_EXERCISE=3`이므로 예약 교육은 하루
최대 6개 신규 모델 요청이다. 같은 요청의 busy/quota 대기는 새로운 요청을 만들지 않는다.
11명을 약 6일에 걸쳐 순환하며 각 직원의 문제 유형은 두 종류를 번갈아 사용한다.
baseline/manual 평가는 명시적으로 등록된 일회성 업무이며 이 일일 자동 예약 수에 포함되지 않는다.

일반 업무와 구독 backoff가 있으면 교육을 연기한다. 실행 중 모델 호출은 중단하지 않는다.
quota/auth/불확실한 호출은 실력 부족으로 점수화하지 않는다. blocked는 operator 대사가 필요하고
정기 다음 문제는 기록을 덮어쓰지 않는다. feature flag를 끄면 기존 교육 workflow도 새 호출을 멈춘다.
새 서버나 API 요금제는 추가하지 않지만 Codex 구독 사용량과 저장 공간은 사용한다.

## 질문 예

Slack 총괄에게 “직원별 최근 평가와 부족한 부분을 알려줘”, “데이터 담당은 어떤 도구와 절차를 쓰니?”라고
요청하면 staff_status 및 runtime config로 확인할 수 있다. 평가는 조용히 저장되며 문제마다 Slack을 보내지 않는다.

운영자 전용 명령(서비스 환경/인증을 사용하는 호스트에서 실행):

```bash
quant-company staff status
quant-company staff status --employee financial_strategist
quant-company staff enqueue --employee data --id <UUID>
quant-company staff tick
quant-company staff review --id <평가 UUID> --disposition disputed --note '가정 또는 채점 기준의 구체적인 문제'
```

enqueue는 기본 허용 소유자에 묶이며 같은 UUID는 같은 과제만 반환한다. tick은 한 번의 호출만 처리한다.
operator API는 `GET /v1/staff`, `POST /v1/staff/runs/{id}/review`다. 인증은 기존 operator token이며
공개 Slack endpoint에서 접근할 수 없다. 일반 staff_status는 정답키나 비공개 문제 전체를 반환하지 않는다.

## 문제은행과 판정의 해석

| 직원 | 두 가지 평가 영역 |
|---|---|
| 총괄 | 변경된 지시/산출물 완결성, 실행 권한 |
| 금융전략 | 현금흐름 할인/듀레이션, 옵션 무차익 관계 |
| 데이터 | 가용시각/중복, 누락 키/입력 결함 |
| 국내연구 | 발표 지연, label/적합 구간 겹침 |
| 글로벌연구 | 시장 간 가용시각, 통화 방향/환산 |
| 가상자산연구 | 이벤트/지연, 왕복 비용/펀딩 |
| 개발 | bp 단위 변환, 독립 수치 대조 |
| 검증 | scope/commit 연결, 감사 증거 부재 |
| 리스크 | 총/순 노출과 시나리오, 위험 해석 |
| 운영 | 불확실한 외부 송신, 복원 증거 |
| 개선 | 오래된 진단, 평가 독립성 |

정답은 직원/개선 BOT의 판단으로 정하지 않는다. 사전 고정된 코드가 수치 허용오차·오류 누락·오탐을 검사한다.
설명은 보관되지만 `explanation_review=unscored_requires_independent_review`다. 질문의 제한된 요구를 넘어선
추론 능력·전문가 대비 능력·실제 연구 성과를 이 점수로 표현하지 않는다. 문제은행은 새 숫자·사례 조합을
생성하며 매번 새로운 학문 분야나 문제 유형을 발명하는 서비스는 아니다.

같은 seed, suite version, code, role/pack snapshot으로 입력을 재현할 수 있다. 과거 기록은 덮어쓰지 않는다.
평가 변경은 suite 버전을 바꾸고 별도 검토한다. 서로 다른 모델·문제 유형·pack 결과를 하나의 상승 점수로 합치지 않는다.

## 개선 경로

새 실패 → 직원별 연습 피드백 + 개선 담당의 관찰 → 현재 코드/입력 확인 → 재현 가능한 수정/검증 → PR →
사람 검토 → 운영 반영 → 새 문제와 실제 업무에서 확인.

실패 사례가 공개된 뒤의 재풀이는 연습이다. 같은 답 암기나 평균 점수 상승만으로 개선을 확정하지 않는다.
문제가 틀렸다면 disputed로 보존하고 정정 문제는 새 suite/새 과제로 만든다. 실제 업무의 source-backed
기억은 기존 제안·검토 경로로 축적한다. 미검토 메모/합성 정답은 공통 금융 사실로 승격하지 않는다.

## 숫자 도구의 범위

`finance_compute`는 rates/returns를 분수로 받는다(5%=0.05). 채권은 쿠폰 지급일, 일정 현금흐름,
지급 주기로 복리화한 명목 수익률을 가정한다. 옵션은 유럽형·상수 변동성·연속 배당이며 vega는 변동성
1%p 변화당 가격 변화다. FX는 기준통화/외화 환율이다. 시나리오는 선형 포지션으로 비선형 파생상품의
완전 재평가가 아니다. 참고: [FINRA](https://www.finra.org/investors/insights/bond-yield-return),
[MSRB](https://www.msrb.org/sites/default/files/Evaluating-Interest-Rate-Risk.pdf),
[OIC](https://www.optionseducation.org/advancedconcepts/black-scholes-formula).

`data_quality`는 제공된 최대 500행만 검사한다. 전체 레이크/기업행사/PIT 유니버스의 품질 인증을 하지 않는다.
두 도구는 입력·단위/가정·버전·digest를 반환하고 임의 코드·워커 제출·거래를 수행하지 않는다.

## 실제 모델 인수

`scripts/qualify_staff.py`는 opt-in 구독 호출과 별도 `staff_qualification_*` PostgreSQL DB를 사용한다.
입력/출력·모델·pack·도구·판정을 저장하며 실제 Codex 호출과 fixture 테스트를 구분한다.
CI에는 이 구독 호출을 넣지 않는다. 실행 기록과 배포 여부는 `docs/project`의 검증 문서에 별도로 남긴다.
