# Slack 직원 무응답 조사 — 2026-09-28

실제 Slack, 운영 PostgreSQL, Temporal Cloud와 고정 연구 worker를 읽기 전용으로 대조했다.
[기계 판독 증거](evidence/slack-reply-incident-20260928.json)에 시각과 범위를 남겼다.
[운영 적용 영수증](evidence/slack-reply-cutover-20260928.json)에 실제 교체 결과를 따로 기록했다.

## 확인된 원인

- 금융전략 DM의 14:30 KST 사용자 메시지 두 건은 `inbound`와 Temporal workflow까지 생성됐다.
  응답 작업은 `queued`, 실행 횟수 0이다. 연구 커밋에 고정된 worker가 새 발송 전용
  `quant_scout` 역할의 specialist pack을 찾다가 `Unknown specialist`로 반복 실패한다.
- `research-center`의 진행 질문 세 건은 DB에 접수되지 않았다. 해당 프로젝트에서
  업무 한도에 계산되는 작업이 41건으로 기본 한도 40건을 넘었다. 질문 표현이 즉시 상태
  명령으로 분류되지 않아 앞의 두 건은 정책 거절 로그와 시각이 일치한다. 세 번째의 Socket
  전달 여부는 확인되지 않았다.

## 준비한 수리

- 현재 운영 앱 `0e09b85` 기반 코드 후보 `ae132c3`은 자연스러운 진행 질문을 모델 없이 처리하고,
  일반 질문이 한도에 걸리면 접수 영수증과 한도 안내 답변을 한 번만 남긴다.
- 고정 연구 worker `31ff903` 기반 후보 `e53b771`은 발송 전용 `quant_scout`를 직원
  모델 문맥에서 제외하는 한 줄의 코드 변경이다. 현재 main에도 이 제외가 있으나 운영
  worker는 연구 호환성을 위해 구버전으로 고정돼 있다. 활성 연구 중에는 이미지와
  `COMPANY_CODE_COMMIT`을 유지하고 [worker 전용 역할 설정 overlay](../../deploy/pinned-worker-roles-compat.compose.yaml)로
  비활성 발송 역할만 제외하는 임시 경로를 준비했다. Compose 병합 검사는 통과했다.
  실제 고정 커밋 소스에서도 필터링한 12개 역할을 읽고 활성 직원 4명의 모델 문맥을
  오류 없이 구성하는 것을 로컬에서 확인했다.
- 합성 Slack 입력을 실제 임시 PostgreSQL에 연결한 앱 회귀 검사 16개와 고정 worker
  검사 1개가 통과했다. 최신 앱 기반 전체 검사는 1,223개 통과·38개 건너뜀·실패 0개였고
  Ruff도 통과했다. 실제 직원 답변이나 새 Slack 왕복은 아직 검증하지 않았다.

## 운영 적용 조건

현재 연구 임무는 활성 상태다. 마지막 읽기 전용 관측 때 실행 중인 연구 job과 일반 turn은
없었지만, 이는 재시작 시점의 안전을 보증하지 않는다. [연구 배포·복구 절차](../runbooks/autonomous-research.md)에
따라 실행 중 외부 효과와 release pin을 다시 대사하고, 진행 중인 다른 배포와 조율한 뒤
앱 Socket을 새 코드로 교체한다. 고정 worker는 기존 이미지를 유지한 채 역할 설정만 분리해
재시작한다. 새 역할 파일은 운영 `roles.json`에서 비활성 `quant_scout` 한 항목만 제외하며,
다른 모든 역할이 같은지 확인하고 원본과 새 파일의 digest를 영수증에 기록한다.
기존 이미지·설정·영수증을 보존한다. 고정 worker를 다시 생성하는 후속 Compose 명령에도
같은 worker 전용 overlay를 포함해야 한다. 고정 이미지가 수리된 코드로 바뀔 때
임시 overlay를 제거한다.

