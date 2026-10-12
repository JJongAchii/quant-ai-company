# 2차 실험 입력 계약 확인과 기술 실패 검토 경로

## 확인한 사실

실제 3070의 첫 실험 `6cdc6e7`과 실패 구현 `cdcfe52`를 가져와 manifest의 네 코드 SHA를 모두 대조했다. 두 실험의 보호된 평가기는 같은 `1984bf6b…` 바이트다. `prediction()`은 `history`와 JSON 복제한 정적 설정만 전달한다. 평가기가 알고 있는 실제 결정 시각과 이전 호출별 이력을 callback에 전달하지 않는다.

실패 구현의 `weights()`는 필수인 결정 시각·이전 사건 인자를 생략한 채 `_public_kernel()`을 호출한다. 이 함수는 인자가 없으면 항상 `ReadinessError`를 낸다. 실제 실패 영수증도 warmup 자격검사 exit 1이며 경제평가 영수증은 없다.

선택된 가설과 독립 반박의 등록된 시험 의무는 과거 호출 입력·초기화·재기준화의 검증과 선행군 전경로 동치를 요구한다. 누락 자료를 날짜 추정·전역 캐시·미래 일정으로 보충하거나 보호된 평가기를 바꾸는 것은 허용하지 않는다.

첫 실험 archive `9c5c2a8e…`의 코드·설정·manifest·result·receipt 및 기본/스트레스 출력 SHA를 실제 보관 파일과 대조했다. 선언된 출력은 `base.csv`와 `stress.csv`다. 별도 과거 호출·목표비중 추적 기록은 archive에 없다. 기존 float/Decimal 구현과 새 정확 상태 구현의 동치를 확정하지 않았다.

근거: [입력 계약 확인](evidence/etf-exploration-20260930/second-trial-frozen-input-contract-20261012.json), [실제 worker artifact 검증](evidence/etf-exploration-20260930/input-contract-worker-artifacts-20261012.json).

## 필요한 서비스 변경

기존 유효한 engineer 보류는 같은 수정 재시도를 멈춘다. 그러나 선택된 trial이 `technical_waiting`에 남으면 다음 가설을 선정할 수 없고, 총괄에게 이를 판단할 단계도 없었다.

새 `technical_review`는 운영자가 검증한 새 계약 근거를 연결한 기존 보류에서만 시작한다. 총괄은 정확한 trial·failed job·원래 실패 출처·새 근거를 읽고 `retire_candidate` 또는 `wait`를 제출한다. 운영자가 후보 종료나 새 전략을 선택하지 않는다.

종료에는 다음 조건이 필요하다.

- 현재 승인된 프로젝트·미션과 총괄 역할.
- 원래 failed job·trial·코드 manifest에 묶인 승인된 자격검사 실패 출처. 경제평가 시작은 false여야 한다.
- 수신 archive와 측정된 result outcome이 없음. 불확실하거나 실행 중인 job은 종료할 수 없음.
- 새 근거의 실제 내용 SHA·미션 digest·trial·job·code commit 일치.
- 독립 총괄의 명시적인 결정과 원래 실패·새 근거 인용.

종료 결정은 별도 불변 `research_technical_dispositions`에 기록한다. 물리적인 실패 상태 `technical_waiting`과 원래 plan/job/hold를 유지하고 `technical_closed_at`만 기록한다. 공개 상태는 이를 `technical_closed`로 구분한다. `reported`, 경제적 손실·무개선, 감사 통과를 만들어내지 않는다. 과학 counters와 incumbent, 예산 예약·정산은 바꾸지 않는다.

미종료 pending trial의 고유 인덱스와 선정 조건은 이 검증된 종료를 제외한다. 종료 후보의 수리·재큐잉은 거절한다. 다음 가설은 기존 독립 반박·선정·구현·사전검사·경제평가·감사 절차를 거친다. 같은 실행의 중복 방지와 승인된 총/회차 예산은 유지한다. `wait`는 한 번의 검토 후 같은 근거로 자동 재시도하지 않는다.

## 호환성과 적용 상태

새 물리적 state enum을 추가하지 않는다. 기존 reader는 같은 행을 기술 실패로 읽고 보류하므로 이전 코드가 알 수 없는 상태를 보고 예외를 내지 않는다. additive column/table과 같은 이름의 pending 인덱스를 idempotent migration으로 준비한다.

구현 `8eca1d6`의 실제 PostgreSQL·Git·고정 qlab 전체 회귀는 1,853개 통과·14개 제외, 범위 회귀는 142개 통과했다. 직원·Slack·시장 응답은 합성이다. 저장소 lint도 통과했다.

실제 부모 API/worker의 source와 설치 패키지에 커밋 차분을 fuzz 없이 적용했다. 별도 설치 모델 정책 9줄과 나머지 Python/SQL digest를 모두 보존했다. 운영 묶음 `68a53f1c699837256db5e8a4a6479bbd0fc82876`의 비활성 이미지는 준비됐으며 실제 보류·원래 실패 내용·응답 schema를 검증했다. 정확한 manifest·이미지 적용 검토는 PR #105에 기록했다.

