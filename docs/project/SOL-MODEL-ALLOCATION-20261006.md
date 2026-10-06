# 직원 GPT-6.1 Sol 배정 — 2026-10-06

소유자의 요청에 따라 모델을 쓰는 직원과 뉴스 배경 작업 **16개를 `gpt-6.1-sol`로 고정 배정**했다. PostgreSQL 정책은 **#0 → #1**, 적용 시각은 **2026-10-06 22:13:29 KST**다. 업무별 추론 강도를 아래와 같이 설정했으며, 실제 운영 API 재조회와 실행 이미지의 배정 선택 검증을 통과했다.

## 배정

| 추론 강도 | 담당 | 설정 목적 |
| --- | --- | --- |
| `max` | 총괄 | 복합 업무의 최종 판단·조정. 기존 총괄 `max` 제약 유지 |
| `xhigh` | 금융전략, 퀀트개발, 독립검증, 리스크 | 복잡한 논리·구현·반례 검토 |
| `high` | 국내연구, 글로벌연구, 가상자산연구, 데이터, 개선 담당, 애널리스트/브리핑 | 근거 조사·분석·문제 진단 |
| `medium` | 뉴스 리포터, 플랫폼 운영, Quant Scout | 정리·큐레이션·반복 운영 |
| `low` | 뉴스 선별, 뉴스 검색 | 짧은 분류·탐색 |

정확한 대상 ID와 입력은 [plan.json](evidence/sol-allocation-20261006/plan.json), 실제 적용은 [apply.json](evidence/sol-allocation-20261006/apply.json)에 기록했다. 이 강도 배분은 역할별 초기 정책이며, 품질·응답 시간·사용량을 비교해 최적성을 입증한 결과는 아니다.

Slack 대화 활성 직원은 총괄·금융전략·국내연구·데이터·개선·애널리스트·뉴스 리포터 **7명 그대로**다. 대기 직원의 모델도 고정했지만 활성화하지 않았다. Quant Scout의 Slack 대화 비활성 표시는 전용 배경 서비스의 중지를 뜻하지 않는다.

Tech Scout와 Trend Scout는 자체 모델 요청을 실행하지 않는 발송 신원이므로 직접 배정에서 제외했다. Trend 편집이 사용하는 Reporter 정책은 이번 Sol 배정을 따른다. 별도 Claude 독립 검토 런타임은 기존 설정을 유지한다.

## 실제 계정 확인과 적용 경로

- 현재 선택한 공식 ChatGPT 계정은 `primary`, 계정 revision **2**다. 계정 변경은 없었다.
- 운영 Codex **0.160.1**의 실제 계정 catalog를 **22:12:43 KST**에 조회했다. `gpt-6.1-sol`과 `low/medium/high/xhigh/max`를 확인했다. catalog의 `ultra`는 회사의 입력 계약에 없으므로 사용하지 않았다. [catalog.json](evidence/sol-allocation-20261006/catalog.json)
- 운영자 토큰으로 실제 `/v1/model-assignments`에 인증하고, 배포된 소유자·채널 권한 검사와 모델/강도 검증 함수를 사용했다. 예상 정책 revision **0**과 예상 계정 revision **2**를 잠금 아래 비교했으며, 대기 중인 모델 명령은 없었다.
- typed proposal을 [preview.json](evidence/sol-allocation-20261006/preview.json)으로 먼저 검증했다. 이후 한 PostgreSQL transaction에서 전체 bindings, revision 이력과 회사 event를 함께 커밋했다.
- stable operation ID는 `sol-allocation-20261006-owner-chat-v1`, 입력 digest는 `474e03701785a9405dc13f5e53dabf38fe1330a945ff9f05c5e97ad6e400ceef`다. 이 작업의 actor는 `operator`, 승인자는 설정된 소유자이며, 출처는 이 대화의 명시적 변경 요청이다.
- Slack 이벤트를 만들거나 소유자 입력을 위조하지 않았다. 이력의 `command_id`는 `NULL`이며, 회사 event는 `operator_model_assignment`다. 새로운 모델 업무·Temporal 실행·Slack 메시지를 생성하지 않았다.

운영자 코드와 실행 경계는 [operator-allocation.py](evidence/sol-allocation-20261006/operator-allocation.py), [host-control.py](evidence/sol-allocation-20261006/host-control.py)에 보존했다. 실행은 배포된 API immutable 이미지의 별도 컨테이너에서 수행했다. 읽기 전용 루트·설정 mount, 내부 네트워크, 256 MiB 메모리, CPU 제한, 권한 제거를 적용했다. 운영자·DB secret은 기존 mount와 배포 entrypoint로만 읽었으며, Slack/Codex 인증 파일을 이 컨테이너에 전달하지 않았다.

## 검증과 운영 중 발견 사항

**2026-10-06 22:21:28 KST**에 [production-verify.py](evidence/sol-allocation-20261006/production-verify.py)를 실행해 [verify.json](evidence/sol-allocation-20261006/verify.json)을 남겼다.

