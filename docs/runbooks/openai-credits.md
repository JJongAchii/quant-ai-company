# OpenAI 무료 API 크레딧 우선 사용

소유자 요청(2026-11-05): 직원이 무료 API 예산을 먼저 사용하고 소진 후 ChatGPT 구독을 사용한다.
기능은 기본 꺼짐이다. 이 문서는 활성화 완료 증거가 아니다.

## 실행 정책

- `OPENAI_API_ENABLED=true`이며 주계정·유효한 grant·API 키·예약 가능한 잔액이 있을 때
  `https://api.openai.com/v1/responses`를 직접 호출한다. Codex CLI의 API-key 인증을 사용하지 않는다.
- 선택된 모델과 effort, 작업 ID, 입력 digest, 계정 revision, 기존 typed proposal 검증을 유지한다.
  현재 가격을 검증한 `gpt-6-astra`, `gpt-6.1-sol`만 API 대상이다. 임의 모델 대체는 없다.
- 예산 부족·만료·미설정이면 기존 공식 ChatGPT 구독으로 실행한다. API의 명확한 400/401/403/404/429
  거절도 미실행 영수증을 남긴 후 구독으로 넘긴다. 잔액/지출 한도 거절은 해당 grant 만료까지 API를 쉰다.
- 타임아웃·연결 중단·5xx·불완전 응답·잘못된 출력·취소는 이미 청구됐을 수 있다. 같은 ID를 API나
  구독으로 재실행하지 않고 영수증 대사를 기다린다. 클라이언트·API SDK의 자동 재시도는 없다.
- 기존 영수증과 구독 continuation, native 웹 검색, 이미지 첨부, 예비계정, 명시적 effort가 없는 구형 요청, 가격 미등록 모델은 기존
  구독 경로를 유지한다. 새 기능이 기존 감사 대화의 head나 웹 검색 계약을 바꾸지 않는다.
- API 예산은 회사·뉴스·Quant·브리핑 lane과 두 Codex 컨테이너가 하나의 파일 잠금 원장으로 공유한다.
  요청 전에 UTF-8 입력 상한+framing 여유와 최대 출력 64,000토큰 비용을 예약한다. 30분 캐시 쓰기와
  긴 컨텍스트 요율을 반영한다. 완료 후 토큰을 정산하며, 미계측/불확실 호출의 예약은 남긴다.
- `budget_charge_usd`는 캐시 쓰기 비용을 보수적으로 가정한 상한이다. OpenAI 청구서나 실시간 잔액이 아니다.
  reasoning 토큰은 output에 포함되므로 다시 합산하지 않는다. 한 호출의 예약이 안 들어가면 남은 소액도
  구독으로 넘긴다. 영수증의 `usage.backend`/`billing_mode`로 실제 경로를 식별한다.

## 활성화 전 운영자가 확인할 값

OpenAI 혜택을 API 조직에 연결한 뒤 실제 **남은 무료 크레딧**, **결제 주기 시작**, **만료일**을 확인한다.
첫 지급은 일할 계산될 수 있다. 다른 앱이 같은 조직 잔액을 사용했다면 이를 먼저 차감한 금액을 설정한다.
기능이 OpenAI 청구 포털을 읽거나 grant를 자동 갱신하지 않는다.

무료분만 사용하려면 연결 조직에 구매한 크레딧·후불 청구 경로가 없고 자동 충전이 꺼져 있어야 한다.
프로젝트 예산만으로 무료/유료 잔액을 구별할 수 없다. 이 사실을 확인한 뒤에만
`free_only_confirmed`를 true로 둔다. 조직의 청구 설정이 바뀌면 API 경로를 먼저 끈다.

## 릴리스 운영자에게 전달할 항목

리뷰된 PR을 main에 머지하고 CI를 통과한 **main SHA**와 아래 값을 운영자에게 전달한다.
`deploy/release.py`가 두 런타임의 코드·compose overlay를 릴리스한다. 새 서비스·호스트 unit·렌더 이미지
변경은 없다. 이 기능을 넣기 위해 호스트를 직접 편집하거나 컨테이너를 수동 재생성하지 않는다.

1. 운영자 전용 파일 `/var/lib/quant-company/openai-credits/api_key`: 해당 조직의 제한된 프로젝트 키.
   소유자 `10001:10001`, 0400. 채팅·Git·로그·Slack에는 값을 남기지 않는다.
2. `/var/lib/quant-company/openai-credits/grant.json`: 아래 형식. 실제 값 확인 전 예시는 실행되지 않는다.
   디렉터리는 0750, grant는 `10001:10001` 0400. 두 모델 컨테이너에만 읽기 전용으로 마운트한다.
3. runtime.env의 `OPENAI_API_ENABLED=true`. 확인되지 않았으면 false를 유지한다.

```json
{
  "version": 1,
  "grant_id": "replace-with-actual-billing-cycle-id",
  "budget_usd": 0,
  "starts_at": "REPLACE_WITH_ACTUAL_START_AND_TIMEZONE",
  "expires_at": "REPLACE_WITH_ACTUAL_EXPIRY_AND_TIMEZONE",
  "free_only_confirmed": false
}
```

`budget_usd`는 0 초과 200 이하이며 실제 남은 무료분 이하여야 한다. 기간은 timezone이 명시된 32일
이하의 결제 주기다. 동일 grant ID의 예산/기간을 바꾸어 원장을 초기화할 수 없다. 새 지급을 확인한 뒤
새 ID와 새 기간·예산을 운영자가 제공한다. 기존 주기의 영수증과 원장은 보존한다.

## 확인과 복구

- `/var/lib/quant-company/codex/jobs/openai-api/status.json`: grant, 예약 포함 금액, 만료, 최근 판단.
  운영 `ops status`에도 요약한다. 이 파일에는 키·프롬프트·응답 원문이 없다.
- 최초 실제 요청의 canonical `jobs/<request_id>.json`에서 `usage.backend=responses_api`,
  `usage.billing_mode=api_credit`, 토큰, grant ID를 확인하고 OpenAI Usage의 같은 시간대와 대조한다.
- 잔액 부족/만료 뒤 새 요청은 `backend=codex_cli`, `billing_mode=subscription`임을 확인한다.
- ledger 손상/변경 또는 불확실 영수증은 동일 ID를 지우거나 새 ID로 대체하지 않는다. API 측 사용 기록과
  대사한 뒤 원래 영수증을 복구한다. 이미 실행됐을 수 있는 예약은 자동으로 반환하지 않는다.
- 기능을 꺼도 기존 API 완료 영수증은 캐시에서 복구하고, 불확실 요청은 계속 차단한다. API 재호출이나
  자동 크레딧 구매는 발생하지 않는다. 원래 요청과 무관한 새 요청은 기존 구독으로 처리한다.

## 근거와 검증 범위

- [OpenAI 구독 API 크레딧](https://developers.openai.com/api/docs/guides/chatgpt-api-credits)
- [Standard 요율](https://developers.openai.com/api/docs/pricing)
- [30분 캐시 쓰기·usage](https://developers.openai.com/api/docs/guides/prompt-caching)

테스트는 모의 Responses HTTP 전송과 실제 프로세스로 실행한 가짜 Codex CLI를 사용한다.
실제 무료 크레딧 지급·실제 API 청구·운영 활성화 검증은 별도 영수증이 필요하다.
