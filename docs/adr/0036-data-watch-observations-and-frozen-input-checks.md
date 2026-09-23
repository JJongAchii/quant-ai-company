# ADR 0036: 전체 레이크 관측과 고정 연구 입력 검사를 분리한다

2026-09-22. 사용자 승인된 채널 구조의 두 번째 구현.

2 GiB 회사 서버는 전체 catalog와 변경된 객체의 bounded footer만 읽는다. qdata의 전체
coverage나 임의 경로를 읽지 않는다. metadata object identity와 descriptor를 대조하고
조회 전후 파일 교체·중간 실패·불완전 통계를 건강 상태로 바꾸지 않는다. 업로드 시각과
데이터 기준일을 구분하며 명시적인 날짜/공표 마감 계약 밖은 기준 미등록이다.

현재 승인과 revision이 유효한 등록 ETF recipe만 상세 검사 대상으로 만든다. 기존 워커가
정확한 고정 파일을 읽고 typed receipt를 반환한다. 회사는 scope·hash·lease·측정값을 검증한다.
새로운 연구·training·백테스트·실주문·최신 데이터 교체를 수행하지 않는다. 이 검사는 연구
감사를 대체하지 않는다. 전체 범위와 명시적 미검사 항목은 [운영 문서](../data-watch.md)에 있다.

PostgreSQL은 observation/lease/receipt/incident/outbox를 소유한다. Temporal의 독립 queue가
60초 tick, 30분 목록 bucket, 09:00 KST 이후 당일 요약을 수행한다. 모델·CI 대기는 이 queue를
점유하지 않는다. 도중 crash는 DB lease와 고정 ID로 회복하며, 조회 실패도 독립 영수증으로 남긴다.
Slack은 기존 data 신원을 사용하고 전달된 root에만 후속 상태 전환을 게시한다. uncertain 쓰기는
재전송하지 않는다. 현재 권한·revision·채널·정책은 claim과 실제 HTTP 직전에 대조한다.

새 장애는 동일 장애 스레드를 계속 사용하고 현재 활성인 관측 실패만 improvements에 연결한다.
최신 레이크의 실패와 고정 연구 입력 실패의 영향은 별도로 서술한다. 회사 코드의 진단/수정은
기존 exact-candidate 승인 경로를 유지하며 상류 저장소 수정은 이 결정의 범위 밖이다.

기본값은 비활성이다. 실제 Slack 게시와 3070 운영 인수는 로컬 합성 검사와 별도로 기록한다.
