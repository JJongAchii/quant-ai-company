# 2차 탐색 실험의 수정 경로 복구 · 2026-10-12

**10:30 KST 확인: 운영 수리와 실제 직원 응답은 확인됐다. 2차 경제평가는 아직 실행되지 않았고, 필요한 입력 근거를 확인하는 단계에서 보류됐다.**

현재 자료 확인과 수정안 준비에는 사용자 재승인이나 Slack 메시지 재전송이 필요하지 않다.

## 완료한 작업

- 실제 실패 구현과 사전검사 오류를 SHA로 검증해 기존 수정 업무에 제공했다. 변경 전 기준 코드와 실제 실패 코드를 구분했다.
- 직원에게 정확한 수정안·근거 보류 응답 형식을 제공했다. 유효한 보류 응답을 저장하고, 같은 실패 요청의 자동 재시도를 멈추도록 고쳤다.
- **09:46:47 KST**에 실제 API와 worker에 적용했다. 다른 운영 서비스의 변경, 모델 설정, 환경 값, 승인된 연구 범위·예산과 3070 연구 release를 보존했다. 백업을 확인하고 배포 일시정지를 해제했다.
- **09:50:57 KST**에 기존 대기 업무의 타이머를 해제했다. 현재 선택된 계정 revision을 사용했고 계정 전환이나 새 연구 업무를 만들지 않았다.
- 실제 직원이 새 응답 형식과 오류 근거를 읽고 **09:54:10 KST**에 독립적인 근거 보류 응답을 제출했다. 서비스가 이를 저장했으며 추가 자동 재시도는 예약되지 않았다.

구현 commit은 `d0386ef`, 실제 운영 적용 패키지는 `3171e59a5c7f6a2332c33b3bc1249abddccba6ef`다. PR #105는 초안 상태이며 canonical merge를 수행했다고 주장하지 않는다.

## 실험이 보류된 이유

직원은 선택된 구현이 `decision_at`과 `prior_events`를 필요로 하는데, 동결된 `weights(history, config)` 입력에서 이를 제공하거나 복원할 근거를 확인하지 못했다고 판단했다. 실제 사전검사도 `actual_decision_and_past_public_inputs_unverified`로 실패했다.

이는 직원의 독립적인 수리 판단이다. 이번 운영 복구에서 평가기 결함이나 연구 준비성 통과를 과학적으로 확정하지 않았다. 직원은 오류 자료와 실패 manifest·설정을 끝까지 읽었고, 실패 candidate는 24,000자를 읽었다. candidate 전체를 읽었다고 주장하지 않는다.

첫 실험은 기존 보고 상태를 유지한다. 두 번째 trial은 `technical_waiting`, 기존 job은 `failed`다. 10:30 KST의 실제 기록에서도 수정 업무는 attempt 79, `repair_requires_evidence`, 다음 재시도 시각은 null이다. 두 번째 경제평가나 새 과학 job은 없다.

## 다음 작업

1. 실제 사전검사·평가기 호출이 전달하는 입력을 확인하고, 자격검사 호출과 첫 개발 결정 사건을 구분한다. 결정 시각·이전 사건·당시 공개 입력을 기존 허용 범위에서 연결할 수 있는지 확인한다.
2. 첫 실험의 보관 코드·설정·manifest·출력·영수증을 실제 SHA로 연결하고, 직원이 요구한 재사용 권한이 기존 서명된 명세에 있는지 확인한다.
3. 근거가 갖춰지면 허용된 `candidate.py`와 `config.json`에서 가능한 구체적인 수정안을 준비한다. 이후 사전검사를 통과한 실행만 경제평가로 진행한다. 보호된 평가기·실행 profile 변경이 필요하면 별도 명세와 정확한 변경 검토를 먼저 준비한다.

후속 입력 계약 검토 명세를 기록했다. 이 후속 수정의 구현·배포·재실행은 아직 수행하지 않았다. 현재 보류 결과, 첫 실험 결과, 예산 차감과 기존 실패 기록을 유지한다.

## 검증과 관측 범위

- 실제 PostgreSQL·Git·고정 qlab을 사용한 전체 회귀: **1,838 passed, 14 skipped**. 수정 경로 집중 검증 17 passed, lint passed.
- 테스트의 직원·Slack·시장 응답은 합성 자료다. 실제 운영 이미지의 업무 입력 검증, 실제 직원 호출·파일 읽기·보류 저장은 별도 운영 기록으로 확인했다.
- 이번 적용 후 새 signed Slack 메시지의 수신부터 답변까지 왕복 검증은 수행하지 않았다. 실제 직원의 내부 업무 응답 복구를 확인한 것이다.
- 읽기 전용 관측기는 보류 응답을 확인한 **09:54:47 KST**에 종료했다. 관측 13회, 읽기 오류 0건이며 현재 실행 중이 아니다. 자동 알림·수리 기능은 없다.

## 근거

- [실제 운영 적용 영수증](evidence/etf-exploration-20260930/research-repair-runtime-applied-20261012.json)
- [실제 직원 호출·읽기·보류 응답](evidence/etf-exploration-20260930/research-repair-actual-staff-evidence-hold-20261012.json)
- [10:30 KST 실제 상태 확인](evidence/etf-exploration-20260930/research-repair-final-live-state-20261012.json)
- [관측기 종료](evidence/etf-exploration-20260930/research-repair-observer-completed-20261012.json)
- [후속 입력 계약 검토 준비 명세](evidence/etf-exploration-20260930/research-repair-next-input-contract-review-20261012.json)
- [회귀 검증](evidence/etf-exploration-20260930/research-repair-regression-20261012.json)
