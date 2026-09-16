# AWS Lightsail 운영 준비와 복구

이 문서는 **배포할 수 있도록 준비한 설정과 실행 절차**다. 이번 변경에서 AWS 리소스,
Slack 앱, Temporal Cloud namespace를 만들거나 서버에 로그인하지 않았다. Docker daemon이
없어 컨테이너 실행·컨테이너 내 Codex 인증·서버 재부팅·S3 복원은 아직 검증하지 못했다.

## 1. 처음 사용할 구성

Linux Lightsail 4GB 한 대에서 API, Slack Socket 수신기, worker, dispatcher, PostgreSQL 16,
격리된 Codex 실행기를 운영한다. 기본 Slack 연결에는 도메인과 공개 HTTP endpoint가 필요 없다.
Socket 수신기가 Slack에 인증된 outbound WebSocket 연결을 유지한다. Caddy는 선택 기능이다.
[Slack Socket Mode](https://docs.slack.dev/apis/events-api/using-socket-mode/) 운영 workflow는 Temporal Cloud를 사용한다. 직원 10명 중 총괄·금융전략·
국내연구·데이터만 활성화하며, Codex는 한 번에 한 작업을 처리한다. 무거운 계산·학습은
이 서버의 업무가 아니다. 4GB는 시작용 자원 예산이며 부하 검증 뒤 유지/증설을 정한다.

| 프로세스 | 연결 | 영속 상태·권한 |
|---|---|---|
| Slack Socket 수신기 | core/서비스 egress → Slack | DB 앱 계정, Slack 자격증명; 공개 포트 없음 |
| API | edge/core, host 127.0.0.1:8000 | DB 앱 계정, Slack 자격증명·operator token |
| Caddy — 선택 `https` profile | public 80/443 → API | 인증서 저장, HTTP 수신을 선택할 때만 실행 |
| worker | core/model/서비스 egress | DB, Temporal API key, model runtime token |
| dispatcher | core/서비스 egress | DB, Temporal, Slack 발신 token |
| PostgreSQL | core 전용, 공개 포트 없음 | DB 볼륨, 별도 관리자 계정 |
| Codex runtime | model/전용 egress | Codex 계정 캐시·job 영수증·runtime token |

Codex 컨테이너에는 DB·Slack·AWS 자격증명, Docker socket, 연구 레이크나 사용자 홈을
마운트하지 않는다. worker만 두 내부 네트워크를 연결한다. 네트워크 구성은 도달 범위를
줄이는 장치이며, 완전한 인터넷 egress allowlist는 아니다. 실행기는 별도로 모델의 native
shell/MCP/app 도구를 금지하고 typed proposal만 회사 서비스에 돌려준다.
[Compose 네트워크](https://docs.docker.com/reference/compose-file/networks/)

API·Socket 수신기·worker·dispatcher·Codex·Caddy는 UID 10001, 읽기 전용 root filesystem, 제한된 메모리·
프로세스 수로 실행한다. PostgreSQL 공식 entrypoint는 초기 볼륨 소유권을 설정한 뒤
postgres 사용자로 실행하며, 앱용 `company` 계정에는 superuser 권한을 주지 않는다.

## 2. 계정과 입력값

- AWS: 기존 EC2/Insight-Invest가 있는 계정의 account ID, 리전/가용 영역, Lightsail SSH key
  pair, SSH 허용 공인 IPv4. 같은 AWS 계정에 별도 회사용 자원을 준비한다. 현재 읽기 조회에서는
  Lightsail key pair가 없었으므로 생성 전에 공개키 등록 또는 키 준비가 필요하다.
- 새 전용 Slack: workspace/team ID, 허용 사용자·채널 ID, 네 앱 각각의 bot token과
  `connections:write` 권한을 가진 app-level token(`xapp-…`).
- DNS·ACME 이메일·signing secret은 선택 HTTP 연결에서만 필요하다.
- Temporal Cloud: namespace, endpoint, 해당 namespace용 API key. 이 설정은 TLS를 사용한다.
- Codex: 개인 구독의 공식 device login. 다른 직원 수만큼 별도 사용량이 생기지 않는다.

비밀 값은 채팅·Git·Docker image에 넣지 않는다. 운영자는 호스트의 secret 파일을 직접
설정한다. `deploy/.env`에는 ID·주소·한도·파일 위치만 둔다.

## 3. 리소스 계획 검토와 생성

[lightsail.json](../deploy/lightsail.json)은 인스턴스 1대, 고정 IPv4, 비공개 암호화·버전 관리
S3 백업 버킷을 정의한다. 기본 inbound 규칙은 operator `/32`의 SSH(TCP 22)뿐이다.
80/443은 `EnableHttpsIngress=true`를 명시했을 때만 열린다.
기존 EC2 콜렉터와 Insight-Invest 서비스에는 변경을 가하지 않는다.
[AWS Lightsail CloudFormation](https://docs.aws.amazon.com/AWSCloudFormation/latest/TemplateReference/aws-resource-lightsail-instance.html)

서비스 폴더에서 실행하는 아래 명령은 **계획만 출력**하며 AWS에 접속하지 않는다.
예시 식별자는 실제 계정 값으로 바꾼다.

```bash
python3 deploy/provision.py \
  --account-id 123456789012 --region ap-northeast-2 --zone ap-northeast-2a \
  --key-pair quant-company --ssh-cidr 192.0.2.1/32 \
  --bucket example-quant-company-backups
```

실제 생성 전 `aws lightsail get-blueprints`와 `get-bundles --no-include-inactive`로
리전의 Ubuntu blueprint·4GB bundle·현재 가격을 확인한다. 기본 `ubuntu_24_04`와
`medium_3_0`은 검토할 시작값이며, 여기서 가용성을 확인했다고 주장하지 않는다.
자원·가격 검토 후 같은 명령에 `--apply`를 추가하면 실제 비용이 발생한다. apply는
`sts get-caller-identity`의 account가 지정한 값과 같을 때만 CloudFormation을 실행한다.

도메인을 마련한 뒤 HTTP 수신을 선택하려면 계획 명령에 `--enable-https-ingress`를
추가하고 변경 내용을 검토한다. 기본 Socket Mode 운영에는 이 옵션을 넣지 않는다.

스택 삭제 시 인스턴스·고정 IP·백업 버킷은 **보존**된다. 데이터 보존을 위한 설정이며,
사용을 끝낼 때는 보존된 자원까지 확인해 제거해야 청구가 종료된다.

## 4. 호스트에 승인된 릴리스 배치

Docker Engine/Compose v2+, Python 3, AWS CLI를 설치한 Ubuntu 호스트가 필요하다.
Docker 설치는 [공식 Ubuntu 절차](https://docs.docker.com/engine/install/ubuntu/)를 따른다.
자동 설치 스크립트나 전체 Quant 레포·데이터 레이크 동기화는 포함하지 않았다.

커밋된 `services/quant-company` 디렉터리와 `uv.lock`을 `/opt/quant-company/current`에
배치한다. 다음 명령의 작업 디렉터리는 이 서비스 디렉터리다.

```bash
python3 deploy/prepare-state.py
sudo python3 deploy/prepare-state.py --apply
sudo ln -s /var/lib/quant-company/config/runtime.env deploy/.env
```

`prepare-state`는 기존 파일을 덮어쓰지 않는다. `.env`는 코드 릴리스 밖의 영속 설정을
가리킨다. `deploy/.env`의 `RELEASE_COMMIT`에는
배치한 코드의 40자리 Git commit을 기록하고 Slack·Temporal 값을 채운다.
기본 Socket Mode에서는 주석 처리된 도메인·ACME 항목을 채우지 않아도 된다.
`/var/lib/quant-company/secrets`에서 다음 파일을 설정한다.

| 파일 | 설정 방법 |
|---|---|
| `database_password`, `database_admin_password` | 최초 준비 시 난수 생성; 기존 DB 비밀번호 변경은 별도 DB 작업 |
| `operator_token`, `model_runtime_token` | 최초 준비 시 난수 생성; API와 모델 token 분리 |
| `temporal_api_key` | 실제 namespace API key로 교체 |
| `slack-credentials.json` | 네 역할의 app ID/bot user ID/bot token/app token 입력; signing secret은 HTTP에서만 필요 |

호스트의 secrets 디렉터리는 root만 접근한다. 개별 파일은 명시적으로 마운트된 컨테이너의
비 root 프로세스가 읽을 수 있도록 준비한다. 단일 호스트 Compose의 secret 파일은 별도의
암호화 secret manager가 아니다. AWS 백업 자격증명은 호스트에만 두고 컨테이너에 전달하지 않는다.

```bash
sudo docker compose --env-file deploy/.env -f deploy/compose.yaml config --quiet
sudo docker compose --env-file deploy/.env -f deploy/compose.yaml build api codex-runtime
```

Python 의존성은 committed `uv.lock`과 `uv sync --frozen`으로 설치하고, Codex CLI는
`@openai/codex@0.154.0` 및 빌드 시 버전 검사로 고정한다. Python/Node/Caddy의 기본 image
태그는 준비용이다. 실제 자격 검증 때 사용할 image digest를 기록·고정하고 동일 이미지를
배포한다. Docker daemon이 없는 이 개발 환경에서는 image pull/build를 검증하지 못했다.

## 5. Codex 로그인과 서비스 시작

서버에서 한 번 실행하고 사용자가 자신의 브라우저로 device code 인증을 마친다.
이 단계 전에 개인 ChatGPT 보안 설정에서 device login이 허용되어야 한다. 인증 정보는
컨테이너의 `/state/auth`에 연결된 호스트 전용 디렉터리에 유지된다.
[OpenAI 공식 인증 문서](https://learn.chatgpt.com/docs/auth)

```bash
sudo docker compose --env-file deploy/.env -f deploy/compose.yaml run --rm --no-deps \
  --entrypoint codex codex-runtime \
  -c 'cli_auth_credentials_store="file"' -c 'forced_login_method="chatgpt"' login --device-auth
sudo docker compose --env-file deploy/.env -f deploy/compose.yaml run --rm --no-deps \
  --entrypoint codex codex-runtime login status
sudo docker compose --env-file deploy/.env -f deploy/compose.yaml up -d postgres
sudo docker compose --env-file deploy/.env -f deploy/compose.yaml run --rm --no-deps api quant-company migrate
sudo docker compose --env-file deploy/.env -f deploy/compose.yaml up -d
```

`login status`가 ChatGPT 인증인지 확인한다. API key로 자동 전환하는 경로는 제공하지 않는다.
Linux의 Codex read-only sandbox가 호스트 kernel/seccomp 환경에서 동작하는지도 실제 작은
요청으로 확인한다. 실패하면 sandbox를 해제해 운영을 시작하지 않는다.

Slack manifest는 개발 단말의 서비스 폴더에서 다음과 같이 생성한다. 기본 transport는
Socket Mode이며 Request URL을 지정하지 않는다. 생성된 네 manifest를 새 workspace에
각각 설치하고, 각 앱의 Basic Information → App-Level Tokens에서 `connections:write`
권한을 가진 token을 생성해 `app_token` 필드에 넣는다. bot token과 app-level token은
서로 다른 값이다. Socket Mode 및 Event Subscriptions를 활성화하고 DM 메시지 탭을 열어 둔다.
[공식 Slack Socket 설정](https://docs.slack.dev/tools/python-slack-sdk/socket-mode/)

```bash
uv run quant-company slack-manifests --output .local/slack-manifests
```

앱을 허용 채널에 초대한 뒤 DM·멘션으로 실제 왕복을 확인한다. Socket 연결에서는 인증된
WebSocket과 앱·workspace·사용자·채널 검사가 수신 경계를 이룬다. DB 저장 후 envelope를
ACK하며, HTTP용 signing secret 검증과 구분한다. 연결 재수립·재전송 중에도 업무가 중복
생성되지 않는지 실제로 확인해야 한다. Socket 수신기는 모델·Temporal·operator·AWS secret을
받지 않는다. 운영 API는 SSH tunnel로 호스트 `127.0.0.1:8000`에 연결한 뒤 operator token으로 쓴다.

### 선택: 도메인을 마련한 뒤 HTTP 연결 사용

`COMPANY_DOMAIN`·`ACME_EMAIL`을 설정하고 DNS가 고정 IP를 가리키게 한다. AWS 계획의
`--enable-https-ingress`로 TCP 80/443을 명시적으로 열고 `https` profile의 Caddy를 시작한다.
Caddy는 operator `/v1/*`를 공개하지 않는다. HTTP manifest에는 역할별 Request URL과
서명 검증에 사용할 signing secret이 필요하다.

```bash
uv run quant-company slack-manifests --transport http \
  --base-url https://office.example.com --output .local/slack-manifests-http
sudo docker compose --env-file deploy/.env -f deploy/compose.yaml stop slack-socket
sudo docker compose --env-file deploy/.env -f deploy/compose.yaml --profile https up -d caddy
```

HTTP로 전환한 앱의 Socket Mode를 끄고 manifest/Request URL을 변경한다. 이후 HTTP 운영의
전체 서비스 시작은 `--profile https up -d postgres api worker dispatch codex-runtime caddy`처럼
대상을 명시해 Socket 수신기를 다시 시작하지 않는다. 기본 `up -d`는 Socket Mode 구성이다.
실제 HTTP 전환·앱 설정 변경·인증서 발급은 이번 구현에서 수행하지 않았다.

## 6. 백업·복원

[backup.sh](../deploy/backup.sh)는 기본적으로 계획만 보여 준다. `--apply`는 실행 중인
Socket 수신기/API/worker/dispatcher/Codex를 잠시 멈추고, PostgreSQL `pg_dump`와 비밀 값 없는 운영 설정·역할 설정·Codex job
영수증을 같은 정지 구간에서 복사한다. DB뿐 아니라 중복 호출 방지 영수증도 함께 보존한다.
그 뒤 원래 실행 중이던 프로세스를 재개하고 SHA-256 manifest가 있는 묶음을 S3에 올린다.
Codex 인증 캐시·Slack token·DB password는 이 묶음에 포함하지 않는다.
[PostgreSQL pg_dump](https://www.postgresql.org/docs/16/app-pgdump.html)

```bash
sudo bash deploy/backup.sh --s3-uri s3://example-quant-company-backups/company/
sudo bash deploy/backup.sh --s3-uri s3://example-quant-company-backups/company/ --apply
```

호스트 AWS profile에는 [백업용 정책 예시](../deploy/backup-policy.example.json)의 버킷·prefix
범위만 부여한다. IAM identity와 자격증명은 자동 생성하지 않는다. 백업을 위한 root 계정
key나 포괄적인 관리자 key를 호스트에 넣지 않는다.

`quant-company-backup.service`와 `.timer`를 `/etc/systemd/system/`에 배치하고
`backup.env.example`을 `/etc/quant-company/backup.env`로 설정한 뒤 timer를 활성화한다.
기본 일정은 19:30 UTC(다음 날 04:30 KST), 최대 5분 지연이다. 백업 중 Slack 연결에 짧은
중단이 생길 수 있다. 재접속 및 누락 가능 구간 확인도 복구 검증에 포함한다. 이 단일 호스트 구성은 무중단 고가용성이 아니다. timer 실패와 디스크
용량을 운영자가 확인한다. S3 lifecycle은 30일이며 로컬 백업은 자동 삭제하지 않으므로
외부 복사·복원 확인 후 용량을 관리한다.

복원할 묶음과 `.sha256` 파일을 S3에서 같은 디렉터리로 내려받은 뒤 다음을 실행한다.

```bash
sudo bash deploy/restore.sh --archive /path/company-backup.tar.gz --database restore_drill
sudo bash deploy/restore.sh --archive /path/company-backup.tar.gz --database restore_drill --apply
```

복원은 `restore_*`라는 **새 DB만 생성**한다. 대상이 존재하거나 현재 live DB 이름이면
중단한다. hash·archive 경로를 확인하고 `pg_restore --single-transaction --exit-on-error`로
실패 시 부분 테이블 복원을 막는다. 실패해서 생긴 빈 검증 DB도 임의로 지우지 않는다.
[PostgreSQL pg_restore](https://www.postgresql.org/docs/16/app-pgrestore.html)

스크립트는 운영 DB, 현재 역할 파일, Codex 영수증, 회사 서비스를 자동 전환하지 않는다.
출력한 임시 복원 경로에서 기록된 commit/역할/영수증을 확인하고, 격리한 DB로 업무·기억을
검증한다. 실제 전환 전에는 외부 Slack 게시·진행 중 Temporal 업무·불확실한 Codex 호출을
대사해야 한다. 과거 DB로 돌아간 것만으로 이미 발생한 외부 효과가 되돌아가지는 않는다.
자격증명은 별도 비밀 저장 경로에서 다시 설정하고 Codex는 필요하면 재로그인한다.

## 7. 배포 전 실제 확인할 항목

- `/healthz`: 회사 API는 DB `SELECT 1`을 검사한다. schema·모델 준비 상태까지 보장하지 않는다.
  Codex health는 프로세스 응답 확인이다.
- Temporal Cloud 연결·poller와 실제 typed model turn, 네 앱의 Socket 연결·envelope ACK·각 직원의 게시.
- Socket 재연결·동일 이벤트 재전송·DB 장애 시 ACK 보류 및 복구. 선택 HTTP는 별도로 서명 검증.
- 호스트 재부팅 후 볼륨·역할·기억 보존, quota/auth 오류 시 업무 보존과 대기.
- 맥북을 종료한 상태에서 휴대폰의 요청 → 직원 간 위임 → 결과 수신.
- 외부 백업으로 새 DB에 복원하고 pending/uncertain 효과를 중복 실행하지 않는 복구.
- 메모리·디스크·회사 공용 한도 측정 후 4GB 유지 여부 결정.

현재 완료한 것은 role/schema 검사, 기본 Socket/선택 HTTPS Compose 설정 파싱,
공개 포트 opt-in·네트워크·secret 경계 검사, 백업 시 Socket 수신기 정지·재개 순서 검사,
archive 무결성/경로 검사와 기존 DB 복원 거부 검사다. 정지·재개 검사는 명령 모형을 사용했으며,
실제 WebSocket 연결·컨테이너 정지 시험은 통합 검증에서 수행해야 한다. 이는 서버에 배포됐다는
증거가 아니다. 실제 수행 기록은 통합 검증 receipt에 별도로 남긴다.
