# 전용 Maintainer·개선 케이스 검증

2026-09-22. 상태: **첫 구현 범위의 로컬 인수 완료, 운영 활성화 미수행**.

## 반영 내용

- [최종 회사 채널 구조](SLACK-COMPANY-DESIGN.md)를 설계·로드맵에 반영했다.
  이번 코드는 improvements와 Maintainer이며 다음 구현은 data-watch의 전체 개요 + 핵심 상세다.
- Maintainer는 설정으로 활성화하며 기존 engineer의 모델/effort를 재사용한다. 전용 채널의
  메시지와 후속 대화를 처리하고 상태·목록 조회에는 모델을 호출하지 않는다.
- 새 진단에 케이스 ID, 원문 소유자/revision, 대상 스레드, 봇 카드의 전달 영수증을 연결한다.
  기존 진단은 원래 경로를 유지한다. 사용자 원문은 수정하지 않는다.
- 경량 Temporal workflow가 모델·CI 작업과 별도로 진행 카드를 갱신한다. 불명확한 게시와
  갱신은 재전송하지 않으며, 실제 전달된 PR 알림의 커밋과 승인 대상을 대조한다.
- 설치용 Slack manifest와 설정 예제, [사용·운영 인수 절차](../improvements.md)를 준비했다.

## 실행 검증

| 검사 | 결과 | 실제/모의 범위 |
|---|---|---|
| `uv run pytest -q --disable-warnings --maxfail=3 --junitxml=docs/project/evidence/improvements-tests.xml` | 997 passed, 36 skipped | 실제 임시 PostgreSQL DB와 로컬 Temporal. opt-in 실제 구독·플랫폼별 검사는 제외 |
| `uv run pytest deploy/test_deployment_contract.py -q --disable-warnings --maxfail=2 --junitxml=docs/project/evidence/improvements-deployment-tests.xml` | 27 passed | 실제 Docker Compose 설정 해석; 서버 배포 인수가 아님 |
| `uv run ruff check .` / `git diff --check` | 통과 | 소스 린트·패치 공백 검사 |

새 케이스 검사 11개는 전용 ingress와 중복 억제, 원문→전용 스레드, 상태 카드 갱신,
불명확한 루트·갱신, 권한·revision 변경, 실제 알림 이후의 정확한 커밋 승인, 새 오류만 자동 접수,
자기 메시지 수정 제한, 진단 멱등성, typed 대화→기존 엔진→최종 케이스 결과를 검증했다.
긴 maintenance activity가 대기 중인 동안 별도 실제 Temporal worker가 카드를 갱신했고,
그 workflow history를 재생했다. 새 회사 인스턴스로 같은 DB를 읽어 중복 카드가 생기지 않음도 확인했다.

처음 Temporal 테스트 서버 다운로드가 지연되어 동일 SDK 버전의 기존 로컬 실행 파일 캐시를
사용했다. 이후 전체 검사를 정상 완료했다. PostgreSQL은 이 worktree 전용 임시 클러스터를 사용했고,
실제 사용자 DB에는 접속하지 않았다. 합성 Slack HTTP·모델·GitHub 응답을 실제 서비스로 표시하지 않는다.

기계 판독용 [검증 영수증](evidence/improvements-validation.json)과
[전체 검사](evidence/improvements-tests.xml), [배포 설정 검사](evidence/improvements-deployment-tests.xml)를 보존한다.

## 운영 활성화 경계

이 변경의 Slack 앱 설치, 실제 채널 생성/연결, 실제 구독 Maintainer 대화, PR 승인 후 실제 서버
반영은 아직 수행하지 않았다. `COMPANY_IMPROVEMENTS_ENABLED` 기본값은 false다.
정확한 검토 커밋과 실제 앱 신원·채널 ID를 준비한 뒤 사용 절차의 운영 인수를 수행해야 한다.
data-watch, 본사 요약, 신규 정기 발송, 다른 비활성 직원의 활성화는 이번 코드에 포함하지 않는다.
