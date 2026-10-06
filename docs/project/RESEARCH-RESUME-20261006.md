# Slack·조건부 연구 복구 기록

## 운영 확인

2026-10-06 KST, 이미 검증·배포된 회사 소스 `231d6ba0755f658f167636a8ea2c3ef65b6af562`의
중지된 기존 컨테이너를 재개했다. 새 컨테이너나 연구 입력을 만들지 않았고, 이미지·모델·계정·역할·프로필
설정의 동일성을 확인했다. API는 healthy, Socket 수신부는 활성 직원 7명의 HTTPS 연결을 확인했다.
3070도 동일한 회사 소스를 사용한다. 실제 Slack 승인 처리와 메시지 수정의 전달·본문·버튼 제거까지 확인했다.

최초 복구 스크립트는 WARNING 로그 설정에서 출력되지 않는 INFO 연결 로그를 기다려 시간 초과했다.
이 최초 상태를 보존하고, 실제 연결과 Slack 영수증으로
[복구 영수증을 대사](evidence/etf-exploration-20260930/resume-reconciled-20261006.json)했다.

## 유효한 연구 승인

기존 엄격한 프로그램 `e06537d3…`은 실제 소유자의 서명된 Slack 취소로 `cancelled`이다.
08:13 KST에 실제 소유자가 승인한 유효 프로그램은 아래 명세다.

