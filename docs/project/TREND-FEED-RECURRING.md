# 검색 트렌드 정기 세 슬롯·소유자 요청

2026-10-07 사용자 요청과 한국 시간 08:00·14:00·20:00 선택을 구현했다.
슬롯별 안정 ID와 영속 확정 본문, 전용 채널의 결정론적 `실시간 검색어` 수신,
최신 성공 RSS 갱신, 요청 스레드 응답, 최근 검증 편집·NAVER 원 응답 재사용을 추가했다.
스포츠 제외·10개 목표·주요 이슈 보충·원문 검증을 유지한다.

설정과 사용법은 [운영 설명](../trend-feed.md), 선택 근거는
[ADR](../adr/0042-trend-recurring-owner-requests.md)에 있다.

실제 PostgreSQL·로컬 Temporal 관련 회귀 119개가 통과했다.
[테스트 출력](evidence/trend-recurring-20261007/pytest.txt),
[lint](evidence/trend-recurring-20261007/ruff.txt).
HTTP 서명은 합성 시험 키, 자료·모델·Slack 응답은 fixture다.
이 자동 시험을 실제 Slack 연결 또는 배포 근거로 사용하지 않는다.

현재 실제 부모 소스에서 새 기능만 적용한 후보로 수집·발송 부모 회귀 88개와
API·인증 소켓 부모 회귀 38개가 통과했다. 실제 PostgreSQL·로컬 Temporal을 사용했고
외부 자료·모델·Slack은 fixture다. 다른 브리핑 패키지는 원래 바이트를 보존했다.
[수집·발송 부모](evidence/trend-recurring-20261007/native-news-pytest.txt),
[수신 부모](evidence/trend-recurring-20261007/native-api-pytest.txt).

전체 서비스 검사도 1,620 통과·48 skip·1 deselect다.
[전체 검사](evidence/trend-recurring-20261007/full-pytest.txt).

원격 CI, 실제 앱 scope 재설치·인증 소켓 연결, 실제 요청/응답과 운영 활성화 영수증은
별도로 확인하여 추가 기록한다. 현재 이 문서의 자동 회귀는 그 완료를 뜻하지 않는다.
