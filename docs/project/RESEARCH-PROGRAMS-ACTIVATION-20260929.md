# 자료 기반 연구 프로그램 운영 활성화 결과

2026-09-29 09:01 KST 기준, 소유자가 승인한 회사 코드
`382284512ade2c0750c79494389a0899d2b46688`을 Lightsail의 API·Slack socket·
Temporal worker·dispatch와 3070 연구 poller에 적용했다. 첫 프로그램
`53392822414ca32e89fab0f3a9a1093a350196345bd13084654a45510297159b`의
**승인 요청**을 기존 소유자 Slack 스레드에 게시했다. 프로그램 상태는 `draft`이고,
별도의 Slack 소유자 승인이 아직 기록되지 않았다.

## 적용과 복구 기준

- 활성화 직전 서버의 API·Slack socket·연구 worker는 예상보다 최신인 `2e1b5c78…`였다.
  [활성 이미지 소스 대조](evidence/research-programs-20260928/activation-v2-active-image-validation.json)와
  [새 기준 조사](evidence/research-programs-20260928/activation-v2-live-server-preflight.json)에서
  실제 코드·설정과 진행 중 작업 0건을 확인한 뒤 같은 승인 후보로 진행했다.
- [운영 백업](evidence/research-programs-20260928/activation-v2-backup.json)은 PostgreSQL·설정·
  연구 상태를 포함한다. archive SHA-256은
  `c1c4acc7112fea6fcdca7e6e5fbbedf7bc8c0cae0d982748fca977a87dfa05a2`다.
  [S3 객체 검사](evidence/research-programs-20260928/activation-v2-backup-verification.json)에서
  같은 크기와 암호화 상태를 확인했다. 서버와 3070의 이전 설정·유닛은 개인 복구 경로에 보존했다.
- [가산적 DB migration](evidence/research-programs-20260928/activation-v2-migration.json) 뒤
  [네 서비스 전환](evidence/research-programs-20260928/activation-v2-server-start.json)을 확인했다.
  PostgreSQL과 다른 서비스의 컨테이너는 교체하지 않았다. 새 runtime 설정 SHA-256은
  `ad9a525f4a7bad7db53693a42844f49ca1a5a644e95a47b7daceb77447c5e239`, 프로필 SHA-256은
  `22a5fd7aa0fcd440730258f3a828720c04bf496ac28846354c84ee81e92d0779`다.
- 3070 최초 시작에서 systemd 작업 경로가 이전 디렉터리 구조를 가리켜
  [실패했다](evidence/research-programs-20260928/activation-v2-worker-path-repair.json). 해당 경로를
  준비된 후보 디렉터리로 수정한 뒤 [실행 영수증](evidence/research-programs-20260928/activation-v2-worker-start.json)과
  [최종 live 확인](evidence/research-programs-20260928/activation-v2-final-worker.json)에서
  새 커밋·설정·유닛·터널이 일치함을 확인했다. 이전 `2e1b5c78…` 릴리스도 registry에서 해석된다.
- 전환 중 보류한 release·backup timer는 [둘 다 복구](evidence/research-programs-20260928/activation-v2-timers-restored.json)했다.
  타이머 복구 후 [서버 최종 조회](evidence/research-programs-20260928/activation-v2-final-server.json)에서도
  정확한 이미지, 설정 해시, API 건강 상태와 운영 DB 상태가 유지됐다.

## 실제 경로와 승인 요청

- API `/healthz`와 Codex runtime liveness가 정상이다.
  [Codex CLI 인증 조회](evidence/research-programs-20260928/activation-v2-codex-auth.json)는
  ChatGPT 로그인을 표시했고 API key 사용을 표시하지 않았다.
  [Temporal Cloud system-info RPC](evidence/research-programs-20260928/activation-v2-temporal-connect.json)는
  실제 TLS 연결에서 성공했다.
- [잘못된 Slack 서명 요청](evidence/research-programs-20260928/activation-v2-slack-ingress-gate.json)은
  운영 ingress에서 HTTP 401로 거부됐다. 진짜 소유자 서명 이벤트의 적용은 소유자가 별도로
  승인할 때 확인된다. Codex 실제 모델 turn과 새 연구 과학 실행도 이번 활성화에서는 발생하지 않았다.
- 서비스의 `program_draft` 검증이 승인된 원문, 프로젝트 rev 5, 네 개의 고정된 ETF·주식
  envelope, 실행 프로필과 digest를 대조했다.
  [초안 등록 영수증](evidence/research-programs-20260928/activation-v2-program-submit.json)의 프로그램 ID는
  `e06537d3-fac3-5c8c-bf25-ddabb3c7e282`다. 누적 한도는 과학 시행 24회, 워커 계산시간
  36,000초, 미션 6개, 동시 미션 1개다.
- [발송 원장](evidence/research-programs-20260928/activation-v2-program-delivery.json)은 1회 발송 후
  `delivered`와 Slack timestamp `1790639676.639789`를 기록했다.
  [Slack 재조회](evidence/research-programs-20260928/activation-v2-slack-readback.json)는 같은 봇 메시지와
  승인·취소 버튼을 확인했다. [소유자 스레드의 승인 요청](https://achiisquantresearch.slack.com/archives/C0C2B9EUEGM/p1790639676639789?thread_ts=1789633942.673909&cid=C0C2B9EUEGM)을 열 수 있다.
- 최종 조회에서 프로그램 승인 이벤트는 `null`이고, 프로그램 과제·미션·자원 예약은 모두 0건이다.
  기존 미션의 누적 과학 시행은 1회 그대로다. 다음 결정은 해당 Slack 메시지에서 **프로그램 자체를
  승인하거나 취소**하는 것이다. 승인을 받은 경우에만 서비스가 위 한도 안에서 새 연구를 시작한다.

로컬 후보 검증은 `uv run pytest -q` **1,288 통과, 13 건너뜀**, `uv run ruff check .` 통과였다.
실제 PostgreSQL과 3070의 네 가지 자료 준비 검사를 사용했다. 준비 검사에서 과학 시행은 0회다.