- 프로그램: `f7deaf96-e677-5afe-93d4-18ac387043bb`.
- 전체 digest: `c3ba5268d71c87bd2c6226160bc720498137bd291396a054970a1f1e7ebcf2db`.
- 기존 12개 제안 이력 포함, 최대 4회 실험·7,200초·1개 과제·동시 1.
- `kr-etf-retrospective-v1`, 고정 빈티지에 대한 조건부 개발 연구.
- 당시 공개·수정 시점은 미확인이다. 역사적 시점성·실제 체결·확증·배치 승인을 주장하지 않는다.
- [전체 승인 명세와 실제 전달 식별자](evidence/etf-exploration-20260930/approved-canonical-program-20261006.json).
- [실제 Slack 승인 안내](https://achiisquantresearch.slack.com/archives/C0C2B9EUEGM/p1791241501260409?thread_ts=1789633942.673909&cid=C0C2B9EUEGM).

이 작업에서 올렸던 24회 예산의 추가 초안 `a9854436… /04cee0f9…`은 승인되지 않았다.
실제 승인된 명세와 대조한 후, 그 초안의 Slack 안내를 기존 durable update outbox로 수정하고 버튼을 제거했다.
프로그램·승인·최초 전달 영수증은 바꾸지 않았다.
[실제 수정 본문](evidence/etf-exploration-20260930/owner-review-correction-readback-20261006.json)을 보존했다.

## 독립 데이터 검토의 형식 오류

연구자는 원문과 입력 증거를 읽고 **‘분산예측 수준 보정이 ETF의 스트레스 비용 후 절대수익을 개선하는가’**를
제안했다. 초기 독립 데이터 검토에서 `gpt-5.6-terra /high`의 `AgentDecision` 응답 검증 오류가 네 차례 발생했다.
정상 파일 읽기도 확인됐으나 각 새 시도의 읽기는 해당 시도의 증거로만 인정된다.
이는 과학 결과나 데이터 적합성 판정이 아니다. 연구 예약·과학 시행·실제 계산시간은 이 관측에서 0이다.

별도의 입력에 묶인 subscription 진단 호출 1회는 정상 응답이었다. 그 제안 효과는 적용하지 않았다.
원본 실패는 `decision_contract:invalid_shape`로만 기록되어 있다. 일시적인 정상 호출을 형식 오류 해결로
표시하지 않는다. 데이터 검토 전용 구조화 응답을 준비하고 검증한 PR과 운영 적용 명세를 제공한다.
기존 프로그램의 과학 범위와 예산은 이 기술 수리로 바뀌지 않는다.

## 백업 실패 관측

10월 3~5일 정기 백업의 연구 증거 복사에서 `No space left on device`가 발생했다.
일부 실패에는 서비스 `start`도 실패한 기록이 있다. 현재 root filesystem은 약 82GB,
가용 공간은 약 35GB이며 inode 사용은 9%다.
[실제 공간·오류 단계 기록](evidence/etf-exploration-20260930/backup-failure-details-20261006.json)을 보존했다.
이 복구에서 백업 정책을 변경하거나 파일·이미지를 삭제하지 않았다. 이후 다른 운영 작업에서 만든
성공 백업의 실제 영수증은 아래 후속 운영 확인에 별도로 기록했다.

## 역사적 배포의 구분

PR99의 `3c848af9…` 탐색 정책 적용과 PR101의 `79586479…` Socket 취소 수신부 적용은 완료된 역사다.
현재 운영 소스 231은 이 기능을 포함하는 후속 버전이다. 이전 parser로 되돌리지 않는다.
`7ac51efe…` 호환성 후보는 현재 운영보다 오래됐으며 배포하지 않았다. 그 후보의 로컬 전체 검사는
잘못된 PostgreSQL 역할로 인해 setup 실패했고, 합격 gate로 사용하지 않는다.

이번 운영 관측은 실제 PostgreSQL·Slack·현재 컨테이너와 3070의 읽기 확인이다.
이전 signed simulated Slack·toy worker 검증과 실제 운영 관측을 구분한다. 새 성과 수치는 없다.

## 후속 운영 확인 — 2026-10-06 10:44 KST

다른 운영 작업이 회사 소스 `5c44ad07273adc780a56a47d7d35114fd0dc32c8`을 서버의 여섯 서비스에
적용했다. 실제 import 경로와 소스 파일 205개의 바이트 대조를 보존한
[운영 배포 영수증](evidence/etf-exploration-20260930/canonical-cutover-public-receipt-20261006.json)의
상태는 `active_data_review_resumed`이다. 이 root는 중복 후보 `5f84e57` 또는 `bb1e42e`을 서버에 배포하지 않았다.
현재 API·모델 gateway·Codex runtime은 healthy이며 활성 서비스 15개와 직원 7명의 Socket TLS 연결을 확인했다.
권한·역할·모델·계정·공개 연구 프로필과 입력은 승인된 설정을 유지한다.

3070의 설정과 실행 unit은 당시 이전 소스 231을 사용했다. 새 소스5c에 이미 등록된 release가 있어
root가 준비한 별도 경로를 최초로 선택하려는 작업은 `ReleaseError`로 거절됐다.
[최초 실패](evidence/etf-exploration-20260930/canonical-worker-alignment-first-failure-20261006.json)를 보존하고
이전 poller를 정상 재개했다. 등록된 release의 코드 파일과 qualification 영수증을 대조한 뒤,
이동된 입력 경로의 두 파일이 승인된 SHA-256과 동일함을 검증하여 그 release를 선택했다.
[완료 영수증](evidence/etf-exploration-20260930/canonical-worker-aligned-20261006.json)의 상태는
`active_worker_aligned`이다. 실제 unit의 `WorkingDirectory`·`PYTHONPATH`도 소스5c 경로이고 poller는 active다.
`KillMode=process`와 이전 registry 항목을 유지했으며 분리된 실행 프로세스를 종료하지 않았다.

실제 독립 data 직원은 새 native `research_stage_v1` 응답으로 필수 파일을 읽었다.
시도5의 최종 응답은 version2에 이전 버전의 최상위 `data_policy_digest`·`evaluation_prices`를 섞어
기존 검증기가 거절했다. [그 관측](evidence/etf-exploration-20260930/canonical-data-mixed-version-rejected-20261006.json)을
보존했으며 응답을 수정하거나 readiness를 대신 결정하지 않았다. 시도6에서는 필수 원문·패킷·계보 읽기를
완료하고 직원이 만든 올바른 version2 판정이 적용됐다.

[현재 프로그램 관측](evidence/etf-exploration-20260930/canonical-program-selection-20261006.json)은
데이터 판정 `conditional_ready`, 과제 상태 `assessed`, 총괄의 `program_selection` 실행을 확인한다.
이 판정은 당시 공개·수정 이력과 실제 체결을 입증하지 않는 승인된 조건부 개발 범위다.
과제 선정 결정·mission·예약·과학 시행·계산시간은 이 관측에서 아직 없다. 추가 과학 승인은 필요하지 않다.

10:49 KST [후속 관측](evidence/etf-exploration-20260930/canonical-program-revision-requested-20261006.json)에서는
총괄의 선정 단계가 정상 완료되고 `revise` 결정이 기록됐다. 기존 같은 문헌·종목·개발기간·월별 수준 보정
제안과의 차이 및 현재 평가 엔진의 정보 경계·보유수익·비용 처리 연결을 명시하라는 요구다.
원래 과제는 `rejected`로 이력을 유지하고 새 `program_proposal`이 실행 중이다.
이는 연구 내용의 수정 결정이며 `stage_response_rejected` 같은 수신 오류가 아니다.
이후 시행을 임의로 시작하거나 제안·검토 판단을 operator가 바꾸지 않았다. 예약·시행·계산시간은 여전히 0이다.
10:51 KST 실제 Slack 읽기에서는 data의 검토 알림과 director의 선정 알림 모두 동일 스레드·해당 bot 작성자로
수신됐고 outbox는 각각 첫 시도에 delivered다. 전송부가 붙이는 `[지시 v5]` 접두사는 raw outbox 본문과
구별한다. [최초 raw 본문 관측](evidence/etf-exploration-20260930/canonical-progress-slack-readback-20261006.json)과
[실제 전송 형식 대조](evidence/etf-exploration-20260930/canonical-progress-slack-rendered-readback-20261006.json)를
구분해 보존했다.

정기 백업 정책 수리는 이 root의 범위에 포함하지 않았다. 다른 운영 작업의 성공 영수증에는
`company-20261006T005804Z-16dc9f8d.tar.gz`, SHA-256
`8331d198bba7996201ce79614138eea862bb86e167d9454ec4994d6dd4c12212`, 동일한 S3 대상과
`secrets_included=false`가 기록됐다. 기존 ENOSPC 관측은 그대로 남긴다.

## 형식 재발 방지와 검사 구분

저장소의 후속 소스 `86aea8cdf03b5543666813c97689acb1e8158a6d`은 새 data 요청에 해당 과제가 허용한
DataAssessment 버전만 노출한다. version2의 최상위 legacy 필드는 제외되며 기존 business validator와
독립 증거·과제 선정 gate는 유지된다. root는 동일 문제의 별도 수정 대신 이 변경을 통합했다.
이 문서는 후속 소스86의 신규 운영 적용을 주장하지 않는다. 현재 데이터 검토 완료는 운영 소스5c에서 일어났다.

- 통합 소스 `bb1e42e`의 회사 소스는 운영5c와 동일하다. 전체 실제 임시 PostgreSQL 및 고정 qlab 검사:
  **1,769 passed /14 skipped**. [JUnit](evidence/etf-exploration-20260930/data-response-integrated-full-tests-20261006.xml).
- 실제 3070의 소스5c 합성 ETF producer→consumer와 Linux sandbox qualification은 통과했다.
  [영수증](evidence/etf-exploration-20260930/canonical-worker-qualified-20261006.json)의 과학 시행·실제 시장 입력 읽기는 0이다.
- 별도 실제 ChatGPT subscription 진단 두 건은 첫 호출 `events:invalid_shape` 실패, 긴 입력 호출은 정상 읽기
  제안이었다. [두 영수증](evidence/etf-exploration-20260930/canonical-native-qualification-20261006.json)을 함께 보존했다.
  이 진단 제안은 적용하지 않았으며 성공 1건을 반복 실패 해결이나 연구 완료로 표시하지 않는다.
- 실제 운영의 시도6 완료와 `conditional_ready`는 위 진단·모의 CLI 검사와 별개의 PostgreSQL 관측이다.
  새 성과 수치는 없다.
- 소스86과 회사 코드가 동일한 통합 `c2a5fe1385d634db95207e230d2f0090f4386d74`에서,
  추가 회귀 검사를 포함한 **1,775 passed /14 skipped**, Ruff 통과를 확인했다.
  실제 임시 PostgreSQL·Temporal 및 고정 qlab을 사용했다. 모의 provider/Slack과 실제 subscription·Slack 관측을
  구별한다. 전체 검사에서 opt-in 실제 모델 호출과 Linux 워커 검사는 제외됐다.
  [최종 JUnit](evidence/etf-exploration-20260930/data-response-scoped-integrated-full-tests-20261006.xml).
  신규 scoped 판정에 legacy 필드가 섞이면 계속 거절되며, 올바른 blocked 판정은 기록되고 mission·job은 생성되지
  않는 사례 및 이전 요청이 그대로 동결되는 사례를 검증했다.

[최종 검사 영수증](evidence/etf-exploration-20260930/restoration-final-validation-20261006.json)과
[현재 운영·후속 검토 명세](evidence/etf-exploration-20260930/restoration-and-followup-manifest-20261006.json)에
현재 소스5c, 후속 소스86, 승인된 입력·예산 및 적용한 운영 효과를 구별해 고정했다.
이 root의 PR은 후속 소스86과 비교해 추가 테스트·문서·증거만 포함하며 새로운 서버 cutover를 요구하지 않는다.

## 최종 정리

[PR #105](https://github.com/JJongAchii/quant-ai-company/pull/105)에 이 root의 추가 회귀 검사와 복구 증거를
게시했다. upstream `ccf7e11`의 운영 기록도 통합했으며 회사 코드·배포 파일·의존성은 현재 base와 동일하다.
10:58 KST [마지막 프로그램 관측](evidence/etf-exploration-20260930/canonical-final-program-progress-20261006.json)은
원래 승인이 active이고 새 연구자 제안 단계가 running이며 오류가 없음을 확인한다.
과학 시행·예약·mission·계산시간은 여전히 0이다. 서버 current는 소스5c를 유지한다.
복구·검사·증거 통합은 완료됐으며 연구 후보 수정과 이후 독립 검토는 서비스가 기존 승인 안에서 계속 수행한다.

## 실제 실행 추적 — 2026-10-06 12:10 KST

후속 후보 ‘목표비중 10%p 갱신 유예’의 data 직원은 세 번의 응답 형식 오류로 자동 재시도가 보류됐다.
과제는 proposed이며 readiness·선정·mission·3070 작업은 없다. 앞 절의 완료는 이전 복구·검사 범위이며
현재 사용자가 요구한 실제 과학 시행은 0이다. [보류 관측](evidence/etf-exploration-20260930/scoped-data-program-held-20261006.json).

소스86의 버전별 응답 스키마 수정은 운영5c 대비 src 두 파일, 24행 추가·1행 삭제다.
[정확한 이미지 준비](evidence/etf-exploration-20260930/scoped-data-server-qualified-20261006.json),
[3070 합성 입력 qualification](evidence/etf-exploration-20260930/scoped-data-worker-qualified-20261006.json),
[실제 subscription의 scoped 최종 응답 검증](evidence/etf-exploration-20260930/scoped-data-native-qualified-20261006.json)이 완료됐다.
진단 응답은 프로그램에 적용하지 않았다. 실제 입력 연구나 readiness 결정은 이 검증으로 대체하지 않는다.

root는 [준비된 운영 명세](evidence/etf-exploration-20260930/scoped-data-review-20261006.json)와
[적용 순서·권한·실패 처리](evidence/etf-exploration-20260930/scoped-data-apply-review-20261006.json)를 검토했다.
기존 소유자의 배포·진행 요청에 따라 이 호환 형식 수리를 적용한다. 신규 exact-SHA 소유자 승인을 받았다고
기록하지 않는다. signed c3 프로그램의 예산·범위·입력·직원 판단은 유지한다.
현재 이 명세 작성 시점에는 운영 current가5c이며, 이후 활성화 영수증을 별도로 기록한다.

### 적용 완료와 실제 검토 재개 — 13:01 KST

[활성화 영수증](evidence/etf-exploration-20260930/scoped-data-cutover-active-20261006.json)은 source86 서버·3070 정렬 완료를 기록한다.
[여섯 서비스의 실제205파일 대조](evidence/etf-exploration-20260930/scoped-data-six-services-active-20261006.json),
[3070 정렬](evidence/etf-exploration-20260930/scoped-data-worker-active-20261006.json),
새 백업 SHA2763660bed9afe718ef737580a687f02f74fd3c41242b12e1abd9a9dba0442b9를 보존한다.
최초 backup lock 경합은 변경 전에 끝났고 journal/current로 대사했다. 후속 worker 재개 잠금 경합도
기존 phase가 유지됐음을 확인했다. 기술 보류 대사의 첫 호출은 secret-reference entrypoint 없이 실행돼
DB 연결 전에 실패했다. 정상 entrypoint로 재개한 [durable event667](evidence/etf-exploration-20260930/scoped-data-hold-reconciled-20261006.json)은
원본 응답·attempts·reads·승인·예산을 보존하고 형식 오류 보류만 해제한다.

[실제 재개 관측](evidence/etf-exploration-20260930/scoped-data-program-resumed-20261006.json)은 새 program_data stage
`e68ac24e-2f55-5812-bf45-cdedd1a985b4`의 정상 attempt1과 승인 프로그램 active를 확인한다.
기존 stage0b9b를 덮어 과거 읽기를 새 증거로 재사용하지 않았다. 이 시점 readiness·mission·job·과학 시행은 아직0이다.
현재 root의 실행 추적 목표는 진행 중이다.

### 데이터 검토 완료와 준비 대기 해소 — 13:33 KST

[source86 실제 검토 완료](evidence/etf-exploration-20260930/scoped-data-completed-first-attempt-20261006.json)는
새 data stage의 첫 시도에서 전체 필수 증거와 원문을 읽고 scoped conditional_ready를 정상 기록했음을 확인한다.
이후 director의 선정 응답은 원문 마지막 부분을 남겨 source completion gate가 보완 읽기를 요청했다.
보완 turn55aa는 workflow가 시작됐지만 모델 요청은 생성되지 않았고,
[실제 Temporal history](evidence/etf-exploration-20260930/research-preparation-starvation-20261006.json)에
higher_priority_request/2초 defer가 반복됐다.

최신 소유자의 실제 실험 진행 요구 안에서 root가 현재 준비 task af28의 queue priority만100→0으로
[검토](evidence/etf-exploration-20260930/research-preparation-priority-review-20261006.json)하고
[event680 적용](evidence/etf-exploration-20260930/research-preparation-priority-applied-20261006.json)했다.
signed 프로그램의 resources.priority=autonomous, 과학 예산·입력·자료 판정·선정 gate는 변경하지 않았다.
root가 새 모델 호출이나 과학 job을 enqueue하지 않았다. 기존 turn55aa는13:33에 실제 마지막 원문 읽기를
완료했고 최종 선정 호출이 정상 실행 중이다. mission·job·과학 시행은 아직0이다.

별도 브리핑 배포0ff6657이 이 구간에 전역 current를 변경했다. 이 소스는86을 포함하고
research/company/contracts/runtime 코드는86과 동일하며 config 추가3개는 briefing용이다.
13:15 관측의 dispatch는0ff, 연구 API/worker/socket/gateway/runtime은86이었다.
앞서 여섯 서비스 전체86 바이트 대조는13:03 당시 사실로 보존한다.
독립 브리핑 배포를 되돌리지 않았으며 이 root의3070 source86 정렬은 유지한다.

## 14:14 KST — 실제 미션 선정과 스케줄러 보류 충돌

직원은 13:35 KST에 목표비중 갱신 유예 과제를 accept했고, 원래 signed programme
`c3ba5268…`의 승인 출처를 가진 active 미션 `2ce40574-6368-5cef-b706-b4e67441b3de`을 만들었다.
14:11 관측까지 실제 시행·예약·job은 모두 0이다. 이전 owner 미션
`4462aff3-c7fe-5e66-a003-4aca2e8f3330`은 audit/waiting, `_audit_hold`, 2100년 재시도 상태다.
전역 스케줄러가 이 미션을 매번 선택해 새 미션을 선택하지 못했다. 이전 감사 보류는 유지한다.

`05c8030f05d95696a0e30a49767e5ecaf849db11`은 실제 운영의 동시 브리핑 release `0ff6657…`를
통합한 뒤 held audit만 candidate 선택에서 제외한다. 실제 PostgreSQL 회귀 검사는 보류 행의
불변성, 새 승인 background 미션의 proposal 생성, 감사 게시·시행 미발생, 보류 조정 후
owner 우선권 복귀를 검증했다. 전체 lint와 이 검사는 통과했고 전체 회사 검사는 진행 중이다.
정확한 새 코드의 실제 3070 합성 ETF producer→consumer 및 Linux sandbox 검사는 통과했다.
합성 검사값은 과학 결과가 아니며 실제 입력을 읽거나 과학 예산을 소비하지 않았다.

검토·준비는 PR105에 보존하고, 기존 배포·계속 진행 승인 범위에서 좁은 scheduling 수리를
준비한다. 새 exact SHA에 대한 별도 사람 승인을 받았다고 기록하지 않는다. 새 후보의
활성화 전에는 전체 회사 검사, 정확한 서버 이미지, 3070 정합성과 일관된 최신 백업을 확인한다.

### 기존 대기 응답 호환성 보존

초기 `05c8030` 전체 검사에서 기존 감사 보류 대기 응답 5건이 idle로 바뀐 것이 확인됐다.
이 후보는 활성화하지 않았으며 실패 JUnit·inactive 서버/3070 준비 영수증을 보존한다.
`defd4833adea6a0191412121a5a5c6e8265c58ba`은 held audit를 삭제·제외하지 않고
실행 가능한 미션 뒤로 정렬한다. held 미션만 남았을 때 기존 waiting 응답을 유지한다.
보류·감사 예산·비통과 결과를 그대로 보존하면서 정상 미션의 진행을 검증하는 관련
실제 PostgreSQL·백엔드 검사 29건이 통과했다. 이 최종 소스의 전체 검사는 별도로 실행한다.
첫 실패 검사 도중 후속 수정 파일이 편집된 사실도 기록하며, 초기 검사를 최종 소스의
정확한 자격검증으로 주장하지 않는다.

### 14:31 KST 동시 운영 배포 확인과 통합

최종 def 소스는 전체 1,825 pass/14 skip, lint, actual3070/server 준비를 통과했다.
기본 review 준비가 현재 포인터를 대조해 신규 운영 `e1e74d3…`를 확인하고 전환 전에 멈췄다.
cutover journal은 없으며 앱·워커는 바뀌지 않았다. 새 변경은 briefing/data.py의
격리 reader source pin 4줄과 검사 1건이며 연구·실행·의존성 계약은 동일하다.
실제 최신 source를 `ec1fbeb29a49420a367f06af596283e6ead4ab72`로 통합하고 전체 검사를
다시 수행한다. def의 성공 영수증도 보존한다. source 정합성 검증 중 변동을 막기 위해
기존 release.timer만 잠시 정지했다(release.service inactive/MainPID0).
**활성화 또는 작업 중단 뒤 이 타이머를 원래대로 복구해야 한다.**

## 14:52 KST — scheduler 실제 활성화와 새 미션 진행

새 consistent backup `company-20261006T054647Z-06130862.tar.gz`,
SHA `e494ef1d1d99d467c17e2c50f984f3629c095e8eaf090279eb95cf5be721db11`와
S3 대조/secret 제외를 확인한 뒤 ec 서버·3070을 정렬하고 회사 worker를 재개했다.
14:53에6서비스205source파일 바이트, core healthy,15running, Socket443연결7개를
대조했다. release.timer와 backup.timer는 active로 복구했다. 기존 모델/역할/프로필과
3070 prior registry를 보존했고 분리 실행을 종료하지 않았다.

새 mission2ce는 실제 스케줄러의 candidate가 됐고 proposal c305/attempt1이 생성됐다.
현재 모델 요청이 priority100으로 higher_priority_request/2초를 반복하는 것을 실제
Temporal history로 확인했다. root가 준비한 PR의 특정 현재 task d7df만100→0으로
대사했다(event686). signed resources.priority, 과학 예산·의미/자료 gate는 그대로다.
15:00 이후 실제 native calls가 완료되며 원래 lineage를 순차 읽는다.
아직 actual scientific job/시행/예약은0이다. 첫 실제 실험까지 계속 추적한다.

### 15:22 KST — 준비 절차의 실제 소요 확인

[준비 메타데이터](evidence/etf-exploration-20260930/first-mission-preparation-read-progress-20261006.json)에서
proposal은 attempt1/running이고 오류나 재시도는 없다. 35개 모델 turn이 완료됐고 1개가 실행 중이다.
필수 scientific-lineage 파일 12개 조각, 명세, 평가 엔진, 후보/통계 코드와 설정은 읽기를 완료했다.
전체 history와 큰 근거 source는 아직 부분 읽기 상태다. 파일마다 다음 offset이 증가하며 같은 완료
파일을 반복 읽는 정황은 없다. 한 조각마다 새 모델 turn을 거치는 준비 절차가 실제 소요를 늘린다.

[event686 읽기 영수증](evidence/etf-exploration-20260930/first-mission-preparation-priority-applied-20261006.json)을
별도로 보존했다. 이번 확인에는 운영 변경이 없다. programme의 실제 science job·trial·예약은 모두0이고,
실제 계산 시간도0초다. 이후 challenge·selection·implementation을 정상 경로로 따라 실제3070 실행을
확인하기 전까지 현재 INTENT는 완료가 아니다.
