# Claude 구독 독립 검토 실행기

공식 Pro/Max 로그인으로 Opus 5를 호출하는 선택 기능이다. 기본값은 비활성화이며, 회사의
기존 직원 개발 루프가 `staff_independent_reviews`에 보관한 답안만 검토한다. 결제 API 키가
필요하지 않다. 현재 배포 이미지는 검증한 Linux x86_64 바이너리의 SHA-256까지 고정한다.

## 최초 연결

1. 계정 소유자가 Claude 설정에서 usage credits/extra usage를 끈다. 이는 계정 설정이므로
   실행기가 자동 확인/변경할 수 없다. 별도 API 환경변수나 OAuth token 문자열을 전달하지 않는다.
2. `deploy/prepare-state.py --apply`로 전용 호스트의 `/var/lib/quant-company/claude/auth`와
   `claude/jobs`를 UID 10001, mode 0700으로 준비한다. 승인된 exact commit을 빌드한다.
3. 호스트의 대화형 터미널에서 아래 로그인 명령을 실행한다. 출력된 공식 URL에서 계정을
   승인하고 필요한 코드를 그 터미널에만 입력한다. 로그인 출력/인증 파일을 Git·채팅에 복사하지 않는다.

```bash
sudo docker compose --profile claude --env-file /var/lib/quant-company/config/runtime.env \
  -f deploy/compose.yaml run --rm --no-deps claude-runtime claude auth login
```

같은 환경에서 `claude auth status`의 loggedIn/authMethod/subscriptionType만 확인한다.
`claude.ai` 및 `pro`/`max`여야 한다. 일회성 로그인 컨테이너가 쓴 인증 디렉터리를 실행기에도
동일하게 마운트한다. `--bare`와 `--console` 인증은 사용하지 않는다.

## 활성화와 운영

계정 소유자의 확인 후 비밀 값 없는 `runtime.env`에 다음 설정을 저장한다.

```dotenv
COMPANY_STAFF_REVIEW_ENABLED=true
STAFF_REVIEW_DAILY_LIMIT=2
CLAUDE_USAGE_CREDITS_DISABLED_CONFIRMED=true
CLAUDE_MEMORY_LIMIT=512m
```

`--profile maintenance --profile claude`로 claude-runtime 및 새 app 이미지를 시작한다.
worker의 기존 비공개 bearer token으로 호출하고 Claude 계정 인증은 Codex와 분리한다.
Claude에는 DB·Slack·AWS·GitHub key와 Docker socket이 없다. 모델 자식 프로세스에도
bearer token을 넘기지 않는다. `/healthz`는 HTTP 생존 확인으로 인증/구독 확인을 대신하지 않는다.

`staff_status`의 independent_review에 결과와 오류가 보인다. 아직 보정되지 않은 평가이므로
직원 등급/절차 자동 승격에는 사용하지 않는다. quota/auth 장애는 업무 능력의 실패가 아니다.
과제 원문/답안/권한이 달라졌거나 허위 인용/다른 모델/도구 실행이 감지되면 결과 저장을 막는다.

완료 결과는 원래 request ID로 복구한다. `running`/`failed` receipt가 남은 불확실한 호출은
새 ID로 재시도하지 말고 해당 DB 행과 실행기 receipt를 먼저 대사한다. 직원 개발을 끄면
정기 검토도 멈춘다. 검토만 끄려면 `COMPANY_STAFF_REVIEW_ENABLED=false`를 반영하고
진행 중 호출이 끝난 후 claude-runtime을 정지한다. 계정의 추가 사용량 설정을 바꿀 때도 먼저 끈다.

정기 백업은 Claude까지 정지하고 `claude-jobs/` 영수증을 DB와 함께 저장한다. 인증은 제외한다.
복구 시 DB와 두 제공자의 영수증을 함께 대사한 뒤 활성화한다. 이전 Claude 없는 백업도 읽을 수 있다.
유지보수 release executor는 활성화된 Claude 이미지도 갱신하고 동일 commit/상태를 검사한다.

2GB 기본 구성의 1664 MiB 정적 합계에는 이 profile과 maintenance가 들어 있지 않다.
컨테이너 상한은 예약 용량이 아니므로 실행 중 가용 메모리/OOM을 확인한다. 새 서버·상위 요금제로
자동 변경하지 않는다. 구독 한도는 개인 Claude 사용과 공유하며 고정된 일일 호출량을 보장하지 않는다.