[검토용 초안 PR #83](https://github.com/JJongAchii/quant-ai-company/pull/83)을 열었다.
운영 반영 전 PR 검토와 cutover 승인이 남아 있다.

운영 적용 후에는 DM의 기존 두 Temporal turn이 완료되고 실제 Slack 답변 영수증이 생기는지
확인한다. 거절된 `research-center` 메시지는 자동 재생하지 않는다. 사용자가 새 메시지로
상태를 물었을 때 한 번의 답변이 게시되는지 확인한다. 기존 코드에서도 정확히 `상태`라고
보내면 모델 없이 상태 요약을 받을 수 있다.

## 2026-09-28 운영 적용 현황

소유자 승인 후 07:24 UTC에 API·Slack 수신부를 `d571734` 이미지로 교체하고, 고정 연구
worker는 `31ff903` 이미지 그대로 역할 파일만 분리해 재시작했다. 다른 컨테이너 ID와
연구 커밋은 유지됐고, 적용 직전 활성 연구 job·running turn·발송 중 outbox는 모두 0건이었다.
현재 API는 healthy이고 두 DM turn은 worker에서 모델 계정 게이트까지 진행했다. 선택된 예비
ChatGPT 계정이 quota 상태로 07:34 UTC까지 대기 중이어서 실제 Slack 답변은 아직 없다.
계정은 자동 전환하지 않는다. `research-center`의 기존 거절 메시지도 다시 실행하지 않았다.
공통 release 링크와 runtime.env는 기존 `c54667e`를 가리키므로, 후속 Compose 작업은
실제 컨테이너 이미지와 worker 전용 overlay를 확인해야 한다.

이후 소유자가 Slack의 정확한 계정 명령으로 기본 계정을 선택했다. 정책 revision 2에서
실제 모델 성공 기록이 생겼고, DM 두 turn이 모두 완료됐다. 발송 영수증의 두 타임스탬프는
원래 DM 스레드의 금융전략 봇 메시지 두 건과 Slack API로 대조했다. `research-center`의
상태 경로도 소유자의 새 메시지 두 건과 director 봇의 발송 영수증 두 건을 실제 Slack
스레드에서 대조했다. 기존에 거절된 세 메시지는 자동 재실행하지 않았다.

## 후속 요청: 프로젝트 업무 40건 한도

소유자가 같은 스레드의 일반 업무 40건 제한 제거를 요청했다. 이 값은 프로젝트 단위
모델 업무 생성 수를 제한하는 `company_max_project_tasks`의 기본값이며 Slack 수신,
직원 위임, 새 지시를 처리하는 경로에 적용됐다. 코드 후보는 기본값 0을 무제한으로
해석하고 세 경로 모두에서 불필요한 개수 조회와 거절을 생략한다. 한도를 명시적으로
양수로 설정한 환경은 기존 동작을 유지한다. 업무별 turn 한도와 위임 깊이는 그대로다.
실제 임시 PostgreSQL에서 40건 초과 Slack 접수·직원 위임·새 지시 검사가 통과했고,
전체 1,226개 통과·38개 건너뜀·실패 0개, Ruff 통과를 확인했다. 준비 당시에는 운영에
적용하지 않았다. 연구 고정 worker의 코드 기본값은 40이므로 앱만 교체하면 직원
위임에서 다시 거절될 수 있다. [후속 적용 준비 영수증](evidence/project-task-quota-preparation-20260928.json)에
앱과 연구 가능 worker의 새 이미지·소스 바이트 대조·기존 연구 3070의 새 release
준비 결과를 기록했다. 3070의 기존 활성 커밋과 과거 job release registry는 유지된다.
PR 재검토와 적용 직전 외부 효과 대사를 교체 조건으로 삼았다.

## 2026-09-29 KST 업무 한도 제거 적용

소유자가 갱신된 PR #83과 앱·Slack 수신부·모델 worker·3070 연구 실행기 적용을 승인했다.
3070의 새 회사 release를 08:30 UTC에 활성화했다. 기존 연구 job의 release pin과
과거 커밋 등록은 보존했다. Lightsail에서는 새 PostgreSQL·설정 백업을 만들고,
실행 중 turn·연구 job·발송 중 outbox가 모두 0건인 것을 확인한 뒤 23:10 UTC에
앱·Slack 수신부·모델 worker를 커밋 `2e1b5c7` 이미지로 교체했다. 기존 역할 설정
overlay는 제거했다.

운영 앱과 모델 worker의 실제 `company_max_project_tasks`는 모두 `0`이며,
3070의 실행 환경에도 이를 다시 40으로 설정하는 값이 없다. API는 healthy이고
3070 연구 서비스는 새 코드 경로에서 active다. 교체 직후 모델 worker와 Slack 수신부
로그에서 오류·`Unknown specialist`는 0건이었다. 모델 작업 일시 중지는 해제했다.
[적용 영수증과 백업 해시](evidence/project-task-quota-cutover-20260928.json)를 기록했다.
기존에 거절된 Slack 메시지는 자동 재전송하지 않았다. 이번 교체 이후 사용자의 새
Slack 질문에 대한 접수·답변 왕복은 아직 검증하지 않았다.

23:44 UTC경 별도 후속 배포가 앱·Slack 수신부·모델 worker와 3070의 활성 release를
`3822845`로 교체했다. 00:12 UTC 재확인에서 현재 앱·모델 worker의 업무 한도 설정은
각각 `0`이고, 현재 코드의 업무 생성·지시 처리 조건도 무제한 동작을 유지한다.
3070 서비스는 후속 release 경로에서 active이며 기본값은 `0`이고 이를 덮는 환경값은
없다. 최초 적용 이후 새 사용자 inbound 기록은 확인되지 않아, 새 Slack 질문의 실제
접수·답변 왕복은 별도로 확인해야 한다.

저장소 `main`은 00:23 UTC 확인 당시 여전히 기본값 40이다. 후속 연구 후보의
[초안 PR #87](https://github.com/JJongAchii/quant-ai-company/pull/87)은 같은 무제한
변경을 포함하며 대상 브랜치가 `main`이다. 이 변경이 승인·병합되기 전 `main`에서
새로 배포하면 40건 기본값이 돌아올 수 있다. 00:21 UTC에 새로 접수된 이벤트 한 건은
일반 질문이 아닌 Slack 승인 action이고, 응답 outbox는 `delivered`였다.