11:19 KST 적용을 시작해 기존 video 호출이 종료된 뒤 회사 turn/outbox와 연구 job을 drain하고 일관된 암호화 S3 백업을 검증했다. **11:34:31 KST** API/worker 교체·원래 설정/다른 서비스 보존·새 근거 연결·실제 이미지의 총괄 prompt 검증이 끝났다. 소유한 pause도 해제됐다. 실제 prompt projection의 task/turn/outbox 쓰기는 rollback했고 과학 결정을 주입하지 않았다.

정상 연구 controller가 pause 아래 생성한 실제 총괄 검토의 turn에는 잔여 배포 타이머가 있었다. 실제 Temporal 이력의 timer와 미수신 계정 revision을 확인·검토한 뒤 **11:39:25 KST** 현재 primary revision2로 기존 turn만 깨웠다. 계정 선택·request·turn ID·과학 권한을 바꾸지 않았다.

11:40 KST 실제 Astra max 요청과 명세/이력 read receipt를 확인했다. 원래 engineer attempt79와 보류는 보존됐다. 아직 총괄의 후보 종료 결정이나 후속 경제 실험을 완료했다고 주장하지 않는다. 실제 서비스 건강 상태와 3070 user-scope worker의 기존 release 동작도 확인했다.

24시간 상한·45초 주기의 읽기 전용 관찰기를 실행했다. 실제 검토 stage가 완료 timestamp보다 5초 먼저 pause 아래 생성되어, terminal 검사는 같은 operation commit의 검토도 포함하도록 V2를 시작했다. 이전 관찰기는 중지했다. 자동 경보·수리 기능은 없다.

근거: [실제 적용](evidence/etf-exploration-20260930/technical-review-runtime-applied-20261012.json), [타이머 깨움](evidence/etf-exploration-20260930/technical-review-existing-turn-wake-applied-20261012.json), [실제 직원 시작](evidence/etf-exploration-20260930/technical-review-actual-staff-started-20261012.json), [관찰기 V2](evidence/etf-exploration-20260930/technical-review-bounded-observer-v2-active-20261012.json).

근거: [회귀](evidence/etf-exploration-20260930/technical-review-regression-20261012.json), [실제 부모](evidence/etf-exploration-20260930/technical-review-runtime-baseline-20261012.json), [비활성 준비](evidence/etf-exploration-20260930/technical-review-runtime-prepared-20261012.json), [적용 검토](evidence/etf-exploration-20260930/technical-review-apply-review-20261012.json).

## 실제 총괄 결정과 후속 연구

**11:46:55 KST** 실제 총괄이 `retire_candidate`를 제출했다. 원래 실패와 새 계약 source를 모두 인용했고 필요한 보류·manifest 및 source를 이번 attempt에서 끝까지 읽은 영수증이 있다. 불변 disposition digest는 `c8f87cc9…`다. 실패 trial에는 `technical_closed_at`만 기록됐다. failed job·plan·원래 hold attempt79와 null retry·첫 result를 보존했고, 새 measured result·ordinal·feasible 값을 만들지 않았다. 누적/회차 과학 result 수는 1로 그대로다.

실제 research-center Slack 답변의 본문·스레드·bot author를 다시 읽어 대조했다: [실제 총괄 결정](https://achiisquantresearch.slack.com/archives/C0C2B9EUEGM/p1791773216343489?thread_ts=1789633942.673909&cid=C0C2B9EUEGM).

**11:46:59 KST** 정상 controller가 새 researcher proposal stage `378b5f19…`와 task `dd8b6874…`를 생성했다. 11:48 KST 실제 Astra high 완료 요청과 새 이력 read receipt 9개를 확인했다. 운영자가 가설이나 후속 후보를 선택하지 않았다. 새 가설 → 독립 반론 → 선정 → 구현 → 실제 3070 자격/경제평가 → 감사/보고가 기존 승인 범위와 예산 안에서 이어진다.

INTENT-v20의 운영 변경과 실제 독립 후속 진행 확인을 완료했다. 과학 프로그램 전체 완료·두 번째 경제평가·새 수익 수치·paper/live 사용 준비는 완료하지 않았다. 24시간 상한 관찰기 V2는 11:48 KST active/running이며, terminal 도달이나 시간 상한에서 끝난다. 자동 경보·수리는 없다. 새 signed Slack 수신→응답 round trip은 이번 적용 뒤 별도로 시험하지 않았다.

근거: [실제 결정·후속 요청](evidence/etf-exploration-20260930/technical-review-actual-disposition-successor-20261012.json), [Slack 본문 대조](evidence/etf-exploration-20260930/technical-review-initial-slack-readback-20261012.json), [완료 범위 요약](evidence/etf-exploration-20260930/technical-review-outcome-summary-20261012.json).
