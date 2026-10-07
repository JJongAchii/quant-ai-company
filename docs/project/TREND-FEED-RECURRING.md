# 검색 트렌드 정기 세 슬롯·소유자 요청

2026-10-07 11:01 KST 운영 전환을 완료했다. 한국 시간 매일 **08:00·14:00·20:00**에
`#search-trends`로 브리핑을 발송하며, 소유자의 `실시간 검색어`·`검색 트렌드` 또는
Trend Scout 멘션 요청에도 원 메시지 스레드로 답한다. 스포츠 제외·회당 10개 목표·
급상승과 주요 이슈의 구분을 유지한다. 전환 직후 다음 정기 발송은 오늘 14시다.

설정과 사용법은 [운영 설명](../trend-feed.md), 선택 근거는
[ADR](../adr/0042-trend-recurring-owner-requests.md)에 있다.
변경과 운영 증거는 [PR 119](https://github.com/JJongAchii/quant-ai-company/pull/119)에 게시한다.

## 실제 연결과 요청 응답

기존 앱을 필요한 세 scope(`chat:write`, `app_mentions:read`, `channels:history`)와
두 이벤트로 재설치하고, 실제 workspace·bot ID·인증 소켓 연결을 확인했다.
토큰 값은 출력하지 않고 서버 비밀 파일에만 저장했다. 다른 앱 자격증명은 보존했다.
[연결 영수증](evidence/trend-recurring-20261007/slack-connection.json).
운영 수신은 기존 Slack SDK 인증 Socket Mode다. HTTP HMAC envelope와 구분하며,
두 경로의 app·workspace·소유자·전용 채널 제한을 적용한다.

실제 소유자 Slack 화면에서 11:06에 보낸 `실시간 검색어`가 DB에 커밋됐고,
요청 뒤 11:07 RSS를 새로 수집해 기존 공식 구독 모델로 편집했다.
11:09 원 메시지 스레드에 **10개**가 전달됐다(약 144초).
검색 급상승 8개·검증된 주요 이슈 2개를 구분하고 스포츠 제외와 자료 마감 시각을 표시했다.
11:13 같은 스레드에서 보낸 `검색 트렌드`에도 약 28초 뒤 10개가 전달됐다.
두 번째 요청은 첫 편집을 재사용하며 추가 모델 호출 없이 원래 11:07 자료 마감을 유지했다.
각 요청의 안정 ID·발송 receipt·실제 Slack 부모·본문 hash·permalink를 대조했다.
[실제 요청과 발송](evidence/trend-recurring-20261007/live-slack.json),
[Slack 화면](evidence/trend-recurring-20261007/ui-thread.json).
이는 실제 자료·모델·Slack을 사용하는 운영 검사이며 아래 fixture 회귀와 별개다.
10개는 목표이며 검증된 비스포츠 자료가 부족하면 실제 개수와 사유를 표시한다.

발송 없는 실제 구독 미리보기 한 건도 비스포츠 10개로 검증했다.
운영자 검사 컨테이너의 모델 네트워크가 처음 빠져 확실한 `unavailable`로 보류됐으며,
연결 복구 후 같은 요청 ID와 동결 입력으로 완료했다. 불명 요청의 새 호출 대체는 없었다.
최종 이미지에서 저장된 구독 응답·원문·본문을 다시 대조했으며 추가 모델/Slack 호출은 없었다.
[미리보기](evidence/trend-recurring-20261007/live-preview.json),
[최종 이미지 대조](evidence/trend-recurring-20261007/preview-current-image.json).

## 활성화와 보존

실제 활성 부모 소스에 이번 기능만 적용한 이미지로 news-worker·api·slack-socket·dispatch를
바꿨다. 다른 브리핑 패키지 바이트, 다른 컨테이너·환경·이미 전달한 본문·기존 모델 요청을
보존했다. 전환용 중지는 해제했다. [활성화 영수증](evidence/trend-recurring-20261007/activation.json),
[이미지](evidence/trend-recurring-20261007/images.json),
[전환 사전 검사](evidence/trend-recurring-20261007/preflight.json).

새 `company-trend-feed-publication-v2`와 기존 수집/편집 workflow는 실행 중이다.
새 publisher 확인 뒤 아침 publisher-v1을 종료했으며 모든 기존 이력은 유지했다.
정기 슬롯 ID와 확정 본문/outbox를 영속 저장하고 Temporal timer로 실행한다.
[실제 상태와 Temporal timer](evidence/trend-recurring-20261007/active-probe.json).
운영 모델 예산은 KST 하루 12개 중 정기 편집용 6개 확보, 네이버 HTTP는 하루 100회 이하다.
최근 검증 편집과 같은 날짜 범위·키워드의 네이버 원 응답 전체를 재사용한다.

빈도만 복구할 때에는 새 코드를 유지하고 시간대를 `[8]`, 수시 요청을 false로 바꾼다.
이미 실행 중인 publisher-v2가 기존 아침 슬롯을 처리하므로 종료한 legacy ID를 자동
재사용하지 않는다. 부모 이미지까지 복구해야 한다면 이전 영수증과 설정을 읽고
원래 워커를 복구한 뒤 별도의 안정 recovery publisher ID를 명시적으로 등록한다.
전송/모델 `uncertain`은 영수증과 실제 Slack을 대조하며 자동 재발송하지 않는다.

## 회귀와 원격 CI

운영 코드 `dcdc29c6fba7d05a89f027e8f2e760b4311014d5`의
[원격 CI](https://github.com/JJongAchii/quant-ai-company/actions/runs/37559786887)는
서비스 **1,623 통과·47 skip·1 deselect**, Codex 무인증 프로토콜 **9 통과**, lint 통과다.
실제 PostgreSQL·로컬 Temporal을 사용한다. HTTP 서명은 합성 키, 외부 자료·모델·Slack은
fixture이며 이 자동 검사만으로 실제 연결/배포를 주장하지 않는다.
[CI 영수증](evidence/trend-recurring-20261007/ci.json),
[최종 요약 로그](evidence/trend-recurring-20261007/ci.txt).

현재 실제 부모 소스에 기능을 적용한 최종 후보 회귀는 수집·발송 **90개**, 수신 **40개**가
통과했다. 다른 브리핑 구현을 보존한 상태에서 실제 PostgreSQL·로컬 Temporal로 검사했으며
외부 자료·모델·Slack은 fixture다.
[수집·발송 최종 회귀](evidence/trend-recurring-20261007/native-news-final-pytest.txt),
[수신 최종 회귀](evidence/trend-recurring-20261007/native-api-final-pytest.txt).

전용 채널 권한만 정책에 반영해 다른 피드의 프로세스별 허용 채널 차이는 요청을 막지 않고,
전용 채널 권한 제거는 계속 발송을 차단한다. 수집 중 도착한 요청의 갱신도 다음 수집에 유지한다.
이전 검사 기록은 [관련 119개](evidence/trend-recurring-20261007/pytest.txt),
[추가 67개](evidence/trend-recurring-20261007/final-pytest.txt),
[초기 전체 1,620개](evidence/trend-recurring-20261007/full-pytest.txt),
[lint](evidence/trend-recurring-20261007/ruff.txt)에 보존한다.
