# Astra 유지·GPT-6.1 Sol 업그레이드 — 현재 배정 #2

사용자가 명확히 정정한 기준에 따라 **원래 Astra 담당 9개는 Astra로 복원·유지하고, 비 Astra 담당 7개만 GPT-6.1 Sol로 배정**했다. 전체 담당의 추론 강도도 역할에 맞춰 다시 설정했다. 실제 운영 적용은 **2026-10-06 22:45:06 KST**, 정책은 **#1 → #2**다.

이전 [Sol 전체 배정 #1](SOL-MODEL-ALLOCATION-20261006.md)은 요청을 잘못 해석한 결과이며 현재 정책이 아니다. 해당 이력·영수증을 삭제하거나 덮어쓰지 않고 새 revision으로 교정했다. 원래 모델 구분은 최초 정책 #0의 실제 운영 조회 기록을 사용했다. [기준 모델과 원본 digest](evidence/mixed-allocation-20261006/baseline.json)

| 모델 | 추론 강도 | 담당 | 배정 이유 |
| --- | --- | --- | --- |
| GPT-6 Astra | `max` | 총괄, 독립검증 | 최종 조정과 엄격한 반례·누수 검토에 높은 사고 강도 배정 |
| GPT-6 Astra | `xhigh` | 금융전략, 리스크, 애널리스트/브리핑 | 경제적 설명·위험·복수 가설의 깊은 비교 |
| GPT-6 Astra | `high` | 국내연구, 글로벌연구, 가상자산연구 | 자료 조사와 근거 연결에 충분한 사고 강도 배정 |
| GPT-6 Astra | `medium` | 뉴스 리포터 | 근거가 있는 뉴스 정리·후속 설명 |
| GPT-6.1 Sol | `xhigh` | 퀀트개발, 개선 담당 | 구현·수정·디버깅에 높은 사고 강도 배정 |
| GPT-6.1 Sol | `high` | 데이터, Quant Scout | 데이터 진단 및 연구 자료·재현 조건 검토 |
| GPT-6.1 Sol | `medium` | 플랫폼 운영 | 반복 운영과 상태 설명 |
| GPT-6.1 Sol | `low` | 뉴스 선별, 뉴스 검색 | 짧은 분류·검색 |

정확한 대상 ID와 입력은 [plan.json](evidence/mixed-allocation-20261006/plan.json)에 기록했다. Astra 유지 대상은 `director`, `financial_strategist`, `researcher_kr`, `researcher_global`, `researcher_crypto`, `validator`, `risk`, `reporter`, `market_brief`다. 나머지 모델 실행 대상은 Sol이다.

이는 업무 성격에 따른 초기 운영 정책이다. 실제 업무의 품질·시간·사용량을 비교해 최적 배분을 입증한 결과는 아니다. [OpenAI 공식 Astra 문서](https://developers.openai.com/api/docs/models/gpt-6-astra)와 [Sol 문서](https://developers.openai.com/api/docs/models/gpt-6.1-sol)는 모두 `low/medium/high/xhigh/max`를 지원한다고 명시한다. [공식 추론 강도 지침](https://developers.openai.com/api/docs/guides/deployment-checklist)은 강도에 따른 처리 시간·추론량의 차이와 실제 업무 평가를 권한다. 이 서비스는 ChatGPT 구독 인증을 사용하므로 API 단가를 이 회사의 구독 비용이나 절감액으로 해석하지 않았다.

## 적용과 실제 검증

- 현재 선택 계정은 **primary, 계정 revision 2**, CLI는 **0.160.1**이다. 실제 계정 catalog를 **22:44:25 KST**에 조회해 두 모델과 선택 강도를 확인했다. [catalog](evidence/mixed-allocation-20261006/catalog.json)
- 운영자 인증, 현재 소유자 권한, 예상 정책 #1/계정 #2, 16개 원래 역할 기본 모델을 검증했다. typed proposal의 각 대상은 원래 Astra면 Astra, 나머지면 Sol이어야 하며 총괄 `max` 제약도 적용했다. [쓰기 없는 preview](evidence/mixed-allocation-20261006/preview.json)
- 별도 읽기 전용 루트의 제한 컨테이너에서 하나의 PostgreSQL transaction으로 bindings·revision·회사 event를 함께 기록했다. 운영 서비스 코드·이미지를 배포하지 않았다. [operator 코드](evidence/mixed-allocation-20261006/operator-allocation.py), [실행 경계](evidence/mixed-allocation-20261006/host-control.py)
- operation ID는 `mixed-allocation-20261006-owner-correction-v1`, 입력 digest는 `1d7313e0535ce39361a042f569e7509f502ec429b96d9bd1010f8756831992eb`다. actor는 운영자, 출처는 이 대화의 명시적 교정 요청이다. Slack 이벤트나 소유자 메시지를 위조하지 않았으며 revision의 `command_id`는 `NULL`이다.
- [apply 영수증](evidence/mixed-allocation-20261006/apply.json)과 동일 operation의 [receipt 재조회](evidence/mixed-allocation-20261006/receipt.json)가 일치했다. 재조회는 `cached: true`, 추가 revision은 없다. 성공 여부가 불명확하면 `apply`를 반복하기 전에 `receipt`를 조회한다.

**2026-10-06 22:48:48 KST**의 [실제 검증 기록](evidence/mixed-allocation-20261006/verify.json):

| 확인 | 결과 |
| --- | --- |
| 실제 인증 운영 API | `/healthz ok`, 정책 #2, Astra 9개·Sol 7개, 강도·고정 배정 일치 |
| PostgreSQL 원장 | #2 한 행·교정 operation event 한 행·기존 #0/#1 보존·대기 명령 0 |
| 네 배포 producer 이미지의 read-only 선택 | worker 11개, news-worker 3개, quant-feed-worker 1개, maintenance 1개 일치 |
| 기존 저장 요청 | **10,175건**의 ID·payload digest 보존 |
| 직원 활성 상태 | 기존 **7명** 유지, 대기 직원의 모델만 설정 |
| 서비스·계정 | 15개 서비스 ID·이미지·설정·재시작 횟수와 primary/계정 #2 유지 |
| 이 교정이 시작한 모델 추론·Slack 발송 | 모두 0회 |

[production-verify.py](evidence/mixed-allocation-20261006/production-verify.py)는 앞서 검증한 producer probe의 전체 SHA-256을 확인해 재사용한다. 해당 probe는 **실제 배포 이미지와 현재 설정을 쓰는 별도 제한 컨테이너**에서 읽기 전용 DB transaction으로 선택 결과를 조회한다. 운영 worker 안에 추가 프로세스를 실행하지 않는다. 공급자 측 모델/강도 증명이나 새로운 추론 품질 평가로 표시하지 않는다.

## 확인·후속 조절

`#ai-account-switch`에 일반 메시지 **`모델 배정 상태`** 또는 **`모델 배정 이력`**을 입력하면 현재 정책과 복원 이력을 볼 수 있다. 예를 들어 `모델 지정 금융전략 gpt-6-astra high`는 해당 직원의 강도를 조절한다. 이후에도 특정 업무의 첫 줄 `이번 작업 모델 MODEL EFFORT`가 직원 고정보다 우선한다. 총괄은 `max`를 유지해야 한다.

현재 조합을 다시 복원하려면 **`모델 배정 복원 2`**를 사용한다. #1은 전체 Sol 배정, #0은 구형 Sol/Terra/Luna를 포함한 원래 기본 배정이다. 복원은 새 revision과 현재 계정의 지원 검증을 거친다. `모델 자동 대상`은 고정을 해제해 역할 기본값을 사용하며 최신 모델 자동 선정은 아니다.

배정은 새로 준비하는 요청에 적용된다. 이미 저장된 요청·이어지는 세션은 해당 모델을 유지한다. Tech Scout·Trend Scout는 자체 LLM 실행이 없는 신원이므로 직접 배정하지 않는다. 별도 Claude 독립 검토도 기존 런타임을 사용한다. 이 교정은 직원 활성화, 연구 실행, 계정 전환 또는 API 인증 전환을 하지 않았다.

검증은 실제 PostgreSQL·운영 API·실제 ChatGPT 계정 catalog와 네 배포 이미지의 read-only 선택 확인이다. Ruff, Python/JSON 구문과 증거 일치, whitespace, qws 완료 검사를 수행했다. 구성·운영 증거 변경을 위해 애플리케이션 전체 pytest를 다시 실행하지 않았다.
