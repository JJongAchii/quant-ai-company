# 자율 연구 검토 흐름 복구 — 2026-09-30

승인된 연구 프로그램의 자료 검토가 같은 턴에서 재개됐다. 06:35 UTC에 연구
워커만 `73fcfc3`으로 교체했고 다른 서비스 컨테이너 ID는 유지됐다. 원본 데이터와
공개 시점에 관한 기존 공백은 남아 있으며 미션·예약·과학 시행은 0건이다.

## 새 자료와 다섯 번째 과제

어제 기록 이후 원문 `Gold: Bugs, Bears and Myths`가 유입되어 자료 수가
11편에서 12편으로 늘었고, 그 뒤 `How Much Will a Babysitter Cost?`가 들어와
13편이 됐다. 두 전환 모두 기존 원문 파일 SHA는 그대로이고 증거 버전은
새 원문 때문에 바뀌었다. 단계별 패킷 집합 차이에 따른 반복 오류의 재발로
해석하지 않는다. 자료 유입 자체가 ETF 데이터 공백을 해결한다는 뜻도 아니다.
[새 원문 전환 영수증](evidence/research-programs-20260930/new-source-stage-transitions.json).

다섯 번째 ETF 과제 `3bf21cca-034b-5927-992a-6a62c7d72972`도 독립 데이터
직원의 `blocked`, director의 `wait` 판단으로 끝났다. 시가 간 평가 계약은
정합적이지만 원천·변환 이력과 실제 신호 사용 시점의 이용 가능성이 증명되지
않았다. 다섯 과제가 대기 중이고 그 다음 제안 단계가 진행 중이다.
[운영 DB 조사](evidence/research-programs-20260930/program-continuation-diagnosis.json).

## 대기 원인과 수정

후속 제안 단계의 두 번째 턴은 2026-09-29 17:39 UTC부터 요청 생성 전에
대기했다. 실행 한도·소유자 보류·미확정 모델 호출이 원인은 아니었다.
Temporal은 실행을 재시도했고 `Mission stage context needs bounded evidence
selection` 오류를 반복했다. 이전 과제 기록만 53,731자로, 원문 청크가 더해진
요청이 90,000자 계약을 초과했다.
[Temporal 이력](evidence/research-programs-20260930/program-temporal-readback.json).

수정은 이전 과제의 전체 방법·데이터 심사·선정 판단을 내용 해시가 붙은
불변 파일로 제공한다. 기본 요청에는 과제 상태, 판단 요약과 파일 위치를
제공하며 자세한 내용에 의존하려면 해당 파일을 읽도록 안내한다. DB의 전체
기록은 보존하고 기존 실행 중 단계에도 같은 방식이 적용된다. 출처 완독,
검증 기준과 프로그램 권한은 그대로다.

실제 PostgreSQL 회귀 검사에서 긴 이전 과제 12개를 준비하고, 파일을 여러
청크로 끝까지 읽어도 요청 크기가 제한 안에 머무는지 확인했다. 회귀 20건,
전체 테스트 1,373건 통과·13건 건너뜀, lint와 서비스 CI가 통과했다. 이 검사의
모델은 모의 직원이며, 운영 Codex 처리는 별도 영수증으로 구분한다.
[검증 기록](evidence/research-programs-20260930/prior-evidence-validation.json).

## 운영 적용과 실제 재개

후보 설치 소스 12개와 두 ETF 패킷을 확인했다. 승인 digest와 서명된 Slack
승인을 재확인하고, 해당 대기 턴의 요청·응답·모델 호출이 없으며 다른 실행 중
턴·불확실한 호출·실험·예약이 모두 0건인 조건에서 워커 하나만 교체했다.
같은 단계·시도·턴 ID를 유지했고 Temporal이나 DB 턴을 다시 만들지 않았다.
[후보 검증](evidence/research-programs-20260930/prior-evidence-qualified.json),
[전환 전 검사](evidence/research-programs-20260930/prior-evidence-precheck.json),
[워커 전환](evidence/research-programs-20260930/prior-evidence-cutover.json),
[설치 코드 확인](evidence/research-programs-20260930/prior-evidence-postcheck.json).

기존 턴 `74b8234f-74f6-54de-befb-3d15e24833b4`은 47,130자 요청을 만들고
06:35:51 UTC에 실제 `codex` 응답으로 완료됐다. 후속 읽기 턴들도 처리 중이다.
이전 과제 5개의 파일 바이트·SHA·JSON 내용이 원래 DB 기록과 모두 일치했다.
새 가설의 최종 제출이나 데이터 준비 승인, 과학 결과를 얻었다고 해석하지 않는다.
[실제 턴·파일 대조](evidence/research-programs-20260930/prior-evidence-live-readback.json),
[재현 조회](evidence/research-programs-20260930/prior-evidence-live-readback.py).

다음 연구 실행을 위해서는 [기존 데이터 근거 공백](FIRST-TASK-DATA-GAP-20260929.md)의
원본 객체·변환 계보와 신호 시점의 데이터 이용 가능성을 확보해야 한다. 이번
운영 수리는 그 데이터 판단을 변경하지 않는다.
