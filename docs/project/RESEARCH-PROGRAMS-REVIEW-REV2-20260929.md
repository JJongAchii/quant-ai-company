# 자료 기반 연구 프로그램: 변경된 운영 기준 재검토

상태: **새 릴리스 후보 준비 완료, 소유자의 정확한 재승인 대기**. 운영 서버 DB·서비스·프로필과
3070 활성 설정은 이 작업에서 변경하지 않았다. 프로그램 승인 요청은 Slack에 게시하지 않았고,
과학 trial은 새로 시작하지 않았다. 기계 판독 가능한 값은
[재검토 패킷](evidence/research-programs-20260928/next-release-packet.json)에 있다.

## 재검토가 필요한 이유

소유자가 승인한 이전 배포 후보는 회사 커밋 `2b4034db126ff13ff936c8e3def3ae89c7456824`였다.
활성화 중 3070의 회사 코드가 별도 과제 한도 수정 커밋 `2e1b5c783319c69e384a4a37c79af24a5441c422`로
바뀌었다. 설정과 systemd 작업 경로의 변경 시각은 2026-09-28 08:30:25 UTC다.
[실제 프로세스 확인](evidence/research-programs-20260928/activation-worker-drift.json) 후
이전 배포를 중단했다. 서버에서 기존 서비스와 DB는 그대로이며 원래 후보 이미지는 비활성 상태다.

새 후보 `382284512ade2c0750c79494389a0899d2b46688`는 기존 연구 프로그램 코드에
3070에서 활성화된 과제 한도 변경을 포함한다. 연구 실행·프로필 코드의 변경은 없다.
날짜가 바뀌면 실패하던 주택 공고 테스트의 시계를 고정했다. 새 후보의 소스 변경은
`company.py`, `config.py`, `task_control.py`와 관련 테스트 파일에 한정된다.

## 새 후보와 현재 운영 기준

| 항목 | 값 |
| --- | --- |
| 새 회사 코드 | `382284512ade2c0750c79494389a0899d2b46688` |
| 회사 Git bundle SHA-256 | `cbf3d92af2e77d3ccad4493d5532735ececf06a858ef7c644332d397b5579cf3` |
| quant-data 커밋 / 원본 archive SHA-256 | `d6d7d0ed066ec49541e9acdd657c9ec5692ffc52` / `8bb50549a923f3ec39bb1e6ffce2ac182ed41d368275614e55f60686707a138c` |
| 정본 연구 bundle SHA-256 | `44fb48c8d4b299e66009a01e5b026a0d0b886cfa92bee25db9304607a1f09b49` |
| 서버 프로필 후보 SHA-256 | `22a5fd7aa0fcd440730258f3a828720c04bf496ac28846354c84ee81e92d0779` |
| 3070 후보 설정 SHA-256 | `63337bd726e3b53502d8df282523df9c4a97b9f737b2f2d46c5db1b11598eae8` |
| 현재 3070 코드 / 설정 SHA-256 | `2e1b5c783319c69e384a4a37c79af24a5441c422` / `9741cdeec6b9a26c7e4ed15e7e7d39d25081d44fb34c6eaead84a7894fa058e6` |
| 첫 프로그램 digest | `53392822414ca32e89fab0f3a9a1093a350196345bd13084654a45510297159b` |

[서버 코드 준비](evidence/research-programs-20260928/next-server-stage.json)는 회사 bundle을 검증하고
기존 quant-data archive의 149개 파일과 새 빌드 컨텍스트가 일치함을 확인한다.
[이미지 준비](evidence/research-programs-20260928/next-image-stage.json)는 앱·자율연구 워커 두 이미지의
revision label과 설치된 핵심 소스 해시를 확인한다.
[Compose 검사](evidence/research-programs-20260928/next-compose-preflight.json)는 현재 사용 중인
research·autonomous·model accounts overlay를 새 커밋으로 해석해 통과했다.
[역할 파일 검사](evidence/research-programs-20260928/next-roles-preflight.json)는 새 이미지가
현재 운영의 13개 직원 역할 설정을 그대로 읽는 것을 확인했다.

[3070 준비](evidence/research-programs-20260928/next-worker-stage.json)는 새 회사 bundle을 정확한
코드·설정 쌍으로 풀었다. [네 가지 실제 3070 준비 구간 검사](evidence/research-programs-20260928/next-worker-warmup.json)는
ETF·주식의 전략·예측 주장 유형 모두 통과했고 개발구간 입력을 마운트하지 않았다.
과학 trial은 0회다. [3070 복구 기준](evidence/research-programs-20260928/next-worker-baseline.json)에
현재 활성 설정과 systemd drop-in의 해시를 기록하고 개인 경로에 복사했다.

`uv run pytest -q`는 **1,288 통과, 13 건너뜀**이고 `uv run ruff check .`도 통과했다.
검사는 실제 PostgreSQL을 사용했다. 새 코드의 운영 Temporal·Codex·서명된 Slack 왕복은
활성화 전에는 증명할 수 없으므로 배포 후 별도 인수 검사를 거쳐야 한다.
[최신 서버 조사](evidence/research-programs-20260928/next-live-server-audit.json)에서는 API가
`d571734e…`, 서버 연구 워커가 `31ff903f…`이고 새 프로그램 테이블은 아직 없다.
기존 연구 미션의 누적 trial은 1회다. [3070 재조사](evidence/research-programs-20260928/next-live-worker-audit.json)는
활성 커밋 `2e1b5c78…`과 새 후보가 별도임을 확인한다.

## 재승인 후 순서

1. 위 서버·3070의 정확한 활성 해시와 진행 중 실행·발송을 다시 확인한다. 달라지면 멈춘다.
2. 기존 백업 도구의 정지 목록에 없는 `housing-feed-worker`를 먼저 안전하게 멈춘 뒤
   운영 PostgreSQL·설정·연구 영수증을 백업한다. 해당 워커를 다시 시작하고, 3070의 현 활성
   설정과 systemd unit의 개인 복구 사본을 대조한다. 백업 실패 시 멈춘다.
3. additive migration 후 새 서버 프로필과 앱·Slack socket·Temporal worker·dispatch를
   `3822845` 이미지로 전환한다. 나머지 서비스와 기존 미션 영수증은 유지한다.
4. 3070의 준비된 코드·설정 쌍을 release registry에 넣고 poller의 작업 경로를 같은 커밋으로
   바꾼다. 기존 `2e1b5c78…`은 복구 가능한 이전 release로 남긴다.
5. 실제 API·Codex 구독 인증·Slack 서명 ingress·Temporal 경로와 역할별 수신을 확인한다.
   그다음 [동일한 첫 프로그램 명세](evidence/research-programs-20260928/first-program-draft.json)를
   소유자 Slack 승인 버튼으로 게시하고 전달 영수증을 읽는다.

요청하는 결정은 **새 회사 커밋 `382284512ade2c0750c79494389a0899d2b46688`의 배포와
기존 프로그램 digest `53392822…`의 승인 요청 게시**다. 프로그램 자체의 과학 trial 권한은
별도 서명된 Slack 소유자 승인 전까지 생기지 않는다. 전역 상한은 과학 trial 24회,
계산시간 36,000초, 미션 6개, 동시 미션 1개다.
