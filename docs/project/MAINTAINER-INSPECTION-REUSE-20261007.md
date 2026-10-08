# 개선봇 반복 코드 조회·입력 최적화 — 2026-10-07 적용

사용자의 “그럼 진행해 .” 승인에 따라 기존 입력 최적화의 후속 작업을 구현하고 실제 서버에 적용했다.
소스 커밋은 `055a63ca2bb35d4f3e4540456dd85fef30a066a6`이며,
[PR #118](https://github.com/JJongAchii/quant-ai-company/pull/118)에 구현과 운영 증거를 모았다.
본 보고서의 마지막 운영 조회는 2026-10-08 KST다.

## 변경

- 동일 커밋·동일 코드 조회의 완전한 양성 결과를 서버에 저장된 영수증에서 재사용한다.
  경로 없는 검색은 읽을 수 있는 파일 목록까지 비교한다. 동일 커밋의 읽기 범위가 늘면 다시 조회한다.
  미발견 결과는 재사용하지 않는다.
- 이전에 읽은 모든 고유 코드 범위를 목록으로 제공한다. 본문은 요청한 경로·새 범위를 우선 선택한다.
  본문이 생략되거나 잘린 범위는 정확한 경로·행으로 다시 요청할 수 있다.
- 최초 코드 미리보기와 조회 본문의 길이를 줄이고, 중복 발췌의 추가 저장을 막는다.
  JSON을 간결하게 직렬화하고 중첩 키 순서를 고정한다.
- 원본 코드, 요청·응답, 조회 영수증, 관측·이력은 보존한다.
  인증·모델·effort·호출/조회 한도·수정 제안 검증 규칙은 기존 설정을 유지한다.

구현은 `maintenance/investigation.py`, `maintenance/runner.py`와 회귀 테스트에 한정했다.

## 실제 저장 입력 비교

원 Slack 사례 `49144860-7e42-5a72-8311-d47871ccf315`의 저장된 진단 입력 5개와
실제 PostgreSQL의 코드 자료를 읽기 전용으로 재구성했다. 기존 버전의 입력은 원 저장 문자열과
완전히 일치하는지 검사했다. 새 모델 호출과 데이터베이스 변경은 0회다.

| 입력 | 기존 문자 수 | 최종 후보 문자 수 |
|---|---:|---:|
| 최초 | 87,156 | 68,706 |
| 조회 1 이후 | 87,402 | 84,824 |
| 조회 2 이후 | 87,472 | 83,983 |
| 조회 3 이후 | 87,746 | 86,628 |
| 조회 4 이후 | 87,145 | 85,087 |
| 합계 | **436,921** | **409,228** |

다섯 입력 모두 줄었고, 합계 문자 수는 **6.34% 감소**했다.
반환 코드 발췌 38개 중 12개를 재사용했고, 누적 저장 발췌는 38개에서 25개로 줄었다.
각 라운드에 새 조회도 있어서 전체 저장소 본문을 읽은 횟수는 두 버전 모두 4회였다.
이는 토큰 절감률이나 모델 캐시 적중률의 측정치가 아니다.

최초 구현의 합계 감소는 2.67%였지만 일부 후속 입력은 커져서 적용하지 않았다.
미리보기·선택 순서를 조정하고 파일 커버리지 변경 시 재조회하는 보호 조건을 추가한 최종 후보를 적용했다.
[최종 비교 영수증](evidence/maintainer-inspection-reuse-20261007/qualification.json)과
[첫 구현 비교](evidence/maintainer-inspection-reuse-20261007/qualification-prototype.json)을 보존한다.
이전 3.84% 감소와 이번 값을 합산하지 않는다.

## 검증

- 기존 소스 `679a370fa29e9cc0d3f1f11506ccd8a2dd93ae10`에서 같은 생산자·소비자 회귀 테스트가
  실제 assertion으로 실패했고, 최종 후보에서 통과했다. 수집·import 실패가 아니다.
- 초기 구현의 전체 로컬 PostgreSQL 시험: 1,585 passed / 49 skipped / 2 warnings.
- 최종 후보의 관련 회귀 시험: 30 passed. 양성 재사용, 미발견 재조회, 커밋·범위 변경,
  파일 커버리지 확대, 이전 범위 목록, 원본 입력 불변성, 잘린 본문의 후속 조회를 포함한다.
- 최종 소스 커밋의 [전체 CI](https://github.com/JJongAchii/quant-ai-company/actions/runs/37607381665)는 성공했다.
  실제 PostgreSQL·Temporal 시험과 공식 CLI의 설정·카탈로그 프로토콜 검사를 포함한다.
  CLI 프로토콜 검사는 빈 인증으로 수행했고 모델 추론은 하지 않았다.
- 로컬 시험의 모델·GitHub·Slack은 모의 fixture다. 실제 운영 적용은 아래 영수증으로 확인한다.

[검증 영수증](evidence/maintainer-inspection-reuse-20261007/validation.json)과
[소스 CI 영수증](evidence/maintainer-inspection-reuse-20261007/source-ci.json)을 따른다.

## 실제 서버 적용

2026-10-07 22:51:26 KST에 진행 중인 진단과 대기 중인 모델 응답이 없는지 확인하고
개선봇 컨테이너에 두 모듈만 적용했다.
설치 이미지 ID는 `sha256:cf217bf53ee0f70ec031ddeab3e856e7aa7df2dced51b92af3466fe061726bae`다.
설치된 두 파일의 SHA-256은 자격검사한 소스와 일치한다.

이후 조회에서 개선봇 실행·최근 heartbeat를 확인했다. OOM과 재시작 횟수는 0이고
256 MiB 메모리 한도를 유지한다. 적용 당시 다른 컨테이너 14개의 ID·이미지·실행 상태,
인증/환경과 격리 설정은 그대로였다. 기존 모델 호출 85개와 불확실 검토 11개는 원본 행 해시가
유지됐고, 원 Slack 사례는 `done`을 유지했다.

[적용 영수증](evidence/maintainer-inspection-reuse-20261007/cutover.json)과
[설치 상태·실제 사용량](evidence/maintainer-inspection-reuse-20261007/live-after.json)을 보존한다.
서버 영수증 경로는 `/var/lib/quant-company/operator/maintainer-inspection-055a63ca`다.
Compose override는
`/var/lib/quant-company/config/maintainer-inspection-055a63ca2bb35d4f3e4540456dd85fef30a066a6.compose.json`이다.
원 이미지와 정확한 maintenance 전용 Compose 목록을 적용 영수증에 남겼다.
복구 시 실행 중 요청을 먼저 대사하고 maintenance 이미지 설정만 원 이미지로 되돌린다.

## 적용 후 관측과 남은 확인

운영 중 새로 생긴 진단 `c44f74be-7d4d-4aa1-a789-a4edd1ee5374`는
실제 모델 응답 5개를 기록했다. 해당 호출 합계는 input 127,462 / output 11,953 / cached input 0이다.
원 사례와 과제가 다르므로 이전 호출 총량과 비교해 절감률을 계산하지 않는다.
이 진단은 조회 4라운드 뒤 `investigation_budget_exhausted`로 보류됐고,
저장 발췌 16개·서버 재사용 발췌 0개였다. 완료한 진단으로 보고하지 않는다.
[후속 작업 조회](evidence/maintainer-inspection-reuse-20261007/post-job.json)를 따른다.

새 요청은 적용된 입력 준비·코드 재사용 경로를 사용한다.
실제 토큰·캐시 변화와 조회 한도 내 진단 성공 여부는 이후 동등한 사례에서 관측한다.
이번 확인 작업은 기존 보류 건을 재개하거나 불확실 호출을 재전송하지 않았다.

