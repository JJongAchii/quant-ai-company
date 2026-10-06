# Codex 0.160.1 운영 갱신 — 2026-10-06

상태: **단일 버전 관리·신버전 감지 코드 구현; Linux·실제 계정 자격검증과 운영 교체 진행 중**.
소유자는 이전 진단과 구체적인 갱신 계획을 확인한 뒤 `그래 진행해줘`로 실행을 승인했습니다.

Codex 0.154.0의 오래된 목록이 최신 모델을 누락한 원인은
[진단 기록](CODEX-UPDATE-DIAGNOSIS-20261006.md)에 있습니다.
검증 대상은 공식 안정 릴리스 0.160.1이고, 직원 모델 배정과 기본값은 변경하지 않습니다.
[공식 변경 기록](https://learn.chatgpt.com/docs/changelog)

## 구현

- 검증 manifest 하나에서 Docker 설치 버전·npm integrity·Linux 실행 파일 SHA-256과
  실행·목록 조회 gate의 버전을 연결합니다. 무검증 CLI는 호출 전에 거절합니다.
- 매일 09:17 KST GitHub workflow로 stable 버전을 감지해 unqualified 후보 PR을 기록합니다.
  후보 기록에는 운영 반영·모델 호출·직원 배정 변경 권한이 없습니다.
- 실제 gate를 통과한 private 계정 목록 응답에 `cli_version`, `checked_at`을 추가했습니다.
  기존 회사 client의 `모델 목록` 경로는 같은 `profile`/`models` 계약을 사용합니다.
- 운영 후보는 현재 실제 두 runtime 이미지와 source root에 한정한 overlay입니다.
  main의 설치 package와 Quant의 별도 `/opt/quant-code/src/quant_company`를 보존합니다.

## 검증 범위

로컬 macOS의 실제 CLI 구성/목록 RPC 18개 검사는 통과했습니다. 인증 gate만 모의 처리하며
빈 인증으로 모델 추론을 실행하지 않았습니다. 실제 계정 사용권 검사와 구분합니다.

두 실제 runtime source cohort는 각각 97 passed, 1 skipped, 1 deselected입니다.
company-side `RuntimeClient.models` 검사는 runtime에 설치되지 않는 client 기능이므로
해당 cohort에서는 제외하고 main 전체 suite에서 검증합니다. subprocess와 API는 실제,
해당 단위 검사의 Slack/model RPC는 모의입니다.

Linux 이미지·실제 ChatGPT 계정·새 모델의 구조화/native Quant 응답·웹검색과 구버전 세션
이어받기는 다음 단계에서 별도의 고정 요청 ID·receipt로 검증합니다. 진행 중 결과를 통과로 표시하지 않습니다.

[갱신 runbook](../runbooks/codex-upgrades.md)
