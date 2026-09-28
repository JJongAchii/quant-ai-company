# 자료 기반 연구 프로그램: 운영 반영 검토

상태: **소유자가 정확한 `2b4034d` 배포와 프로그램 승인 요청 게시를 승인했으나,
3070 활성 커밋의 외부 변경으로 활성화 중단**. 운영 서비스·DB·3070 활성 설정은 이 작업에서
바꾸지 않았고, 새 과학 실험은 0회다. 전체 식별자와 영수증 SHA는
[릴리스 패킷](evidence/research-programs-20260928/release-packet.json), 프로그램 권한은
[프로그램 검토 기록](evidence/research-programs-20260928/program-review.json)에 고정했다.

## 관측한 운영 상태

2026-09-28 08:04 UTC [Lightsail 읽기 전용 조사](evidence/research-programs-20260928/live-server-audit-final.json)에서
API는 `d571734e161c39f7c26252005231f93b619b3749`, 서버 연구 워커는
`31ff903f08a1471d26cb2ce0fef9643a0b34fc75`였다. 3070의 활성 회사 코드도
`31ff903f08a1471d26cb2ce0fef9643a0b34fc75`다. 서버 프로필은 기존
`kr-etf-monthly-python-v1` 하나이며 `research_programs` 테이블은 없다.
기존 프로젝트의 미션 `4462aff3-c7fe-5e66-a003-4aca2e8f3330`은 활성 상태,
누적 trial 1회다. 이 미션과 실행·업로드 영수증을 보존해야 한다. 이 관측은 여러
시스템을 동시에 잠근 스냅샷이 아니므로 활성화 직전에 다시 대조한다.

제안한 출처 `research:6a64df0e-9c60-555f-aa3a-a80a70e7f985`는 해당 운영 프로젝트의
승인된 비합성 기록이다. [출처 검사](evidence/research-programs-20260928/source-anchor-verification.json)는
원문 SHA-256 `aaffedd1643b16ec0a97d5f4bb0ef6084c6fc0fdc2b4ad75f707b808d0725379`와
첫 질문에 인용한 문장의 문자 위치 9331을 확인했다. 이 기록은 이전 재현 작업에서
기준선과 초과성과가 **미측정**이라고 명시한다. 수익성 입증 자료로 사용하지 않는다.

## 고정한 배포 후보

| 항목 | 고정값 |
| --- | --- |
| 회사 코드 | `2b4034db126ff13ff936c8e3def3ae89c7456824` |
| 회사 Git bundle SHA-256 | `83f148a6793a38ba24b240aa4e7ec40587890da5d5665449820e0d447c00fe57` |
| quant-data 코드 | `d6d7d0ed066ec49541e9acdd657c9ec5692ffc52` |
| 연구 원본 코드 / bundle SHA-256 | `422e5da2fbaf3682cfc04a5d8334dcc2f34d8a11` / `44fb48c8d4b299e66009a01e5b026a0d0b886cfa92bee25db9304607a1f09b49` |
| 새 ETF / 주식 공개 프로필 digest | `942a9897b1f4382c10e8c66757c651a9dd190b176f2bbf7ae3f5316a55c7a2cf` / `07056f8607ea990c09de6ade29a6230f60c3c0055c980728d95705e538dd21a3` |
| 서버 프로필 후보 파일 SHA-256 | `22a5fd7aa0fcd440730258f3a828720c04bf496ac28846354c84ee81e92d0779` |
| 3070 새 release 설정 SHA-256 | `45cb81892a5e5e0bd9b5a0d5e183c00f6b04ff7e3f16b028adb7b0a4c1c44161` |

