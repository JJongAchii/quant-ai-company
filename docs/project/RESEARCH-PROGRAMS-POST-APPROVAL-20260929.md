# 첫 연구 프로그램 소유자 승인 후 운영 확인

2026-09-29 KST. 소유자가 Slack의 프로그램 승인 버튼을 눌렀다. 운영 PostgreSQL의
읽기 전용 조회에서 프로그램 `e06537d3-fac3-5c8c-bf25-ddabb3c7e282`가 `active`이고,
승인 이벤트가 서명된 `block_actions` 입력, 허용된 소유자·채널, 기존 승인 메시지와
동일한 바인딩, 원래 digest `53392822414ca32e89fab0f3a9a1093a350196345bd13084654a45510297159b`에
연결된 것을 확인했다. [승인과 첫 단계 영수증](evidence/research-programs-20260929/owner-approval-first-stage.json).

첫 `program_proposal` 실행은 계정 인증 오류로 대기했다. 기본·예비 계정 모두 공식
ChatGPT 로그인 조회에서는 정상이었고, 서비스 간 runtime token도 일치했다. 실제 원인은
계정을 이미 고른 연구 워커가 다시 계정을 고르는 account-gateway로 요청을 보낸 설정이다.
게이트웨이가 HTTP 401을 반환했고, 첫 실패 요청에는 Codex 실행 영수증이 생성되지 않았다.
[원인 조사](evidence/research-programs-20260929/routing-diagnosis.json).

커밋 `0742e88dbbcf524644d584722ec2282e0d6392a9`에서 계정 선택이 활성화된 연구 워커의
목적지를 Codex runtime으로 직접 지정했다. 2026-09-29 09:34:50 KST에 **연구 워커만** 기존
`3822845` 실행 이미지로 재생성했다. PostgreSQL·API·Slack·dispatch·Codex runtime·
account-gateway 등의 컨테이너 ID는 그대로다. 현재 릴리스 포인터는 수정된 배포 설정
커밋을 가리키며, 실행 코드 이미지 라벨은 원래 `3822845`로 기록한다.
[적용 영수증](evidence/research-programs-20260929/route-fix-cutover.json).

수정 뒤 자동 재시도에서 공식 ChatGPT 인증을 쓰는 기본 계정의 실제 Codex 모델 응답
4건이 완료됐다. 연구자는 고정 재현 보고서 전체와 원문 논문 세 청크를 읽은 뒤 과제를
제안했지만, 논문의 마지막 청크를 읽지 않은 상태로 인용해 서비스가 제안을 거부했다.
[원문 읽기 감사](evidence/research-programs-20260929/source-read-rejection.json)에서 해당
논문의 `next_offset=36000`이 남은 사실과 차단 사유를 확인했다. 원문 전체를 읽지 않은
인용을 허용하지 않는 검증이 정상 작동한 사례다. 09:48 KST에 새 직원 시도가 시작됐다.
이 시도는 구조화된 과제 문자열 안에 실제 줄바꿈 12개를 넣어 JSON 파싱에서 차단됐다.
[형식 오류 영수증](evidence/research-programs-20260929/artifact-format-rejection.json)은
완료된 Codex 호출과 거부된 과제를 구분해 기록하며 원문이나 모델 출력을 포함하지 않는다.
다음 시도에서도 같은 논문의 마지막 구간을 읽지 않은 인용이 차단됐다. 이 단계까지 프로그램
과제, 미션, 자원 예약, 새 과학 시행은 모두 0건이다.

커밋 `2d2ee42ffc41dabb505020ada896e4d9760a8ab1`은 외부 권한이나 원문 검증 조건을
바꾸지 않는다. JSON 문자열 안의 실제 줄바꿈만 동일 의미로 해석하고, 일부 원문을 읽은
상태에서 조기 제안하면 해당 응답을 거부한 기록을 보존한 채 **같은 직원 시도**에 정확한
다음 구간을 요청할 기회를 한 번 준다. 원문을 전혀 읽지 않았거나 같은 미완독 제안을
반복하면 기존 차단이 유지된다. 실제 PostgreSQL 회귀 검사와 전체 테스트 1,289 통과·
13 건너뜀, lint 통과를 확인했다. 2026-09-29 10:13:25 KST에 새 연구 워커 이미지와
설정을 적용했고, 교체 직전 실행 중인 턴·연구 작업이 0건인 것과 다른 운영 컨테이너 ID
보존을 확인했다. [워커 교체 영수증](evidence/research-programs-20260929/read-fix-cutover.json).