| 실제 확인 | 결과 |
| --- | --- |
| 운영 API `/healthz` | `ok: true` |
| 인증된 `/v1/model-assignments`, `/v1/agents` | 정책 #1, 대상 16개 Sol 및 강도 일치, 활성 직원 7명 유지 |
| 실제 PostgreSQL 원장 | revision #1 한 행, 해당 operation event 한 행, Slack 명령 참조 없음 |
| 같은 operation의 영수증 조회 | `cached: true`, revision 추가 없음. [receipt.json](evidence/sol-allocation-20261006/receipt.json) |
| 기존 저장 요청 | **10,167건**의 ID·payload digest 보존 |
| 선택 계정 | `primary`, revision 2 유지 |
| 네 실행 경로 | worker 11개, news-worker 3개, quant-feed-worker 1개, maintenance 1개의 선택 결과가 운영 API와 일치 |
| 운영 서비스 | 15개 모두 실행 중 |

실행 경로 검증은 **실제 배포 immutable 이미지와 현재 설정을 쓰는 별도 컨테이너**에서 읽기 전용 DB transaction으로 수행했다. 운영 worker 안에서 추가 애플리케이션 프로세스를 실행하지 않았다. 새로운 모델 추론과 Slack 발송은 **0회**다. 이번 결과는 요청 준비에 적용되는 구성·선택 경로의 검증이며, 모델 응답 품질 평가나 공급자 측 모델/강도 증명이 아니다. 앞선 실제 Codex 구독 인증 검증은 [업그레이드 기록](CODEX-UPGRADE-20261006.md)에 별도로 남아 있다.

첫 preview는 운영 API 컨테이너의 128 MiB 제한 안에서 추가 프로세스를 실행하다 종료 코드 **137**로 끝났다. 같은 구간 API의 OOM event와 자동 재시작 **1회**가 관측됐다. 변경 모드는 아직 실행하지 않았고 정책은 #0으로 유지됐으며, `/healthz` 정상 복구를 확인했다. 이후 preview·apply·receipt를 별도 제한 컨테이너로 옮겼다. [실패·복구 영수증](evidence/sol-allocation-20261006/preview-failure.json)

검증 도중 별도 **Analyst preview** 배포 `0ea670b7b0637d91a46714b73ef8df96d979cc6f`가 **22:17:10 KST**에 news-worker·dispatch·briefing-data-worker를 교체했다. 이 모델 배정 작업은 해당 배포를 실행하지 않았다. 현재 container ID가 그 배포의 실제 영수증과 일치하는지 확인한 뒤 새 news-worker 이미지의 모델 선택을 다시 검증했다. 나머지 **12개 서비스의 ID·이미지·설정은 보존**됐다. 따라서 전체 15개 이미지가 그대로였다고 주장하지 않는다. 외부 배포의 식별 정보와 당시 영수증 SHA-256은 `verify.json`에 기록했다. 두 Codex 런타임·Claude·API·주 worker·계정 gateway는 교체되지 않았다.

## 적용 범위와 이후 조절

직원별 고정 배정은 **새로 준비하는 요청**에 적용된다. 이미 저장된 요청 및 이전 요청 ID로 이어지는 Codex 세션은 해당 모델을 유지한다. 명시적인 `이번 작업 모델 ...` 지정이 있으면 그 업무의 지정이 직원 고정보다 우선한다. 이번 작업은 기존 요청·세션·역할 기본 파일을 다시 쓰지 않았다.

소유자는 `#ai-account-switch`에 다음 **일반 메시지**를 입력해 확인하거나 조절할 수 있다.

```text
모델 배정 상태
모델 배정 이력
모델 지정 금융전략 gpt-6.1-sol high
모델 지정 뉴스 gpt-6.1-sol high
```

특정 업무만 바꾸려면 해당 직원의 업무 채널에서 첫 줄을 다음과 같이 지정한다.

```text
이번 작업 모델 gpt-6.1-sol xhigh
검토할 업무 내용
```

전체 원복은 `모델 배정 복원 0`이다. 이전 기본 정책으로 돌아가므로 Astra 및 구형 모델을 다시 사용할 수 있다. 이번 Sol 배분을 다시 복원하려면 `모델 배정 복원 1`을 사용한다. 각각 새 revision으로 기록되며 현재 계정 catalog의 지원 여부를 검증한다. `모델 자동 대상`은 고정을 해제하고 역할 기본값을 사용하므로 자동 최신 Sol 배정을 뜻하지 않는다.

운영자가 결과 수신 실패로 커밋 여부를 모를 때는 같은 operation의 **`receipt` 모드만 먼저 조회**한다. `not_committed`면 현재 revision·계정·catalog를 새로 확인한 뒤 판단한다. 성공 여부가 불명확한 `apply`를 자동 반복하지 않는다.

이 변경은 서비스 코드·이미지를 배포하지 않은 운영 설정 변경이다. 이번 검증은 실제 PostgreSQL·운영 API·실제 계정 catalog와 배포 이미지의 read-only 선택 검사다. 새 추론 또는 소유자 Slack 발송을 실행한 것으로 표시하지 않는다. `uv run ruff check .`, JSON 구문·증거 간 일치, qws 완료 검사를 수행했다. 애플리케이션 전체 pytest를 이번 설정 변경을 위해 다시 실행하지 않았다.
