# 새 전용 Slack 연결

기본값은 **Socket Mode**입니다. 회사 서버가 Slack에 인증된 WebSocket을 열기 때문에
도메인이나 공개 HTTPS endpoint 없이 이벤트를 받습니다. 앱별로 연결을 유지하고,
업무를 DB에 기록한 뒤 수신 확인을 보냅니다.
[Slack 공식 Socket Mode 안내](https://docs.slack.dev/apis/events-api/using-socket-mode/)

## 준비한 앱

| 직원 | 설치용 manifest |
|---|---|
| 총괄 | [director.json](../slack-apps/director.json) |
| 금융전략 | [financial_strategist.json](../slack-apps/financial_strategist.json) |
| 국내시장 연구 | [researcher_kr.json](../slack-apps/researcher_kr.json) |
| 데이터 | [data.json](../slack-apps/data.json) |

새 workspace를 만들면 [Slack 앱 관리](https://api.slack.com/apps)에서 **Create New App →
From a manifest**로 각 파일을 적용하고 workspace에 설치합니다. 같은 bot의 표시명만 바꾸는
방식이 아니라 네 개의 앱 identity입니다.

각 앱의 Basic Information에서 App-Level Token을 만들고 `connections:write` scope를
부여합니다. OAuth & Permissions의 bot token과 함께 서버의 비밀 파일에 입력합니다.
토큰 원문은 채팅이나 Git으로 공유하지 않습니다.
[공식 Python SDK 설정](https://docs.slack.dev/tools/python-slack-sdk/socket-mode/)

## 계정·채널 설정

- `SLACK_TEAM_ID`: 새 workspace ID.
- `SLACK_ALLOWED_USERS`: 처음에는 본인 Slack user ID 하나의 JSON 배열.
- `SLACK_ALLOWED_CHANNELS`: 회사 업무용 채널 ID 배열. 그 채널에 네 앱을 초대합니다.
- `/var/lib/quant-company/secrets/slack-credentials.json`: 역할별
  `app_id`, `bot_user_id`, `bot_token`, `app_token`. HTTP를 사용할 때만 `signing_secret`도 필요합니다.

앱·workspace·user·channel 값이 맞지 않는 이벤트는 업무로 만들지 않습니다. bot echo도 무시합니다.
전용 workspace의 무료 요금제로 네 앱 연결을 먼저 확인할 수 있습니다. 회사 업무의 원본은 DB에
있으므로 Slack 조회 범위가 회사 기억을 결정하지 않습니다.

## 첫 연결 확인

1. AWS 서비스와 Temporal 연결, 서버 Codex device login을 먼저 확인합니다.
2. `slack-socket`과 dispatcher를 시작합니다. 각 앱의 인증·연결 오류 여부를 확인합니다.
3. 본인 계정으로 총괄에게 합성 과제를 보내 네 직원의 위임·도구 결과·결론을 확인합니다.
4. 같은 스레드에서 `상태`, 이어서 `수정: ...`를 보내 새 지시가 반영되는지 확인합니다.
5. 프로세스 재시작, 맥북 종료 후 휴대폰 왕복, 외부 백업 복원을 인수 기록에 남깁니다.

설정 파일과 테스트는 준비됐지만 실제 workspace에서 이 절차를 실행한 상태는 아닙니다.
