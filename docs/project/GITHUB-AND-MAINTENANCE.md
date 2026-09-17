# 회사 저장소와 개선 담당

2026-09-16 사용자 선택으로 정본을 비공개
[JJongAchii/quant-ai-company](https://github.com/JJongAchii/quant-ai-company)로 분리했다.

| 저장소 | 책임 | 현재 GitHub 상태 |
|---|---|---|
| quant-ai-company | 회사 서비스·Slack·직원·상시 실행·개선 담당·CI | PR #1~#15 병합, 개선 BOT의 PR #3도 실제 Slack 승인 후 병합 |
| quant-workspace | 공통 연구 규약·스킬·qws 계약, 초기 회사 구축 기록 | PR #42는 원본 이관 기록으로 보존 |
| quant-data | 데이터 수집·조회 라이브러리 | 읽기 API PR #47, CI 통과 |

`git subtree split`의 추출 tree가 기존 서비스 tree와 동일함을 확인했다. 새 저장소의 설치·
Docker build context·문서·CI 경로를 독립 구조로 고쳤다. 과거 SHA/digest는 원본 저장소 기준으로
보존하며, 새 이관 기록에서 원본과 추출 커밋을 연결한다. 로컬 개발은 이 qws의
`quant-ai-company/` worktree를 사용한다.

개선 담당은 2026-09-17 사용자의 운영 활성화 요청에 따라 기존 AWS 서버에서 실행 중이다.
[PR #1](https://github.com/JJongAchii/quant-ai-company/pull/1)을 병합해 활성화했고,
실제 인수에서 발견한 출처 검증 결함을 [PR #2](https://github.com/JJongAchii/quant-ai-company/pull/2)로
고쳤다. 최초 활성화 커밋은 `74abe276dc7a4165a750222eb192adb7a32a7f36`이며 해당 커밋의 CI는
170 passed, 1 skipped, 1 deselected와 lint 통과다. 기본 CI는 live 구독 호출을 제외하며,
별도로 이 인수에서는 서버 구독 모델과 실제 GitHub App을 사용했다.

운영 정책은 사용자가 선택한 **수정·테스트·PR까지 자동, 운영 반영은 검토 후**다.
추가 GitHub 요금제를 구매하지 않았다. PR 알림이 전달된 스레드에서 소유자의 명시 승인을
기록한 뒤 해당 커밋을 검증·병합하고, 실행 코드 변경이면 호스트 배포 작업으로 이어간다.
승인 없는 자동 병합·배포는 하지 않는다. private branch protection은 계정에서
업그레이드를 요구하므로 현재 코드의 broker와 운영 절차를 GitHub 자체 강제 보호라고 부르지 않는다.

Slack·업무·DB·Codex 인증과 개선 담당은 서버에 있어 맥북 종료와 무관하다. 직접 전원을 꺼본 기록은
추가 관찰이며, 서비스의 실행 의존성이나 필수 구축 차단 조건이 아니다.

이관 당시 검증: [REPOSITORY-MAINTENANCE-VALIDATION.json](REPOSITORY-MAINTENANCE-VALIDATION.json).

## BOT 행동·조직 개선 확장 — 2026-09-17

사용자가 확인한 개선 범위에 맞춰 PR #1을 확장했다. 구현 당시 검증 HEAD는
`137495c5c6951971b991ab81cb3c6c25232c4d28`이다. 최근 30일의 업무·위임 기록에서 원인 가설을
만들고, 지침 변경은 수정 전에 고정한 문제/정상 요청을 같은 모델·문맥으로 전후 비교한다.
큰 구조 변경과 자동으로 평가할 수 없는 개선은 효과 미검증인 설계 문서 PR로 남긴다.

이 비교는 저장 요청 두 건의 응답 구조 검사이며 전체 품질·금융 전문성 검증이 아니다.
당시 정책은 하루 개선 모델 호출 6회였다. 아래 PR #12에서 이 제한을 제거했고 사용자 업무 우선·검토 후 운영 반영은 유지한다.

구현 당시 검증: [BEHAVIOR-MAINTENANCE-VALIDATION.json](BEHAVIOR-MAINTENANCE-VALIDATION.json).

## 운영 활성화와 첫 PR — 2026-09-17

- GitHub App `achii-quant-company-maintainer`는 `quant-ai-company` 저장소 하나에만 설치했다.
  Contents/PR write, Actions/metadata read다. 개인키는 서버의 개별 secret mount에 두며 모델에
  전달하지 않는다. 등록 중 쓴 Mac의 임시 PEM은 삭제했다.
- Temporal `company-maintenance-v1`과 maintenance worker가 상시 실행한다. 5분마다 작업을
  이어가고 새 관찰 수집 간격은 10분이다. 바뀐 기록이 없으면 모델을 부르지 않는다.
- 첫 자동 관찰은 근거가 부족하다고 판단해 수정을 제안하지 않았다. 이어 운영자가 등록한
  문서 상태 불일치 인수 요청에서 실제 Codex 응답·Git 브랜치·CI·draft PR·Slack 알림을 확인했다.
  이 요청은 Slack 사람이 직접 보낸 신규 입력 시험이 아니며, 인수 중에는 타이머 대기를
  단축하려고 운영자가 같은 tick 경로를 제한적으로 호출했다. 호출 한도와 우선순위는 유지했다.
- 첫 결과는 [PR #3](https://github.com/JJongAchii/quant-ai-company/pull/3)이다.
  당시 draft로 생성했고 아래 실제 승인 인수에서 병합했다.
  `docs/maintenance.md`의 활성화 상태 정정만 제안했다. 금융 전문성·전략 성과·일반적인 코드 수리
  능력을 이 문서 PR로 입증했다고 해석하지 않는다.
- 인수 중 유효 출처 인용을 과도하게 거절한 결함을 수정했다. 원래 응답을 보존하고 실제 근거와
  변경되지 않은 대상 파일을 대사해 같은 작업을 재개했다. 모델 재호출로 결과를 고르지 않았다.

운영 인수 상세와 남은 관찰 범위:
[MAINTENANCE-ACTIVATION-VALIDATION.json](MAINTENANCE-ACTIVATION-VALIDATION.json).

## Slack 승인과 실제 반영 — 2026-09-17

- 이 승인 인수 당시 운영 코드는 `92d66f56d50e4a6c56a756e442f6a4575c5dbe58`이었다. PR #4~#6에서
  승인 수신, 원래 후보·CI·변경 범위 대사, GitHub App 병합, 호스트 배포·복구 경로를 추가했다.
  해당 커밋의 CI는 195 passed, 1 skipped, 1 deselected와 lint 통과다.
- Slack의 실제 새 메시지 `1789606489.679629`("반영해")가 후보 `52b9afad6090…`에
  고정됐다. GitHub App이 PR #3을 `b0ce21ccbc22…`로 병합했고 완료 알림
  `1789607310.363989`를 원래 스레드에 전달했다. 승인·병합 경로는 추가 모델 호출이 없다.
  같은 이벤트 재전달에서도 신청·메시지·outbox·모델 호출 수가 늘지 않았다.
- 인수 중 GitHub PR의 오래된 base snapshot을 현재 main으로 간주하던 검증 오류를
  PR #6으로 고쳤다. 같은 승인과 같은 후보를 보존하여 대사·재개했다. 과거 메시지 수동 복구
  시도는 이미 생성된 새 Slack 승인과 중복으로 처리됐으며 별도 승인을 만들지 않았다.
- 실제 승인 사례는 문서 수정이라 서버 재배포 대상이 아니다. 실행 코드 후보의 승인 후
  배포·실패 복구는 명령 fixture로 검증했고, 운영에서는 호스트 timer와 GitHub archive
  조회·해제·보호 파일 검사를 확인했다. 운영 서버에 실패 코드를 일부러 배포한 시험은 없다.
- 기존 2GB 서버와 DB를 유지했다. 기존 백업 후 기능 자체를 운영자가 배포했으며, 모든
  7개 컨테이너가 실행 중이고 설정된 healthcheck는 정상이다. `quant-company-release.timer`가
  활성화되어 있다. 문서 PR #7 병합 후 저장소 main은 운영 코드보다 문서 변경만 앞선다.

상세: [MAINTENANCE-APPLICATION-VALIDATION.json](MAINTENANCE-APPLICATION-VALIDATION.json).

## 과거 요청 진단 스레드에서 드러난 연결 공백 — 2026-09-17

사용자가 확인을 요청한 [Slack 스레드](https://achiisquantresearch.slack.com/archives/C0C1Q8D0B6K/p1789607383493039)의
실제 Slack 메시지, DB 기록, 저장된 모델 요청을 대조했다. 직접 진단 요청을 총괄에서 개선
서비스로 보내고 상태를 돌려받는 도구가 당시에는 없었다. 총괄의 `knowledge_search`는 등록된 자료만
검색했으며 이전 Slack 요청을 검색하지 않았다. 당시 총괄 문맥에는 새 요청 한 건과 합성 인수 자료
하나만 있었고, 공유 기억·과거 스레드는 없었다.

자동 관찰기는 이 스레드를 별도로 읽고 `director_history_evidence_ignored` 후보를 만들었다.
그러나 "총괄에게 과거 이력이 이미 제공됐다"는 가정은 저장된 원 요청 문맥과 맞지 않는다.
수정안도 `prompt_replay_cannot_validate_runtime_code_changes`로 차단됐고 이 건의 PR이나
운영 반영은 없었다. 총괄이 이 상태를 사용자에게 조회·보고하는 연결도 빠져 있었다. 이것은
앞선 PR #3 승인·병합 성공으로 해결됐다고 볼 수 없는 별도 구현 공백이다.

이 확인에서 후속 구현 대상으로 소유자 범위의 실제 이력 조회, 진단 요청·상태·결과의 왕복,
각 직원이 실제로 본 문맥에 근거한 개선 후보 검증을 정했다. 당시에는 기록 조회만 수행했다. 근거: [maintenance-thread-diagnosis.json](evidence/maintenance-thread-diagnosis.json).


## 총괄과 개선 진단 연결 — 2026-09-17

해당 인수 당시 운영 커밋은 `ae7517c5fbe79db92c1c19396a60711ecac120e1`이다. 기존 2GB 서버에서 DB를 유지하고
백업 후 배포했다. 데이터 라이브러리 커밋·모델 배치·일일 호출 한도는 바꾸지 않았다.

- [PR #8](https://github.com/JJongAchii/quant-ai-company/pull/8)에서 같은 소유자의 허용된 채널·DM,
  최근 30일의 제한된 이력 조회, 현재 사람 요청에 묶인 진단 접수와 실제 상태 조회를 추가했다.
  같은 스레드에 접수·후보·검증·PR·대기·차단을 알리고, 관찰자가 본 이력과 직원이 실제로 받은
  문맥을 구별한다. 지침 변경에 실행 코드 테스트를 요구하던 불일치도 고쳤다.
- 첫 운영 인수에서 실제 worker에 Slack 허용 사용자·채널 설정이 전달되지 않아 요청이 차단됐다.
  [PR #9](https://github.com/JJongAchii/quant-ai-company/pull/9)에서 공개 허용 목록 전달을 고쳤다.
  토큰·권한을 늘리지 않았고, Compose의 실제 환경으로 두 소비자의 도구 실행을 검사했다.
  최종 CI는 205 passed, 1 skipped, 1 live deselected와 lint 통과였다.
- 실제 사용자가 이미 보낸 Slack 요청을 운영자 재처리라고 표시하여 같은 프로젝트에서 이어갔다.
  새 사람이 보낸 Slack 이벤트 시험은 아니다. 총괄의 실제 구독 모델이 진단 접수·상태 도구를
  사용했고, 접수와 호출 한도 대기 알림이 원래 스레드에 전달됐다.
- 개선 모델 호출은 기존 6회/일을 유지했다. 새 진단은 접수·분류 대기 상태이며 분석 완료가 아니다.
  DB 기준 다음 예산 초기화는 2026-09-18 00:00 UTC(한국 시간 09:00)다.
- 인수 중 운영자가 128MB maintenance 컨테이너에서 조회 검사를 동시에 실행해 메모리 상한에
  걸려 한 번 재시작됐다. 호스트 여유 메모리는 약 966MB였고 서비스는 자동 복구됐다. 검사는
  다른 소비자 및 순차 임시 컨테이너로 분리했다. 서버 크기·서비스 메모리 한도·비용은 바꾸지 않았다.
  원인과 실제 kernel 기록은 [검사 사고 기록](evidence/maintenance-review-probe-incident.json)에 남긴다.
- 접수 이후 총괄의 후속 응답 두 건은 `invalid_output`으로 차단됐다. 과거 실패 원문이 없어
  정확한 형식 원인은 미확인이며 실패를 성공으로 덮어쓰지 않는다. [PR #11](https://github.com/JJongAchii/quant-ai-company/pull/11)에서 안전한 원인
  기록과 JSON Schema 밖의 상태 조건 안내를 보완했다. 관련 런타임 검사 53개와 최종 CI의
  212 passed, 1 skipped, 1 live deselected 및 lint 통과를 확인했다. 실제 인수 결과는 아래 최종 영수증으로 구분한다.
- 보완 후 같은 업무의 총괄 후속 답변이 완료됐고 원래 Slack 스레드의 실제 전달을 확인했다.
  새 진단의 분석·PR은 아직 대기이며 접수 안내 업무 완료와 구분한다. 과거 잘못된 진단 가설이
  현재 검증된 사실이 된 것은 아니다. 실패 기록 두 건은 그대로 남겼다.
- 이력 도구는 운영자가 실제 worker에서 호출해 12건과 생략 표시를 확인했다. 이 별도 검사는
  모델 호출·Slack 발신 없이 진행했고 임시 source는 같은 transaction에서 제거했다. 총괄 모델이
  해당 이력 검사를 직접 수행했다고 주장하지 않는다.

실행·실패·재개·검증 경계: [MAINTENANCE-REVIEW-VALIDATION.json](MAINTENANCE-REVIEW-VALIDATION.json).

## 후속 요청 — 시장 브리핑·분석

사용자 요청으로 [PR #10](https://github.com/JJongAchii/quant-ai-company/pull/10)에 아침 정기 시황 브리핑과
요청형 시장 분석을 로드맵으로 추가했다. 국내·글로벌·가상자산, 세계 경제, 주요 뉴스와 출처·기준 시각을
다루도록 기록했다. 두 기능의 구현·활성화·발송 예약은 추후 진행한다. 상세: [다음 단계](NEXT-STEPS.md).

## 현재 구현 근거와 일일 한도 해제 — 2026-09-17

사용자 후속 지시로 회사 공통 100회/일과 개선BOT 전용 6회/일을 모두 제거했다.
PR #12~#15를 병합해 `85f8620493103b5d9e188af5a775598dcc31d311`를 기존 2GB 서버에 배포했다.
추가 서버·유료 API를 활성화하지 않았고 구독 한도·직렬 실행·업무별 반복 제한은 유지한다.

총괄과 개선 담당은 같은 GitHub 커밋·코드·PR/CI, 실행 코드·공개 설정, 현재 작업 상태,
반영·검증·가설 정정을 읽는다. 소스 읽기와 수정 권한을 분리했고, 변경된 근거에는 새 진단
revision을 사용해 원 입력·응답을 보존한다. “당시 제공된 이력을 무시했다”는 가설은 실제
원 입력과 대조해 기각했다. 모델이 명시한 출처만 구조화하며 없는 근거는 만들지 않는다.

실제 인수에서 발견한 입력 크기 초과와 오래된 작업 상태/인용 전달 문제까지 수정했다.
완료된 작업에 이전 대기 사유가 남는 문제도 PR #15에서 고쳤고 과거 기록은 보존했다.
실제 총괄 답변·기존 개선 진단·Slack 결과 전달을 확인했다. 운영자 재처리를 신규 사람 Slack
메시지로 주장하지 않으며, 유지보수 제안의 운영 반영에는 계속 사람 검토가 필요하다.
검증 범위·남은 기능·실패 이력: [CURRENT-SYSTEM-VALIDATION.json](CURRENT-SYSTEM-VALIDATION.json).
