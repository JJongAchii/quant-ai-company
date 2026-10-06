# 제어 채널 명령어 도움말 — 2026-10-06

2026-10-06 21:30 KST에 운영 반영을 완료했다. 소유자는 **#ai-account-switch**에 일반 메시지로
`도움말`을 보내면 모델·계정 관리 명령어와 예시를 같은 스레드에서 볼 수 있다.
`명령어`, `help`, `모델 도움말`, `모델 명령어`도 같은 안내를 표시한다. 총괄 멘션은 선택 사항이다.
Slack 네이티브 `/help`는 등록하지 않았다. 슬래시 없이 일반 메시지로 입력한다.

## 안내 내용과 처리

모델 목록·배정 상태·이력, 직원 모델 지정·고정 해제·이력 복원, 공용 계정 상태·전환,
일반 업무 채널 또는 DM에서 이번 작업만 모델을 지정하는 예시를 안내한다. 직원 별칭과 추론 강도,
총괄의 max 필수 조건, `모델 자동`이 기본 배정 복귀라는 점도 표시한다.

기존 소유자·워크스페이스·앱·전용 채널 검증과 durable inbox/outbox 경로를 사용한다.
명시적 도움말은 계정 명령 오류 문구 없이 안내를 보여준다. 인식하지 못한 제어 채널 문장과
잘못된 모델 배정 문법도 동일한 명령어 안내를 제공한다. 중복 Slack 이벤트는 완료 결과를 재사용한다.
안내는 모델을 호출하거나 모델·계정 배정을 변경하지 않는다.

## 검증

| 범위 | 결과 |
|---|---|
| 관련 실제 PostgreSQL/Temporal 회귀 | 127 passed |
| 실제 운영 소스에 최소 패치 적용 후 같은 회귀 | 127 passed |
| Linux GitHub CI 전체 서비스 회귀 | 1551 passed, 47 skipped, 1 deselected |
| Linux 실제 CLI 빈 인증 protocol | 9 passed; 모델 추론 없음 |
| Ruff·whitespace | 통과 |

Slack 서명 이벤트와 Codex 출력은 합성 fixture다. 도움말의 소유자·채널·앱·서명 제한,
멘션·스레드 처리, 중복 outbox 한 건, 모델 턴·배정 변경 없음과 기존 정책 보존을 확인했다.
운영 API 이미지에는 기존 provider 코드가 남아 있으므로 cohort 검사에서 fake CLI 버전만
그 이미지의 runner 상수에서 읽도록 했다. 실제 운영 provider 파일과 두 Codex 실행기는 변경하지 않았다.
[검사 영수증](evidence/command-help-20261006/validation.json),
[실제 CI](https://github.com/JJongAchii/quant-ai-company/actions/runs/37462435178).

## 운영 반영

구현 커밋은 `04a465865cf18dbc3cf2fb1aaa4191f6ca7e55ac`이며
[PR #114](https://github.com/JJongAchii/quant-ai-company/pull/114)가 main에 병합됐다.
활성 이미지 `sha256:d6b04a5bfc004bc1a24869ef61a0ec829895e823528b7fd9f24b786face0f650`은
원래 API 이미지의 실제 설치 경로 위에 도움말 관련 파일 다섯 개만 반영했다.
전체 210개 파일을 비교했고 기존 나머지 205개 파일은 그대로 보존했다.

호스트 backup lock을 잡고 모델 실행을 마무리한 뒤 표준 백업을 만들었다. API·Slack socket·
계정 gateway 세 서비스만 재생성했고 기존 환경·명령·mount·보안 설정을 대조했다.
나머지 12개 컨테이너의 ID와 이미지를 유지했다. 기존 요청 digest 10,156건과 기존 모델 영수증·
세션을 배포 경계에서 보존했다. 계정은 primary revision 2, 모델 배정은 revision 0·고정 없음으로 유지됐다.

실제 확인 시각은 21:31:59 KST다. 15개 서비스가 정상 실행 중이며 API health와 인증 제한,
세 서비스의 활성 도움말 모듈 및 전체 source inventory를 확인했다. 실제 Slack 봇 7개 인증과
socket 프로세스 소유 TLS 연결 7개를 확인했다. Temporal 계정 제어 workflow는 RUNNING,
activity poller는 2개다. 실제 소유자의 새 도움말 메시지와 Slack 전달 영수증은 아직 확인하지 않았다.
점검용 Slack 메시지나 도움말용 실제 모델 추론은 보내지 않았다.

백업 `company-20261006T122712Z-1d51dca1.tar.gz`의 SHA-256은
`8387c42eb4dbb9d774256138846d37f6fc9985102fd860b415f68e7d39a0b07d`다.
실제 로컬 checksum과 S3 archive 크기 519,158,261 bytes 및 AES256을 확인했다.
[배포 영수증](evidence/command-help-20261006/production-cutover.json),
[운영 확인](evidence/command-help-20261006/production-verification.json).

## 재생성과 운영 범위

각 서비스의 기존 Compose stack 끝에 다음 override를 유지한다.

- `/var/lib/quant-company/config/command-help-20261006-api.compose.json`
- `/var/lib/quant-company/config/command-help-20261006-slack-socket.compose.json`
- `/var/lib/quant-company/config/command-help-20261006-account-gateway.compose.json`

원본 inspect·전체 요청 digest·영수증 inventory와 일회성 배포 journal은 호스트의 비공개 경로에
보존한다. 회사 저장소에는 비밀 환경을 제외한 요약 영수증과 실행한 운영 스크립트만 기록했다.
배포 스크립트는 기존 journal이 있으면 재실행을 거절한다. 결과를 먼저 대사한다.
기존 mixed-fleet 자동 코드 교체 보호와 timer는 유지했다. 네이티브 slash command·Slack OAuth
권한·직원 모델 선택·구독 계정·연구 실행은 이번 변경 범위에 포함하지 않는다.

[사용 가이드](../runbooks/model-assignments.md),
[Slack slash command 등록 방식](https://docs.slack.dev/interactivity/implementing-slash-commands/).
