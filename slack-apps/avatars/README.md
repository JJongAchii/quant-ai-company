# Slack 직원 프로필

직원 앱과 발송 전용 Tech Scout·Quant Scout의 프로필 이미지. 모두 같은 세라믹 로봇 스타일을 쓰고
배경색·눈·직무 상징으로 역할을 구분한다. 원본 PNG는 각각 1254×1254이며 작은 Slack
아이콘에서도 얼굴과 역할 표식을 알아볼 수 있다.

| 총괄 | 금융전략 | 국내시장 연구 | 데이터 |
| --- | --- | --- | --- |
| ![총괄](director.png) | ![금융전략](financial_strategist.png) | ![국내시장 연구](researcher_kr.png) | ![데이터](data.png) |
| 네이비·금색 / 나침반 | 초록·금색 / 차트 | 파랑·은색 / 돋보기 | 보라·티타늄 / 데이터베이스 |

| Tech Scout |
| --- |
| ![Tech Scout](tech_scout.png) |
| 네이비·청록 / 청록 눈·레이더·나침반·발견 점 |

Quant Scout: 버건디·브론즈 / 앰버 눈·펼친 연구 서적과 데이터 문양.
`quant_scout.png`는 Tech Scout·국내 연구원 두 이미지를 참조해 2026-09-22 built-in image_gen으로 생성했다.
이미지 파일 생성과 실제 Slack 업로드 검증은 별도다.

## 원본과 적용

- 직원 프로필은 2026-09-17, Tech Scout는 2026-09-22 built-in `image_gen`으로 생성했다.
  [최종 프롬프트](prompts.json)를 함께 보존한다.
- Slack App 관리 → 해당 앱 → Basic Information → Display Information → App icon에서 해당 PNG를 업로드한다.
- 현재 Slack UI에서는 아이콘 업로드가 즉시 저장된다. 새로고침과 기존 메시지의 `bot_profile.icons` 조회로 적용을 확인한다.
- 이 디렉터리에는 공개 이미지와 생성 프롬프트만 두며 Slack 토큰은 저장하지 않는다.
- 개선 서비스는 총괄 앱을 통해 보고한다. 별도 Slack 앱이 없는 직무의 계정 생성·운영 활성화는 이 이미지 변경에 포함하지 않는다.

적용 기록: [Slack 프로필 검증](../../docs/project/evidence/slack-bot-avatars-20260917.json).
