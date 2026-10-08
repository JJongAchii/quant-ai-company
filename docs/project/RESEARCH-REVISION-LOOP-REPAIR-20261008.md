# 후속 연구의 반복 심사 복구 · 2026-10-08

## 확인한 상태와 원인

10월 8일 09:39 KST에도 완료된 실제 시행은 한 건이었다. 첫 보고 이후 후속 제안 19건 중 18건이
수정 결정으로 돌아갔다. 이는 추가 사용자 승인을 기다리는 상태가 아니었다.

최근 실패 응답 네 건은 모두 현재 제안의 반론 UUID와 과거 제안들의 반론 UUID를 함께 제출했다.
서비스는 현재 제안의 반론에만 정확히 한 번 답하도록 검증하므로 이 응답들을 거절했다.
프롬프트의 `EVERY challenge`와 최근 과거 반론들이 함께 보이는 문맥이 실제 응답 대상과 맞지 않았다.

최근 완료 심사 세 건은 새 구현의 공개 연결과 검사 영수증을 실행 선정 전에 요구했다.
이 서비스는 실행 선정 뒤 engineer가 후보를 구현한다. 선정 전의 `frozen_experiment_code`는
승인된 기준 코드이며 새 제안의 구현본이 아니다. 실제 서비스 순서가 직원에게 충분히 전달되지 않았다.
이 진단은 전략의 수학적 타당성이나 경제성을 판정하지 않는다.

## 수정

- 모든 프로그램 제안·반론·선정 프롬프트에 실제 허용 동작과 선정→구현→고정 자격검사→평가→
  해석→독립 인과성 감사→독립 의미 검토 순서를 표시한다.
- 선정 프롬프트는 현재 proposal의 실제 반론 UUID를 DB에서 조회하여 응답 대상으로 명시한다.
  출력 스키마에도 해당 UUID와 정확한 응답 개수를 표시한다. 과거 반론과 부정 판단은 원장에 보존한다.
- `test`는 후속 구현과 독립 의미 검토에 전달할 검사 의무이며, 검사 통과나 명령 실행을 뜻하지 않는다.
  `revise`는 계속 선정·구현을 막는다. 선택과 판정은 직원이 독립적으로 한다.
- 운영에 먼저 적용된 `effective_role` 모델 정책 연동을 실제 설치 파일에서 확인해 그대로 보존한다.
  다른 Python 모듈, 역할·데이터·프로필·평가 기준·연구 worker release는 이번 변경 대상이 아니다.

## 검증과 적용 상태

관련 검사 **41개 통과**. 실제 PostgreSQL과 고정 qlab을 사용한 전체 회사 검사
**1,834개 통과, 14개 환경 검사 생략**, lint 통과.
직원 응답·Slack·시장 산출물은 검사에서 합성 fixture이며 이 검사 수치를 실연구 성과로 취급하지 않는다.
두 시행의 독립 의미 검토와 publication gate, 현재 반론·과거 반론의 구분, 필수 수정 시 실행 차단을 검사했다.

첫 두 서비스 교체는 활성 환경 변수의 목록 hash 대조에서 실패하여 원래 이미지로 복원됐다.
실제 원래 controller/source와 설치 파일·나머지 소스 hash 일치, 일시 중지 해제를 확인했다.
최초 도구는 목록 순서까지 hash에 포함하고 host compose의 현재 기본값을 다시 소비했다.
따라서 최초 환경 값 보존 성공은 주장하지 않는다. 개선 도구는 실제 active 환경을 host 내부에서
mode 0600 overlay로 고정하고, compose 해석 전후와 활성화 후 key/value 전체를 직접 대조한다.
순서 변경은 허용하지만 값 변경은 계속 실패시킨다. 롤백도 같은 actual 환경을 고정·검증한다.

개선된 `3c7cc91` 적용은 **10월 8일 10:50:29 KST 완료**했다. API·worker의 모든 active 환경 값,
보호 설정, 실제 모델 정책과 나머지 서비스 identity를 대조했다. 과학 worker release는 그대로다.

정상 서비스가 재개한 실제 director/Astra max 요청에는 새 capabilities와 현재 반론 UUID 한 건이
들어 있다. 실제 출력 스키마의 UUID enum·최소/최대 응답 한 건이 DB의 현재 반론과 일치했다.
후속 응답들이 정상 접수되며 읽기 5→21건으로 진행했다. 11:00 KST 관측은 selection/running이며
추가 stage 오류가 없다.

**11:11:45 KST에 독립 선정 `execute`가 실제 접수됐다.** 현재 반론 UUID 한 건만 제출했으며
누락·중복·과거 UUID가 없다. 정상 서비스가 trial `5868e1af-475c-5b7b-ad4a-da55fc3966e3`을
선정하고 engineer 구현 stage `6fc83fad-30b2-5ece-8663-d90e31734a1c`를 만들었다.
11:18 관측에서 engineer/Sol xhigh의 읽기 25건과 실제 후속 호출 진행을 확인했다.
이로써 INTENT-v18의 진단·영구 코드 수리·실제 후속 진행 확인은 완료했다.
둘째 trial은 selected/job_id=null이며 물리 실험은 아직 없다. 전체 연구·실운용 완료로 세지 않는다.

45초 간격·최대 4시간의 읽기 전용 관측기가 활성 상태다. 둘째 보고 완료 시 종료하며 자동
Slack 알림·수리·모델 호출·과학 재실행은 하지 않는다. 승인된 정상 직원 workflow가 연구를 진행한다.

## 근거

- [실제 반복 심사 진단](evidence/etf-exploration-20260930/post-report-revision-diagnosis-20261008.json)
- [운영 설치 파일·모델 정책 기준](evidence/etf-exploration-20260930/revision-loop-runtime-baseline-20261008.json)
- [전체 회귀검사](evidence/etf-exploration-20260930/revision-loop-regression-20261008.json)
- [정확한 준비·적용 도구](evidence/etf-exploration-20260930/apply-revision-loop-patch-20261008.py)
- [읽기 전용 운영 프롬프트 검사](evidence/etf-exploration-20260930/probe-revision-loop-runtime-20261008.py)
- [실제 운영 적용](evidence/etf-exploration-20260930/revision-loop-runtime-applied-20261008.json)
- [실제 선정 요청](evidence/etf-exploration-20260930/revision-loop-actual-selection-request-20261008.json)
- [후속 응답 접수 진행](evidence/etf-exploration-20260930/revision-loop-actual-progress-20261008.json)
- [한정된 관측기](evidence/etf-exploration-20260930/revision-loop-bounded-observer-20261008.json)
- [실제 선정 접수·독립 구현 진행](evidence/etf-exploration-20260930/revision-loop-selection-accepted-implementation-running-20261008.json)

서명된 프로그램 `f7deaf96-e677-5afe-93d4-18ac387043bb`의 범위·예산·과거 결과는 유지한다.
운영 복구는 새 과학 승인이나 실운용 승인으로 해석하지 않는다.
