# 승인된 ETF 탐색 연구 · 2026-10-12 진행 점검

**10월 12일 08:16 KST의 실제 기록에서 후속 실험은 진전 없이 수정 대기 중이다.**
추가 소유자 승인을 기다리는 상태는 아니다. 이번 점검은 읽기 전용이며 운영 수정을 적용하지 않았다.

## 실제 진행

- 첫 실험은 실제 평가·독립 감사·의미 검토·보고·Slack 전달을 완료했다.
- 두 번째 후보 구현은 10월 8일 완료됐지만, 실제 3070의 사전 자격검사가
  **13:10:56 KST에 exit code 1로 실패**했다. 평가 sandbox와 성과 실행 영수증은 없다.
- 해당 trial은 `technical_waiting`, job은 `failed`다. engineer 수정 stage는 78번째
  시도 이후 `waiting`이며 다음 자동 재시도 예정은 10월 12일 08:59:36 KST다.
- 서버의 여섯 서비스는 실행 중이며, healthcheck가 있는 네 서비스는 healthy다.
  Socket 연결도 관측했지만 이번 점검에서 새 signed Slack 왕복을 수행하지 않았다.

## 반복 수정이 진전되지 않은 원인

실제 worker 진단은 `research_candidate.ReadinessError:
actual_decision_and_past_public_inputs_unverified`다. 기록된 후보의 readiness 요구를
이번 운영 점검에서 과학적으로 판정하거나 제거하지 않았다.

수정 직원에게 제공된 candidate/config SHA는 변경 전 기준 코드의 SHA다.
실제 실패 구현의 SHA와 다르며, 실패 구현과 worker 오류 진단 파일은 읽기 목록에 없다.
최신 직원 응답은 `repair_disposition=blocked`, `ready_for_retry=false`, 빈 `patches`로
실패 코드 증거를 요구한다. 서비스는 이를 패치 형식 오류로 거절해 다시 시도한다.

실제 수정 프롬프트의 `Return the same patch schema`는 해당 형식을 제시하지 않는다.
`output_schema`도 null이다. 이 형식 문제와 누락된 증거는 별도로 복구해야 한다.
새로운 증거 없이 같은 수정 호출만 반복한 것을 연구 진전으로 세지 않는다.

## 감시 상태와 다음 조치

10월 8일 읽기 전용 관측기는 **14:52:07 KST에 4시간 한도로 종료**했다.
318회 관측, 읽기 오류 0건이다. 자동 알림·수리 기능은 없었다.
10월 8일 11시 구현 진행 확인 이후 계속 감시하거나 후속 실험을 완료했다고 주장할 수 없다.

다음 복구는 실제 실패 코드와 정제한 진단의 SHA를 검증하여 직원에게 제공하고,
수정 응답 계약을 명시하는 것이다. 패치와 과학적 판단은 기존 독립 직원이 수행한다.
서명된 범위·예산·과거 부정 결과·완료 응답·평가 및 감사 기준을 유지하며,
이번 상태 점검에서 재실행·배포·성과 채택은 하지 않았다.

근거: [현재 상태·실제 DB 응답 형태·3070 파일 영수증](evidence/etf-exploration-20260930/second-trial-current-status-20261012.json).