후보 회사 코드는 연구 기능 `e949c02`에 현재 `origin/main`과 운영 cutover
`1892265`를 통합한 커밋이다. 서버에는 회사 bundle과
[기존 프로필을 그대로 포함한 후보 registry](evidence/research-programs-20260928/server-profile-registry-candidate.json)를
별도 경로에 배치했고, 정본 연구 bundle도 앱의 읽기 경로에 배치했다.
[서버 준비 영수증](evidence/research-programs-20260928/integrated-server-stage.json)에
각 경로와 SHA가 있다. 3070은 같은 회사 커밋의 release와 두 새 프로필을 준비했다.
[3070 준비 영수증](evidence/research-programs-20260928/integrated-worker-stage.json)은
이전 활성 설정이 유지됨을 확인한다. 기존 등록 프로필도 후보에 바이트 단위로 보존했다.

## 검증과 해석 범위

- 통합 커밋의 전체 `uv run pytest -q`: **1,253 통과, 45 건너뜀**. 이후 고정
  qlab checkout을 연결한 감사·producer/consumer 집중 검사 32건, 실제 PostgreSQL을
  사용한 연구 집중 검사 23건, Temporal 연구 검사 1건이 각각 통과했다. 전체 검사의
  건너뜀에는 환경 의존 검사가 포함된다. `uv run ruff check .`도 통과했다.
- 현재 API 커밋의 스키마를 일회용 **실제 PostgreSQL**에 만든 뒤 새 코드의 migration을
  두 번 수행해 `research_programs` 등 새 테이블 생성과 재실행을 확인했다.
  [migration 영수증](evidence/research-programs-20260928/integrated-migration-test.json).
  운영 DB에는 적용하지 않았다.
- 실제 `DESKTOP-5T00NAF` RTX 3070 Linux 샌드박스에서 통합 커밋으로 ETF/주식 ×
  전략/예측 주장 네 가지 **준비 구간 자격검사**가 통과했다.
  [자격검사 영수증](evidence/research-programs-20260928/integrated-worker-warmup.json).
  개발구간 입력은 마운트하지 않았고, 과학 실험·성과 측정은 0회다.
- 새 배포 이미지와 새 코드의 실제 Codex·Slack 왕복은 활성화 후 검사 항목이다.
  합성/모의 전송 테스트를 운영 인수로 해석하지 않는다.

## 첫 프로그램 승인 대상

[완전한 프로그램 명세](evidence/research-programs-20260928/first-program-draft.json)의 digest는
`53392822414ca32e89fab0f3a9a1093a350196345bd13084654a45510297159b`다.
국내 ETF·주식 각각 전략과 예측 주장 유형을 연다. 전역 상한은 **과학 trial 24회,
계산시간 36,000초, 미션 6개, 동시 미션 1개**다. 각 미션도 유한한 trial 상한을 가진다.
새로운 exact replication은 원문의 동일한 시장·추정량·기간·방법과 목표값이 확보될 때
별도 명세로 넣는다.

[첫 질문 초안](evidence/research-programs-20260928/first-question-draft.json)은 승인된 과거
출처가 남긴 측정 공백에서 출발한다. 2022년 말 정보로 고정한 ETF 10종의 월간 동일가중
규칙이 2023~2025년 개발구간에 30bp 스트레스 비용 후 양의 절대 CAGR을 내는지 묻는다.
동료 데이터 검토, 구현, 반론, 독립 감사와 해석을 거치도록 명세했으며 질문 자체는 아직
직원에게 제출하지 않았다. 원문 재현·배당 포함 총수익·봉인된 2026년 성과는 주장하지 않는다.
직원은 승인 후 접근 가능한 독립 검토 통과 자료를 읽고 가설을 수정하거나 거절할 수 있다.

## 검토 후 활성화 순서와 복구

1. 서버·3070의 이미지, 활성 설정 SHA, 기존 미션/lease/uncertain job을 다시 읽는다.
   값이 달라졌으면 통합·검사를 다시 한다. 신규 연구 스케줄링을 잠시 멈추고 진행 중
   작업의 기존 커밋·bundle·실행 영수증은 유지한다.
2. 기존 잠금·백업 절차로 운영 DB, 설정, research 경로, 3070 활성 config와 systemd
   unit을 보존한다. 비밀값은 기존 secret 경로에서만 다룬다.
