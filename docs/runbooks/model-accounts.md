# Slack에서 회사 공용 Codex 계정 선택

## 사용 명령

`MODEL_ACCOUNTS_OWNER_USER`에 등록된 사용자가 **총괄의 DM**에 다음 문장을 보낸다.
명령은 모델을 호출하지 않고 처리하므로 Codex 한도 소진 중에도 사용할 수 있다.

| 명령 | 동작 |
|---|---|
| `모델 계정 상태` | 현재 선택, 두 계정의 로그인 상태, 관측된 한도 대기 확인 |
| `모델 계정 예비로 전환` | 등록된 예비 계정 선택 |
| `모델 계정 기본으로 전환` | 기본 계정 선택 |

회사 전체 Codex 작업에 적용한다. 로그인 상태는 사용 잔량이 아니다. 다음 실제 요청이
한도에 걸리면 해당 계정의 작업을 대기시킨다. 재시도 시각은 서비스 대기 종료 시각이며
구독 한도 초기화 시각을 뜻하지 않는다. 계정 선택은 자동으로 바뀌지 않는다.

완료된 작업은 재실행하지 않는다. 진행 중인 요청은 기존 계정에서 끝낸다. 명시적인
한도 거절로 대기하던 일반 업무에는 재개 신호를 보내고, 뉴스·직원 점검·유지보수는
기존 주기에 맞춰 재개한다. 결과가 불확실하거나 취소된 요청은 전환 후에도 보존한다.
첫 배포 전에 이미 시작된 Temporal 대기 타이머는 남은 시간을 마친 뒤 새 재개 신호 방식을
채택한다. 이 경우 최초 전환은 기존 한도 대기 시간만큼 늦게 적용될 수 있다.

최초 `모델 계정 상태` 명령으로 알림을 받을 총괄 DM 스레드를 등록한다. 이후 선택된
계정에서 한도 오류가 발생하면 해당 스레드에 전환 안내를 한 번 남긴다. Slack 전송
결과가 불확실한 경우에는 기존 outbox 절차로 대사하며 무조건 재전송하지 않는다.

## 최초 설치와 로그인

1. 현재 운영 release·Compose overlay·실행 중 작업을 확인하고 변경 커밋과 통합한다.
   기존 뉴스/연구/데이터 워커 설정을 예제 파일로 덮어쓰지 않는다. 평소 배포 잠금과
   백업 절차를 사용하고, 이미 실행 중인 모델 호출이 끝난 뒤 runtime을 교체한다.
2. `prepare-state.py --apply`로 `codex/backup-auth`를 준비한다. UID/GID 10001,
   권한 0700이며 기존 `codex/auth`는 기본 계정으로 유지한다. 새 Compose는 두 디렉터리를
   각각 `/state/auth`, `/state/backup-auth`에 mount하고 jobs 디렉터리는 공유한다.
3. 기능을 끈 상태에서 새 runtime/app 이미지를 배포하고 `quant-company migrate`를 실행한다.
   새 테이블은 추가 방식으로 생성한다. 실제 Compose env 경로는 운영 설정을 사용한다
   (기존 호스트: `/var/lib/quant-company/config/runtime.env`).
4. 회사 호스트의 대화형 터미널에서 실행한다.

   ```sh
   bash deploy/codex-account-login.sh backup
   ```

   공식 기기 인증 주소를 **사용자가 브라우저에서** 열어 예비 ChatGPT 계정으로 로그인한다.
   장치 인증 기능이 필요한 경우 해당 계정 설정에서 활성화한다. 코드·토큰·인증 파일을
   Slack, Git, 테스트 증거에 넣지 않는다. 이 스크립트는 로그인만 수행하고 회사 선택을 바꾸지 않는다.
   기존에 사용 중인 프로필을 재인증할 때는 해당 프로필의 실행을 먼저 마무리한다.
5. `bash deploy/codex-account-login.sh backup status`로 확인한다. API key 인증은 지원하지 않는다.
   이메일·계정 ID를 알림이나 증거에 기록할 필요는 없다.
6. 기존 운영 env에 `MODEL_ACCOUNTS_ENABLED=true`, `MODEL_ACCOUNTS_OWNER_USER=<Slack user ID>`를
   설정한다. 해당 사용자는 `SLACK_ALLOWED_USERS`에도 있어야 한다. api/socket/dispatch/worker/
   news-worker/maintenance 등 기존 Compose 서비스에 동일하게 적용한다. Slack 프로세스에
   모델 runtime token을 추가하지 않는다.
7. 실제 소유자가 총괄 DM에서 상태 → 예비 전환을 보낸다. 적용 기록과 다음 정상 업무 응답을
   확인한다. 로그인 확인만으로 실제 구독 모델 검증을 완료했다고 기록하지 않는다.

## 복구와 롤백

- 서비스 재시작은 DB의 선택과 요청 binding을 유지한다. 계정 로그인 정보도 host volume에 남는다.
- 대상 로그인 확인 실패, 권한 변경, 대상의 한도 대기 중에는 선택을 바꾸지 않는다.
- 로그인이 만료되면 선택하지 않은 프로필을 먼저 기기 인증한 후 Slack에서 선택한다.
- 기능을 끄거나 이전 release로 롤백하려면 먼저 기본 계정을 선택하고 모든 실행을 마무리한다.
  기본 계정이 한도로 대기 중이면 조기 전환은 거절되므로 대기가 끝날 때까지 기다리거나
  모델 작업을 중단한 상태로 운영자 대사를 수행한다. 플래그만 끄면 옛 클라이언트는 기본
  계정으로 요청하므로 실행 중에 단순히 끄지 않는다. DB/receipt/transfer archive는 삭제하지 않는다.
- 인증 상태 조회는 `/v1/accounts`, 실행 선택은 private HTTP headers로만 전달한다.
  임의 `CODEX_HOME`, shell 명령, Docker 제어는 Slack에서 허용하지 않는다.

[공식 인증 문서](https://learn.chatgpt.com/docs/auth),
[설계](../adr/0037-owner-selected-codex-accounts.md),
[검증 범위](../project/MODEL-ACCOUNTS-VALIDATION.md).
