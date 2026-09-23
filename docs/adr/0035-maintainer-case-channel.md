# ADR 0035: 전용 Maintainer 케이스와 독립 진행 보고

상태: 구현; 실제 운영 활성화는 별도 인수 증거를 따른다. 날짜: 2026-09-22.

## 문제와 결정

기존 개선 담당은 총괄 신원으로 원문 스레드에 보고하며 긴 maintenance tick의 finally에서
상태를 게시한다. 사용자는 전용 채널에서 문제별 진행을 보고 대화를 이어가길 요청했다.

기존 엔진·모델·approval/application을 재사용하고, 설정으로 활성화되는 Maintainer 대화 역할과
`maintenance_cases`를 추가한다. 케이스는 불변 진단 요청, 원문 project/revision/owner, 대상 project,
봇 카드 message ID를 연결한다. triage→repair→recheck의 내부 job 변화는 외부 케이스 ID를 바꾸지 않는다.
기존 진단은 새 대상으로 이관하지 않는다. 새 진단의 Slack 대상은 서비스 설정만 선택한다.

별도 Temporal workflow/queue가 경량 DB 보고를 수행한다. 기존 workflow history는 바꾸지 않는다.
상태 카드 수정에는 `outbox.update_ts`를 사용하고, 이미 전달된 같은 봇·같은 채널의 카드 영수증을
검증한다. 게시·수정 결과가 불명확하면 자동 재전송하지 않고 후속 수정도 보류한다.

승인은 실제 전달된 PR 알림 이후, 같은 소유자·케이스·현재 원문 revision에서만 수락한다.
후속 application 단계에서도 원문 권한과 케이스 연결을 재확인한다. PR head·패치·CI·배포의 기존
검증을 완화하지 않는다. Maintainer는 승인/모델/활성화/자기 권한 파일을 자동 수정할 수 없다.

## 경계와 검증

전체 채널 설계는 [최종 구조](../project/SLACK-COMPANY-DESIGN.md)에 기록한다. 이번 실행 범위는
개선봇이며 data-watch가 다음 구현이다. 실제 PostgreSQL·Temporal과 모의 Slack/모델로 접수,
권한, 갱신, 승인, 재시작, 장시간 실행 중 독립 보고를 검사한다. 실제 Slack/구독/운영 활성화는
[인수 기록](../project/IMPROVEMENTS-VALIDATION.md)에서 별도 표시한다.
