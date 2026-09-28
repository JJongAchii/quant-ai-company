# Slack 직원 무응답 조사 — 2026-09-28

실제 Slack, 운영 PostgreSQL, Temporal Cloud와 고정 연구 worker를 읽기 전용으로 대조했다.
[기계 판독 증거](evidence/slack-reply-incident-20260928.json)에 시각과 범위를 남겼다.

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
기존 이미지·설정·영수증을 보존한다.

GitHub CLI 인증이 유효하지 않아 검토용 PR은 아직 열지 못했다. 운영 반영 전 PR 검토가
필요하다는 runbook 조건이 남아 있다.

운영 적용 후에는 DM의 기존 두 Temporal turn이 완료되고 실제 Slack 답변 영수증이 생기는지
확인한다. 거절된 `research-center` 메시지는 자동 재생하지 않는다. 사용자가 새 메시지로
상태를 물었을 때 한 번의 답변이 게시되는지 확인한다. 기존 코드에서도 정확히 `상태`라고
보내면 모델 없이 상태 요약을 받을 수 있다.
