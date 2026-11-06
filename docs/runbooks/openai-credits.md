# OpenAI API 월 $120 우선 사용

소유자 지시(2026-11-06): 실제 크레딧 만료일을 추가로 확인하는 대신 서비스 자체의 월 $120 API
상한으로 지금 전환을 준비한다. 기존 키는 로컬 보관 및 인증 조회가 확인됐고 자동 충전은 소유자가 껐다고
확인했다. 아래 정책은 API 결제액이 모두 무료 혜택으로 차감된다고 단정하지 않는다.

## 사용 정책

- 한국 시간(KST) 매월 1일 00:00에 새 월 예산 $120이 시작된다. OpenAI의 실제 지급·갱신일을 추정하는
  규칙이 아니다. 해당 월 예약 포함 사용액이 상한에 이르면 이후 요청은 공식 ChatGPT 구독으로 간다.
- 실제 API 잔액/지출 한도 거절도 구독으로 넘기고 해당 월에는 API를 쉬며, 다음 달에 다시 시도한다.
  크레딧 구매·자동 충전·API SDK 자동 재시도는 없다. 지급 혜택 자체가 없어져도 프로그램이 이를 구매하지 않는다.
- 가격을 검증한 주계정의 `gpt-6-astra`, `gpt-6.1-sol` 신규 일반 직원 요청에 적용한다. 모델·effort·작업
  ID·입력 digest·계정 revision·typed proposal 검증을 유지한다. 기존 대화 continuation, native 검색,
  이미지, 예비계정, 구형 effort/가격 미등록 모델은 기존 구독을 유지한다.
- 네 lane과 두 모델 컨테이너는 한 원장을 공유한다. 요청 전에 UTF-8 입력 상한+framing 여유 및 최대
  출력 64,000토큰 비용을 예약한다. 30분 캐시 쓰기와 긴 컨텍스트 요율을 포함하고 완료 후 정산한다.
  남은 예산에 한 호출의 예약이 안 들어가면 그 호출은 구독으로 간다.
- 타임아웃·연결 중단·5xx·불완전 응답·잘못된 출력·취소는 이미 실행됐을 수 있다. 동일 ID를 어느 경로로도
  자동 재실행하지 않으며 예약을 보존한다. API를 꺼도 기존 완료 영수증은 복구하고 불확실 요청은 차단한다.

## 릴리스 운영자 전달값

프로젝트 AGENTS.md에 따라 **리뷰된 PR을 main에 병합하고 main CI가 통과한 SHA**로만 릴리스한다.
개발 세션이 호스트를 직접 편집하거나 수동 컨테이너 재생성/runtime.env 편집을 하지 않는다.
새 서비스·호스트 unit·렌더 이미지 변경은 없다. 다음 파일과 값은 릴리스 운영자에게 전달한다.

1. `/var/lib/quant-company/openai-credits/api_key`: 로컬 `~/.config/quant-company/openai-api-key`에 보관한
   실제 프로젝트 키. 값은 Git·채팅·로그·Slack에 남기지 않는다. 소유자 10001:10001, 권한 0400.
2. `/var/lib/quant-company/openai-credits/grant.json`: 아래의 소유자 승인 월 예산 정책. 실제 무료 크레딧
   만료일·free-only 확인 필드는 필요 없다. 디렉터리는 0750, 파일은 10001:10001 0400.
3. runtime.env 값 `OPENAI_API_ENABLED=true`. `deploy/release.py`로 승인된 main을 반영한다.

```json
{
  "version": 2,
  "budget_id": "owner-monthly-api-120",
  "monthly_cap_usd": 120,
  "api_spend_authorized": true
}
```

월 ID는 `owner-monthly-api-120-YYYY-MM`으로 정해지며, 실행 중 budget_id를 바꿔 사용액을 초기화하지 않는다.
월 상한은 120 이하만 허용한다. API 측 무료/구매/후불 잔액의 구성은 이 정책으로 검증되지 않는다.
구매 잔액이나 후불 청구가 있다면 그쪽에서 차감될 수 있다. 월 원장의 값은 서비스의 보수적 예산 계상이며
OpenAI 청구서·실시간 잔액 자체가 아니다. 추론 토큰을 출력 토큰에 다시 더하지 않는다.

## 운영 확인과 복구

- `/var/lib/quant-company/codex/jobs/openai-api/status.json`에 월 ID, `budget_mode=owner_monthly_cap`,
  예약 포함 사용액, 다음 예산 갱신 시각이 남는다. `ops status`는 이를 ‘OpenAI API 월예산’으로 표시한다.
- 최초 실제 작업 영수증에서 `usage.backend=responses_api`, `billing_mode=api_credit`,
  `api_budget_mode=owner_monthly_cap`을 확인하고 OpenAI Usage와 대조한다.
- 잔액 부족/예산 부족 뒤 새 요청은 `backend=codex_cli`, `billing_mode=subscription`인지 확인한다.
- 불확실 영수증이나 읽기 불가 원장을 지우거나 ID를 바꿔 재실행하지 않는다. 제공자 사용 기록과 대사한 뒤
  복구한다. 미정산 예약은 자동 반환하지 않는다.
- 버전 1의 실제 무료 grant 정책도 호환된다. 그 정책만 실제 starts_at/expires_at과 free_only_confirmed가
  필요하며 32일 이하 기간으로 제한된다. 버전 2 월 예산은 이 확인 절차에 의존하지 않는다.

## 검증 범위

모의 Responses HTTP 전송, 실제 가짜 Codex 프로세스, 임시 PostgreSQL과 실제 Compose 구성 검사로
전환·공유 예산·월 갱신·실패 복구를 검증한다. 실제 운영 활성화는 릴리스 및 최초 실행 영수증으로 따로 확인한다.
API 인증/모델 정보 조회 성공은 실제 추론 또는 청구 경로의 검증을 대신하지 않는다.
