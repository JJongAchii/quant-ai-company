# 뉴스·트렌드·브리핑·Quant Scout의 Claude 배정

소유자의 2026-11-17 요청을 구현한다. 뉴스 데스크와 Quant Scout의 **새 요청**을 Claude Sonnet 5.5 구독 실행기로,
브리핑을 Claude Opus 5.5 구독 실행기로 보낸다. 영상·쇼츠·뉴스레터·음성·그림·썸네일 설정은 바꾸지 않는다.
결정 기록: [ADR 0051](../adr/0051-editorial-desk-on-claude-slots.md). 직원 배정은 [claude-staff.md](claude-staff.md)다.

| 대상 | 업무 | 현재(정책 #2) | 새 배정 |
| --- | --- | --- | --- |
| `reporter` | 뉴스 편집, 트렌드 작성 | gpt-6-astra medium | claude-sonnet-5-5 medium |
| `news_screening` | 뉴스 선별 | gpt-6.1-sol low | claude-sonnet-5-5 low |
| `news_search` | 뉴스 검색 | gpt-6.1-sol low | claude-sonnet-5-5 low |
| `quant_scout` | Quant Scout | gpt-6.1-sol high | claude-sonnet-5-5 high |
| `market_brief` | 아침·마감 브리핑 | gpt-6-astra xhigh | claude-opus-5-5 xhigh |

## 실행 경계

- 요청의 모델이 공급자를 정한다. Claude의 한도·인증·불명 결과를 Codex에서 다시 실행하지 않는다.
- 브리핑(`news-brief-`), 뉴스 데스크(`news-`), Quant Scout(`quant-feed-`)는 claude-runtime의 자기 CLI 자리를 쓴다.
  영상이 모델 단계여도 기다리지 않고, 영상도 이들을 기다리지 않는다. 직원 업무는 기존 공유 자리와 영상 우선을 쓴다.
- 브리핑 단계는 Codex와 같은 엄격한 출력 스키마와 회차 마감을 쓴다. 뉴스 데스크는 한 호출 600초다.
- 회사 업무는 API 크레딧 레인을 쓰지 않는다. CLI에는 검색 요청의 `WebSearch` 말고 도구가 없다.
- 이미 저장된 요청·이어지는 세션·재시도는 원래 모델과 영수증을 유지한다.

## 배포와 적용

1. PR 검토 → main 병합 → CI-green 정식 릴리스. 모든 서비스가 같은 main SHA인지 `release-status.json`과 verify로 확인한다.
   PR #241(직원 배정)이 먼저 또는 함께 병합되어야 한다. 이 PR은 그 위에 쌓였다.
2. **운영자 runtime.env**: `CLAUDE_MEMORY_LIMIT=1536m` (지금 `512m`). claude-runtime에 CLI가 최대 넷 동시에 돌기 때문이다.
   배정 적용 전에 넣고 claude-runtime을 정식 apply로 다시 만든다. 영상·쇼츠·메일·브리핑이 진행 중이 아닐 때 한다.
3. 배정 preview. 기존 operator bearer와 서버 loopback만 쓴다. 토큰을 출력하거나 명령 인수·Git에 넣지 않는다.
   `expected_revision`은 `GET /v1/model-assignments`의 현재 revision이다(직원 배정을 먼저 적용했다면 3).

   `POST /v1/model-assignments/claude-editorial`

   ```json
   {"operation_id": "claude-editorial-20261117-owner-v1", "expected_revision": 3, "dry_run": true}
   ```

   preview는 위 표의 다섯 대상, 바뀌지 않는 직원 대상(`preserved`), 활성 직원, `plan_digest`를 돌려준다.
   모델을 호출하거나 Slack 메시지를 만들지 않는다.
4. 적용은 **브리핑 회차가 진행 중이 아닐 때**만 한다(06:00–08:50, 15:45–18:20 KST를 피한다). market_brief가 바뀌면
   브리핑 정책 digest가 바뀌어, 커밋되지 않은 회차는 `brief_policy_changed`로 무효가 된다.

   ```json
   {"operation_id": "claude-editorial-20261117-owner-v1", "expected_revision": 3, "dry_run": false,
    "plan_digest": "검토한-preview의-64자리-sha256"}
   ```

   같은 operation ID를 다시 보내면 기존 영수증을 돌려준다. 결과가 불명확하면 다른 ID로 반복하지 않는다.
   operation ID 하나는 한 종류의 배정에만 쓸 수 있다.

## 적용 뒤 확인

- 새 요청의 영수증(`/var/lib/quant-company/claude/jobs/<request_id>.json`)에서 `provider=claude`,
  `usage.actual_model`, `usage.billing_mode=subscription`을 확인한다.
  - `news-screen-`·`news-`·`news-trends-`·`quant-feed-`는 `claude-sonnet-5-5`, `news-brief-`는 `claude-opus-5-5`이어야 한다.
- 다음 아침·마감 브리핑의 커밋 시각과 Slack 도착 시각을 이전(아침 07:04–07:13, 마감 16:46–16:57 커밋)과 비교한다.
- 영상 제작 시간대(07:10–09:15, 16:50–19:00)에도 `news-` 영수증이 계속 생기는지 본다.
- Codex 영수증(`/var/lib/quant-company/codex/jobs`)이 영상 그림(`codex/video-image-jobs`)과 적용 전에 저장된 요청만 남는지 본다.
- **구독 사용량**: 서비스는 Claude 구독의 5시간·주간 사용률을 읽지 않는다. 첫 1–2주는 소유자가 claude.ai의 사용량 화면을
  매일 확인한다. 주간 한도에 걸리면 영상도 함께 멈춘다.

## 복원

소유자의 계정 제어 채널에서 `모델 배정 복원 <적용 전 revision>`을 쓴다. 과거 기록을 덮어쓰지 않고 새 revision을 만든다.
이미 준비된 Claude 요청은 원래 모델과 영수증으로 끝난다. **ChatGPT 플랜을 낮춘 뒤에는** 이 복원으로 뉴스 물량을 Codex가
받기 어렵다. 그래서 플랜 변경은 Claude 단독 운영을 1–2주 본 뒤에 한다.

## Codex에 남는 경로

| 경로 | 이유 |
| --- | --- |
| 영상 그림 생성(`video-image-runtime`, 주 약 22건) | 영상 설정 유지(소유자 2026-11-17). Plus에서 `gpt-6-astra`와 이미지 한도를 쓸 수 있는지는 확인하지 못했다. 플랜 변경 직후 `모델 목록`으로 확인한다. |
| 적용 전에 저장된 요청·세션·재시도 | 고정된 요청을 다른 공급자로 바꾸지 않는다(재실행 금지). |
| `모델 목록`·계정 상태 조회 | 추론이 아니라 ChatGPT 계정 카탈로그·인증 조회다. |

## 검증의 범위

`tests/test_claude_editorial.py`는 실제 PostgreSQL, 실제 subprocess, ASGI HTTP와 합성 CLI 응답으로 뉴스 선별·편집과
브리핑 전 단계의 producer-consumer, 자리 분리, 영상 우선 예외, 마감, 배정 적용을 검사한다.
`scripts/trial_claude_editorial.py`는 고정된 실제 입력 5건을 운영 CLI 버전으로 한 번씩만 실제 구독에 보낸 제한 시험이다.
둘 다 운영 배포·운영 배정·장기 구독 용량의 검증이 아니다.