3. 고정 회사 bundle을 검증해 코드를 풀고 quant-data 고정 커밋과 Docker 이미지를
   빌드한다. `docker compose config --quiet`, 이미지 revision label, 후보 registry와
   원본 bundle SHA를 확인한다. API·Slack socket·Temporal worker·dispatch의
   호환된 이미지와 overlay를 적용한다. 기존 다른 서비스의 설정을 초기화하지 않는다.
4. additive DB migration 후 서버의 후보 프로필 registry를 활성 경로로 원자적으로
   선택한다. 3070에서는 준비한 exact release를 release registry에 등록·선택하고
   user systemd 서비스의 작업 디렉터리와 코드 import 경로를 같은 커밋으로 바꾼다.
   진행 중 옛 커밋 job은 등록된 옛 release로 대사한다.
5. 실제 Codex 인증 경로, 서명된 Slack 이벤트, 모델 제안 검증, Temporal 작업,
   역할별 수신과 연구 출처 접근을 운영에서 확인한다. 그다음 프로그램 명세를 소유자
   승인 버튼으로 게시한다. 이 배포 검토와 **프로그램의 Slack 승인**은 별개다.
   승인 전에는 새 과학 trial을 만들지 않는다.
6. 장애 시 신규 스케줄링을 멈추고 위에서 보존한 이미지·overlay·서버 registry와
   3070 release/config/unit을 이전 식별자로 되돌린다. 추가 DB 테이블, 승인·trial 행,
   기존 미션과 불명확한 외부 효과 영수증은 삭제하거나 자동 재실행하지 않는다.
   API·Slack·Temporal·3070 상태를 다시 대사한다.

이번 검토의 결정은 **위 회사 커밋 배포**와 **위 프로그램 예산·범위의 승인 절차 진행**이다.
실제 모델이 제시한 가설과 과학 결과는 이 준비 자료의 검증 통과로 간주하지 않는다.

## 승인 후 활성화 시도와 중단

2026-09-28 08:18 UTC 소유자는 위의 정확한 회사 커밋 배포와 첫 프로그램 **승인 요청 게시**를
승인했다. 별도 Slack 서명 승인은 주지 않았다. [활성화 직전 조사](evidence/research-programs-20260928/activation-live-preflight.json)와
[서버 복구 기준](evidence/research-programs-20260928/activation-server-baseline.json)을 기록한 뒤,
서버에 정확한 회사 코드와 quant-data 커밋으로 앱·연구 워커 이미지를 빌드했다.
[이미지 검증](evidence/research-programs-20260928/activation-image-stage.json)은 OCI revision label과
이미지 안의 핵심 소스 해시가 후보 코드와 같음을 확인한다. 이 이미지는 활성 서비스에 적용하지 않았다.

3070 전환 직전에 [워커 재조사](evidence/research-programs-20260928/activation-worker-drift.json)에서
활성 설정 SHA가 검토한 `e615f9ff…`에서 `9741cdee…`로, 회사 커밋이 `31ff903f…`에서
`2e1b5c78…`로 바뀐 것을 확인했다. 설정과 systemd drop-in 수정 시각은 모두
2026-09-28 08:30:25 UTC이고 실제 워커 프로세스도 `2e1b5c78…`의 코드 경로에서 실행한다.
해당 커밋은 별도 작업의 프로젝트 과제 한도 변경이며 이 릴리스 패킷에는 포함되지 않았다.
반면 서버의 활성 이미지·설정·프로필과 DB 스키마는 이전 상태다.

검토한 정확한 3070 기준이 달라져 사전등록된 중단 조건을 적용했다. 운영 백업, DB migration,
서비스 전환, 3070 후보 활성화, Slack 승인 요청 게시를 수행하지 않았다. 기존 연구 미션과
영수증은 유지된다. 새 코드 후보는 3070의 현재 커밋과 통합·검증한 뒤 다시 검토해야 한다.
