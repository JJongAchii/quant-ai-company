# 0033: tech-feed 전용 Tech Scout identity

날짜: 2026-09-22. 상태: 채택.

tech-feed의 출처·형식·주기가 hot-news와 다르므로 Reporter를 공유하면 채널에서 발신자를 보고
서비스 경계를 구분하기 어렵다. 기존 피드의 PostgreSQL·Temporal·outbox 상태는 유지하되 Slack
발신 identity만 **Tech Scout**로 분리한다.

Tech Scout는 모델 직원이 아니다. 패키지 roster에는 inactive delivery identity로만 등록하고,
turn·tool·delegation·Socket Mode 대상에서 제외한다. Slack 앱 권한은 `chat:write` 하나이며 이벤트
구독, App-Level Token, signing secret이 없다. 혹시 이벤트가 서비스 endpoint에 도달해도 ingress가
작업 생성 전에 무조건 무시한다. 전용 bot token은 기존 root-owned 비밀 파일에만 추가한다.

새 게시물은 `tech_scout` outbox agent로 기록한다. 배포 migration은 아직 전송되지 않은 pending
tech-feed만 Reporter에서 Tech Scout로 옮긴다. delivered·uncertain 영수증과 과거 Slack 메시지는
원래 identity 그대로 보존하고 재발송하지 않는다. hot-news의 Reporter 역할·자격증명·정책은 바꾸지 않는다.

Slack 앱 이름은 `Tech Scout`, bot 표시명은 `tech-scout`다. 프로필은 기존 직원들과 같은
세라믹 로봇 얼굴·구도·재질을 사용하고, 작은 원형 crop에서도 읽히는 네이비·청록 배경과
레이더·나침반·발견 점으로 역할을 구분한다.
