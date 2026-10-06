# 최신 모델 누락 진단과 Codex 갱신 방식 — 2026-10-06

후속 상태: **20:43 KST에 두 실행기를 0.160.1로 갱신했습니다.**
[실제 운영 갱신·신버전 감지 결과](CODEX-UPGRADE-20261006.md).
아래는 갱신 이전 진단 시점의 기록입니다.

상태: **진단·개선안과 후보 CLI의 초기 검사 완료; 운영 갱신 미적용**.
운영의 두 Codex runtime은 모두 0.154.0입니다. 이번 검사는 운영 소프트웨어·배정·인증 파일을
변경하지 않았으며 모델 추론과 Slack 발송을 점검용으로 실행하지 않았습니다.

## 확인한 원인

2026-10-06 소유자의 실제 `모델 목록` 요청은 완료됐고 outbox가 한 번 시도 후 delivered를
기록했습니다. 선택 계정 primary revision 2의 결과는 Astra와 GPT-5.6 계열 4개였습니다.
즉 실제 명령 수신·응답 경로는 작동하고 있습니다.

운영 CLI, `deploy/Dockerfile`의 설치·검사, `codex_runner.SUPPORTED_CLI_VERSION`,
`model_catalog`의 버전 검사가 모두 0.154.0으로 고정돼 있습니다. 바이너리만 갱신하면
서비스가 미검증 버전으로 거절하므로 이미지와 서비스 버전 계약을 함께 갱신해야 합니다.

OpenAI 공식 변경 기록은 2026-09-29의 0.159.1에 GPT-6.1 Sol을 bundled catalog의 기본 모델로
추가했다고 설명하며, 확인 시점의 최신 안정 릴리스는 2026-10-05의 0.160.1입니다.
구버전 카탈로그가 최신 모델 누락의 주된 원인으로 보입니다.
[공식 변경 기록](https://learn.chatgpt.com/docs/changelog)

실제 사용 가능 여부에는 rollout·로그인 방식·계정과 workspace 설정도 영향을 줍니다.
CLI 갱신 후 선택 계정에서 다시 목록을 조회하고 필요한 실제 실행을 검증해야 합니다.
[공식 모델 문서](https://learn.chatgpt.com/docs/models)

## 후보 버전의 초기 검사

로컬 macOS arm64의 격리 설치로 0.160.1을 실행했습니다. 현재 서비스가 만드는 일반 실행,
웹검색, 세션 재개 명령의 설정을 CLI가 받아들였습니다. 빈 stdin과 없는 schema 경로로
실행을 차단했으므로 prompt를 제출하지 않았습니다. 이 결과는 설정 문법의 호환성입니다.
실제 웹검색이나 기존 세션 재개 결과를 검증한 것은 아닙니다.

같은 버전의 app-server `initialize`/`model/list`는 `gpt-6.1-sol`, `gpt-6-sol`, `gpt-6-luna`를
포함한 8개를 반환했습니다. 인증 디렉터리는 비었고 로그인 gate만 모의 처리했습니다.
운영 계정의 사용권·구독 잔여량·실제 추론 증거와 구분합니다.

## 권장 운영 방식

**Codex 프로그램 갱신과 직원 모델 배정을 별도로 관리합니다.** 프로그램은 신모델을
인식할 수 있도록 갱신하고, 직원 배정은 소유자의 기존 `모델 지정` 명령으로 변경합니다.

1. **버전 관리:** 검증한 CLI version·배포 digest를 한 manifest에서 읽도록 Docker 설치,
   runtime 사전 검사와 목록 조회를 연결합니다. 재배포가 오래된 버전으로 돌아가지 않도록
   실제 두 runtime 이미지와 별도 소스 경로도 release manifest에 기록합니다.
2. **신버전 감지:** 안정 릴리스를 정기 확인해 갱신 후보와 PR을 만듭니다. 모델 추론을 쓰지
   않습니다. 자동 설치·새 timer는 이번 진단에서 활성화하지 않았습니다.
3. **후보 자격검증:** Linux 운영 이미지에서 공식 ChatGPT 인증, 격리·도구 제한, 목록 RPC,
   typed JSON·native Quant 계약, 웹검색·사용량·기존 세션·영수증 호환성을 확인합니다.
   현재 macOS 설정 검사만으로 운영 후보를 통과시키지 않습니다.
4. **운영 교체:** 승인된 후보만 기존 배포 잠금·호출 drain·백업·immutable image 절차로
   두 Codex runtime에 적용합니다. 기존 역할·모델 배정과 입력 digest·불명 결과 영수증을
   유지하고, 불명 추론을 재실행하지 않습니다. 프로그램 갱신 중에는 직원 모델을 바꾸지 않습니다.
5. **목록 갱신과 표시:** 선택 계정의 새 목록을 재조회하고 CLI 버전·검증 일시·갱신 후보를
   `모델 목록` 또는 운영 상태에 표시합니다. 목록에 나타난 모델을 소유자가 별도로 지정합니다.

당장의 다음 단계는 0.160.1의 Linux·운영 계정·실제 실행 경로 검증과 두 runtime의 갱신입니다.
상시 운영에서는 **신버전 감지 → 후보 검사 → 통과한 버전 교체 → 계정 목록 재조회**를 사용합니다.
신모델의 성능 순위 평가와 자동 직원 배분은 별도 연구·정책이며 이 버전 갱신 절차에 섞지 않습니다.

현재 자동 release executor의 모델 배정 보호 guard는 유지합니다. 갱신 절차에 실제 두 runtime
이미지·원래 source revision·별도 PYTHONPATH·모델 배정 overlay 보존을 통합한 뒤 재개합니다.

## 증거

- [실제 운영 CLI와 소유자 조회](evidence/codex-update-20261006/production-runtime-readback.json)
- [실제 소유자 명령·발송 receipt](evidence/codex-update-20261006/owner-catalog-delivery.json)
- [0.160.1 후보의 초기 검사](evidence/codex-update-20261006/candidate-cli-probe.json)
- [검사 코드](evidence/codex-update-20261006/candidate-cli-probe.py)

검사 재현은 `.local/codex-release-probe-01601`에 공식 npm 패키지 0.160.1을 별도 설치한 뒤
`uv run python docs/project/evidence/codex-update-20261006/candidate-cli-probe.py`를 사용합니다.
빈 인증의 CLI/protocol 검사로서 운영 계정 사용권 검사나 배포가 아닙니다.