운영 7회차에서는 조기 제안이 원문 미완독으로 차단된 뒤 같은 직원 시도 안에 다음
턴이 생성됐다. 그 턴이 논문의 남은 6,683자를 `offset=36000`에서 읽어
`next_offset=null` 완독 영수증을 남겼다. 10:26:50 KST에는 최종 구조화 제안 턴이
실행 중이었고, 연구 과제나 과학 시행은 여전히 0건이었다.
[같은 시도 원문 완독 영수증](evidence/research-programs-20260929/same-attempt-source-continuation.json).

완독 뒤 나온 과제 제안은 원문 위치 검증에서 거부됐다. 7개 인용 문구는 원문 문자열에
있었지만, 모델이 페이지 필드의 정확한 값 대신 설명식 위치를 적었다. 보고서 원문은
`char:<offset>`, 페이지가 있는 논문은 `PDF p.6`처럼 원문 `location` 필드 그대로여야
한다. 운영에서는 과제·미션·예약·과학 시행 0건을 유지했고, 다음 직원 시도가 자동 시작됐다.
[인용 위치 거부 영수증](evidence/research-programs-20260929/citation-location-rejection.json).

8회차도 같은 인용 위치 오류로 과제 등록 전에 거부됐다. 커밋
`a7ddf0ec5ee2b7e681dfe1046160140a68be1b82`에서 제안 프롬프트에 페이지 `location`
필드와 `char:0` 위치 형식을 명시했다. 인용 검증 코드는 유지했고 전체 테스트 1,289건,
lint, PR 서비스 CI가 통과했다. [검증 영수증](evidence/research-programs-20260929/citation-prompt-validation.json).

이 수정본을 운영에 적용하려던 사전 검사에서 릴리스 포인터가 다른 감사 작업의
`fac61efe624f843c2edc5aec51b8d1e0c7c989cb`으로 바뀐 것을 발견했다. 검사 단계에서
중단돼 이 작업의 운영 설정·컨테이너는 변경되지 않았다.
[안전 중단 영수증](evidence/research-programs-20260929/release-drift-safe-abort.json).
현재 운영 워커 `d790b2e`의 코드를 포함하도록 소스를 병합한 `6dfbdcc` 후보를 만들었다.
첫 이미지 빌드는 서버 디스크 공간 부족으로 실패했다. 이번 작업의 미사용 이미지 두 개와
오래된 빌드 캐시 일부만 정리한 뒤 재빌드가 성공했고, 새 워커 설정 후보의 Codex runtime
직접 경로를 검증했다. **후보는 아직 운영에 적용되지 않았다.**
[통합 후보 영수증](evidence/research-programs-20260929/citation-integrated-candidate.json).

9회차도 인용 위치 오류로 거부됐다. 통합 후보를 적용하려던 다음 사전 검사에서도 별도
감사 릴리스 `5fcd4353b8adab3e3fb6f11fda3f08fef7aea801`이 먼저 활성화돼 변경 전
중단됐다. 운영 설정과 컨테이너는 이 전환 시도로 변경되지 않았다.
[두 번째 안전 중단 영수증](evidence/research-programs-20260929/second-release-drift-safe-abort.json).
현재 다른 배포 브랜치는 새 감사 릴리스와 동일한 인용 안내를 통합 중이다. 이 작업은
중복 운영 전환을 멈추고 실제 활성 이미지와 연구 기록을 읽기 전용으로 대조한다.

최신 main을 병합한 PR #87의 커밋 `716cbc1`에서 전체 테스트 1,307 통과·13 건너뜀,
lint와 서비스 CI 성공, 병합 가능 상태를 확인했다. 운영 후보 `6dfbdcc`와 PR HEAD의
연구 controller·프로그램 controller·배포 경로 파일은 동일하다.
[통합 검증 영수증](evidence/research-programs-20260929/citation-integrated-validation.json).

배포 설정 회귀 검사는 통과했다. 첫 수정의 전체 `uv run pytest -q` 결과는 1,288 통과·13 건너뜀,
`uv run ruff check .`도 통과했고 PR #87의 커밋 `0742e88` CI는 성공했다.
이 수치는 전략 성과나 가설 검증 결과가 아니다.
이후 `8fe020f9a8893bedf299d01592e341026e2a69ea`에서 main 병합 충돌을 해결했고,
병합본의 전체 테스트 1,289 통과·13 건너뜀과 PR #87의 서비스 CI 성공을 확인했다.

연구 프로그램은 승인된 24회 과학 시행·36,000초 계산·6개 미션·동시 1개 미션의
상한 아래에서 자료 제안 → 독립 데이터 점검 → director 선정 순서로 진행한다. 후속 단계의
실제 결과는 별도 운영 기록과 연구 감사가 있어야 판단한다.
