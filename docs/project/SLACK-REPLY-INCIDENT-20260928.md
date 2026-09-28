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
전체 1,226개 통과·38개 건너뜀·실패 0개, Ruff 통과를 확인했다. 운영에는 아직
적용하지 않았다. 연구 고정 worker의 코드 기본값은 40이므로 앱만 교체하면 직원
위임에서 다시 거절될 수 있다. 고정 연구 job과 3070 release registry를 대조해
연구 가능한 worker 이미지의 안전한 교체를 준비한다.
