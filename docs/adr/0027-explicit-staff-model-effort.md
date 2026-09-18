# ADR-0027: 직원별 모델과 reasoning effort를 명시한다

- 날짜: 2026-09-18
- 상태: Accepted
- 승인 근거: 현재 대화의 사용자 지시
  “응 이어서 진행해. 아 그리고 astra 모델과 같이 성능이 좋은 모델을 써야하는 직원들은 effort는 max로 해서 해. 모두가 max로 해야하는지까지는 모르겠지만 적어도 director는 항상 제일 좋은 모델의 max로 해야함”

## 문제

운영 director의 model ID는 Astra였지만 요청 계약과 CLI 실행기에 effort 전달 필드가 없었다.
모델명만으로 max 실행을 보장할 수 없었다. 직원 연습과 개선 BOT의 재현 비교도 같은 설정을
동결해야 모델 설정 변화가 절차 개선의 효과로 섞이지 않는다.

## 결정

| 담당 | 모델 | Effort | 이유 |
|---|---|---|---|
| director | gpt-6-astra | max | 최종 판단·위임·사용자 소통 |
| 금융전략, 국내·글로벌·가상자산 연구, 검증, 리스크 | gpt-6-astra | max | 가설·반증·금융 판단 |
| 개발, 개선 BOT | gpt-5.6-sol | max | 코드 수정·원인 분석 |
| 데이터, 운영 | gpt-5.6-terra | high | 제한된 조회·계약 검사·운영 작업 |
| 독립 설명 검토 | claude-opus-5 | max | 별도 모델 계열의 설명 검토 |

현재 비활성 직원은 그대로 두며 모델 설정만 준비한다. 모델 사용권은 기존 구독을 사용한다.
2026-09-18 공식 모델 문서 및 서버 Codex 0.154.0 카탈로그에서 Astra와 max 지원을 확인했다.
director는 이 검증된 flagship ID와 max 이외의 운영 설정을 시작 시 거부한다. 더 좋은 모델이
나오면 지원 여부·구독·CLI를 검증한 명시적 릴리스로 상수를 갱신한다. 검증되지 않은 latest
별칭이나 자동 하향 전환을 쓰지 않는다.

`Role.reasoning_effort`를 업무·웹 검색·직원 연습·개선 BOT의 `ProviderRequest`로 전달한다.
Codex에는 `-c model_reasoning_effort="max"`, Claude에는 호출마다 `--effort max`를 전달한다.
모델과 effort는 봇의 runtime context와 durable CLI receipt에도 남긴다. 이는 **요청한 설정**의
증거다. 제공자가 실제 모델을 보고하는 경우와 effort를 독립적으로 증명하는 것은 구별한다.

새 요청의 digest는 effort를 포함한다. 이미 저장된 요청·응답은 수정하지 않는다. effort 없는
옛 Codex 요청은 원래 기본값, 옛 Claude 요청은 당시 명시값 high를 유지하고 같은 ID를 새로운
effort로 재실행하지 않는다. 진행 중 요청은 끝나거나 원래 영수증으로 대사한 뒤 릴리스한다.

직원 연습은 role snapshot의 effort를 동결하고, 개선 비교의 양쪽에도 같은 effort를 사용한다.
연습 진척과 오류 해소 판정은 모델·effort·절차 digest·평가 버전을 함께 구분한다. 과거 effort가
없으면 미확인 구성으로 남긴다. 새 max 결과로 과거 성과를 소급 인증하지 않는다.

## 검증과 영향

실제 PostgreSQL 테스트로 요청 동결·재시작·연습·웹·개선 비교의 전달을 검사한다. 실제 Codex
CLI는 빈 입력·빈 인증 디렉터리로 설정 수용을 검사한다. 모델 추론을 하는 운영 smoke 결과는
배포 영수증과 따로 기록한다. max는 응답시간·구독 사용량을 늘릴 수 있다. 자원 증설·유료 API
전환은 포함하지 않는다.

## 출처

- [OpenAI Astra 모델](https://developers.openai.com/api/docs/models/gpt-6-astra)
- [Codex 설정](https://learn.chatgpt.com/docs/config-file/config-reference) — 문서의 effort enum은
  현재 CLI 카탈로그보다 좁게 기재되어 있어 pinned CLI의 설정 검사 결과도 보존한다.
- [Claude 모델 설정](https://code.claude.com/docs/en/model-config) — Opus 5 max를 CLI 호출마다 지정한다.

운영 확인 기록: [직원 모델 정책](../project/STAFF-MODEL-POLICY-20260918.md).
