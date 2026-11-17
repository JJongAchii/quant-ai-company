# 뉴스·브리핑·영상을 제외한 Claude 직원 배정

사용자의 2026-11-16 요청을 구현한다. 일반 직원의 **새 요청**을(Quant Scout는 2026-11-17 편집 배정으로 옮김)
`claude-opus-5-5` 구독 실행기로 보낸다. 추론 강도와 직원 활성 여부는 현재 배정을 유지한다.
이는 모델 품질 비교 결과나 Plus 무대기 운영의 증명이 아니다.

| 전환 대상 | 현재 정책 #2의 강도 |
| --- | --- |
| 총괄, 독립 검증 | `max` |
| 금융전략, 리스크, 퀀트 개발, 개선 담당 | `xhigh` |
| 국내·글로벌·가상자산 연구, 데이터 | `high` |
| 플랫폼 운영 | `medium` |

Reporter, 뉴스 선별·검색, 트렌드, 애널리스트·브리핑은 기존 Codex 배정을 유지한다.
2026-11-17부터 Quant Scout와 이 편집 대상은 [편집 배정](claude-editorial.md)이 맡는다. 두 배정은 대상이 겹치지
않아 어느 순서로 적용해도 서로의 결과를 덮지 않는다.
영상·쇼츠·뉴스레터·음성·그림·썸네일·자막과 별도 Claude 독립 설명 검토의 모델·정책은 그대로다.
Tech Scout와 Trend Scout의 자체 모델 호출은 추가하지 않는다.

## 구현과 실행 경계

- 라우팅은 저장된 요청의 모델 ID에 고정된다. Claude의 한도·인증·불명 결과를 Codex에서 다시 실행하지 않는다.
- Claude 직원·Quant 업무는 구독으로만 실행한다. 기존 Claude API 예산을 이 업무에 사용하지 않는다.
- 일반 업무는 서비스가 검증하는 `AgentDecision`, Quant는 기존 자료·인용 검증, 연구 단계는 기존 제한된
  출력 계약을 쓴다. CLI에는 쉘·파일·MCP·동료 에이전트 도구를 주지 않는다. 검색 요청만 `WebSearch`를 허용하고,
  완료된 도구 영수증을 확인한다. 검색 후보는 계속 원문 검증 전 자료다.
- 영상이 모델 단계에 있으면 직원·Quant의 새 호출은 기다린다. 일반 Claude 회사 호출은 기존 짧은
  실행 시간 상한을 사용하며 영상용 두 시간 상한을 가져오지 않는다.
- Claude 감사 세션은 private 영수증에서 이전 프롬프트·응답 전체를 복원한다. 생략하지 않고 상한을 넘으면
  거절한다. 공급자 thread ID로 표시하지 않으며, 회사 DB의 이전 턴 전체와 context digest를 대조한다.
- 이미 저장한 요청, 작업별 지정, 이어지는 기존 세션, 직원 연습·비교·재현 요청은 원래 선택을 유지한다.
  복원도 이미 실행한 요청을 다시 만들거나 불명 결과를 재실행하지 않는다.

## 배포와 적용

운영 변경은 AGENTS.md 및 ADR 0044/0045의 경로를 따른다. **PR 검토 → main 병합 → CI-green 정식 릴리스**
이후 모든 서비스가 같은 main SHA인지 `/var/lib/quant-company/release-status.json`과 정식 verify로 확인한다.
새 서비스·인증 파일·runtime.env 값·렌더 이미지 변경은 필요하지 않다.

새 API가 일부 서비스에만 들어간 혼합 버전에서는 배정을 바꾸지 않는다. 코드 릴리스만으로 DB 배정은 바뀌지 않는다.
운영자가 기존 인증 운영 API에 아래 preview를 요청한다. operator bearer는 기존 보관 경계를 사용하고 출력하거나
명령 인수·Git 파일에 넣지 않는다. HTTP 요청은 승인된 암호화 연결 또는 서버 loopback에서만 실행한다.

`POST /v1/model-assignments/claude-staff`

```json
{
  "operation_id": "claude-staff-20261116-owner-v1",
  "expected_revision": 2,
  "dry_run": true
}
```

preview는 12개 대상, 기존·신규 bindings, 보존 대상, 활성 직원 목록, `plan_digest`를 반환한다.
모델을 호출하거나 Slack 메시지를 만들지 않는다. 현재 정책이 #2가 아니면 거절하므로 새 상태부터 확인한다.

검토한 preview의 digest를 넣어 적용한다.

```json
{
  "operation_id": "claude-staff-20261116-owner-v1",
  "expected_revision": 2,
  "dry_run": false,
  "plan_digest": "검토한-preview의-64자리-sha256"
}
```

정책 잠금 안에서 revision과 digest를 다시 확인하고 새 revision·전체 배정·운영자 event를 한 transaction으로
기록한다. 같은 operation ID를 재조회하면 기존 영수증을 반환한다. 성공 여부가 불명확할 때 다른 ID로 반복하지 않는다.
`GET /v1/model-assignments`와 `GET /v1/agents`로 결과를 확인한다.

적용 후 실제로 승인된 작은 일반 업무와 Quant 호출에서 request ID, provider=`claude`,
actual_model=`claude-opus-5-5`, billing_mode=`subscription`을 확인한다. 동작 검증용 Slack 메시지를 별도로
생성했다면 그 사실을 기록한다. 새 계약의 실제 Claude 호출을 확인하기 전에는 운영 검증 완료라고 표시하지 않는다.

## 복원

소유자의 기존 계정 제어 채널에서 `모델 배정 복원 2`를 사용한다. 현재 계정 모델 목록 검증과 새 revision을
거치며 과거 기록을 덮어쓰지 않는다. 이미 준비된 Claude 요청은 계속 원래 모델과 영수증을 사용하므로,
실행 중인 작업을 보존한 상태에서 정책을 복원한다. `MODEL_ASSIGNMENTS_ENABLED=false`는 복원 방법이 아니다.

`모델 목록`에 추가된 Claude 항목은 배포한 Claude 실행 계약의 지원 모델이다. ChatGPT 계정 catalog의 항목이나
실제 Claude 구독 잔여량·계정별 사용권 검증 결과로 해석하지 않는다.

## 검증의 범위

`tests/test_claude_company.py`는 실제 PostgreSQL, 실제 subprocess, 모의 CLI 응답과 ASGI HTTP로
직원·Quant·감사 세션의 producer-consumer 경로, 전환·취소·동일 ID 재시도를 검사한다.
실제 Slack·Claude 클라우드·운영 배포 검증과 구분한다.

Claude CLI의 도구 범위와 허용 설정은 [공식 CLI reference](https://code.claude.com/docs/en/cli-reference)의
`--tools`, `--allowedTools`, `--max-turns`, `--no-session-persistence`를 따른다.
