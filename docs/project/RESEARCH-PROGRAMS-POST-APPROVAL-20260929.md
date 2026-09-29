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
당시 다른 배포 브랜치도 새 감사 릴리스와 동일한 인용 안내를 통합 중이었다. 이후
운영 전환 직전에 실제 활성 이미지와 연구 기록을 다시 대조했다.

추가로 현 활성 릴리스 `5fcd435`에 인용 안내 파일만 더한 `0072b57` 후보를 준비했다.
처음 만든 얇은 이미지는 소스 경로만 바꾸고 실제 설치 패키지의 import 경로를 놓쳤다.
배포 전 import 해시 검사에서 발견해 그 이미지를 제거했다. 수정한 후보는 두 경로의
파일 해시가 일치하고, 운영 설정 후보에서도 공식 ChatGPT용 Codex runtime 직접 경로를
유지한다. 이 후보 준비 시점에는 운영에 적용하지 않았다.
[단일 파일 이미지 후보 영수증](evidence/research-programs-20260929/citation-overlay-candidate.json).

10회차도 기존 워커에서 인용 위치 오류로 거부됐다. 후보 `0072b57`의 전체 테스트
1,307 통과·13 건너뜀, lint와 PR #87 CI 통과를 확인한 뒤, 11:39:13 KST에
안전 사전 검사와 함께 **연구 워커 한 개만** 새 이미지로 교체했다. 승인 digest·서명된
Slack 이벤트·프로그램 활성 상태, 대기 중인 단계, 실행 중인 턴·작업·불확실한 모델
호출이 0건인 것을 검사했다. 나머지 서비스의 컨테이너 ID는 그대로였고, 적용 후 실제
워커가 설치 패키지에서 수정된 파일 해시를 import하는 것을 읽기 전용으로 확인했다.
[후보 검증](evidence/research-programs-20260929/citation-overlay-validation.json),
[운영 전환 영수증](evidence/research-programs-20260929/citation-overlay-cutover.json),
[적용 후 확인](evidence/research-programs-20260929/citation-overlay-post-check.json).
11회차 자동 재시도는 11:43 KST에 새 워커에서 시작됐다. 11:53 KST 읽기 전용
조회에서는 일곱 개의 고정 모델 요청 모두에 정확한 인용 안내와 `PDF p.6` 예시가
들어 있었다. 연구자는 재현 보고서와 선택한 논문의 마지막 구간까지 읽었고, 최종
제안 턴은 실행 중이었다. 과제·미션·예약·과학 시행은 0건이다.
[11회차 요청·완독 영수증](evidence/research-programs-20260929/citation-guidance-attempt11.json).
검증을 통과한 제안이나 독립 검토 결과는 아직 없다.

11회차의 최종 제안은 인용 검증을 통과했지만 선행 미션 검증에서 거부됐다. 운영
프로그램의 미션은 0건인데 제안의 `predecessor_mission_ids`에 1건이 들어 있었다.
완료된 모델 호출 7건에는 불확실한 호출이 없고 과제는 생성되지 않았다.
[선행 미션 거부 영수증](evidence/research-programs-20260929/predecessor-rejection-attempt11.json).
커밋 `ac44bf5`는 제안 요청에 이 프로그램의 허용된 선행 미션 ID 목록을 명시하고,
첫 과제처럼 목록이 비었으면 `[]`를 요구한다. 선행 미션 검증 규칙은 그대로다.
실제 PostgreSQL 테스트를 포함한 전체 1,307 통과·13 건너뜀, lint가 통과했다.
현 워커 `0072b57`를 바탕으로 실행 소스 한 파일만 바꾼 이미지를 만들고, 설치된
패키지의 import 해시와 공식 ChatGPT용 Codex runtime 직접 경로를 검증했다.
12:09 KST 후보 준비 시점에는 운영에 적용하지 않았으며, 12회차는 기존 워커에서 실행 중이었다.
[후보 영수증](evidence/research-programs-20260929/predecessor-overlay-candidate.json),
[후보 검증](evidence/research-programs-20260929/predecessor-overlay-validation.json).

12회차도 기존 안내에서 논문을 완독하고 인용 검증을 통과한 뒤 같은 선행 미션
오류로 거부됐다. 제안은 프로그램 밖의 선행 미션 1건을 다시 지정했고 과제·미션·
예약·과학 시행은 0건을 유지했다.
[12회차 거부 영수증](evidence/research-programs-20260929/predecessor-rejection-attempt12.json).
PR #87의 서비스 CI 성공과 병합 가능 상태를 확인했다. 2026-09-29 12:25:01 KST,
승인 digest·소유자 Slack 이벤트·대기 단계, 실행 중 턴·작업·불확실한 모델 호출 0건을
재검사하고 **연구 워커 한 개만** `ac44bf5` 이미지로 교체했다. 나머지 서비스의
컨테이너 ID는 그대로였으며, 새 워커가 설치 패키지에서 수정된 파일 해시를 실제로
import하는 것을 확인했다. 다음 자동 재시도는 12:29:22 KST 예정이다.
[전환 영수증](evidence/research-programs-20260929/predecessor-overlay-cutover.json),
[적용 후 확인](evidence/research-programs-20260929/predecessor-overlay-post-check.json).

13회차 자동 재시도는 12:29 KST에 새 워커에서 시작됐다. 고정된 모델 요청에
`allowed_predecessor_mission_ids=[]`와 첫 과제의 빈 선행 미션 목록 지시가 들어
있다. 12:39 KST에는 재현 보고서와 선택한 논문을 모두 완독했고 최종 제안 턴이
실행 중이었다. 이 시점의 과제·미션·예약·과학 시행은 0건이다.
[13회차 요청·완독 영수증](evidence/research-programs-20260929/predecessor-guidance-attempt13.json).

13회차 최종 제안은 검증을 통과해 12:45 KST에 첫 과제 1건으로 등록됐다. 독립
데이터 직원은 지정 ETF 입력의 시점·커버리지·체결 가능성에 대한 현재 과제의
조회 근거가 부족해 `blocked`를 기록했다. director는 원문 완독 검증 뒤 과제를
`waiting`으로 남겼다. 미션·자원 예약·과학 시행은 0건이다. 이후 새 검토 자료
1편이 유입돼 다음 연구자 제안 단계가 시작됐다. 새 자료는 국내 ETF 입력 증거가
아니다. [첫 과제 데이터 근거 공백](FIRST-TASK-DATA-GAP-20260929.md)에 실제
준비 영수증과 독립 심사의 남은 확인 사항을 대조했다.

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

13:06 KST에는 새 문헌을 근거로 연구자가 두 번째 ETF 과제
`dc0e9eac-040d-5eac-9a6d-69fd2c32a0f6`를 등록했다. 원문 2건에 대한 인용
8건과 빈 선행 미션 목록이 기록됐다.
[두 번째 과제 요약](evidence/research-programs-20260929/second-task-proposal-summary.json).
첫 데이터 직원 시도는 인용한 원문을 끝까지 읽지 않아 차단됐고, 두 번째·세 번째
시도는 Codex 응답의 회사 결정 계약 형식 오류로 차단됐다. 네 번째 시도도 인용한
원문을 열지 않은 상태로 결론을 제시해 차단됐다. 각 시도의 호출 영수증은
정상 완료와 형식 거부를 구별한다. 네 번째 시도 직후 과제는 `proposed`였다.
[첫 차단 영수증](evidence/research-programs-20260929/second-task-data-read-rejection.json),
[재시도 호출 영수증](evidence/research-programs-20260929/second-task-data-retry-faults.json),
[네 번째 시도 상태](evidence/research-programs-20260929/second-task-data-attempt4.json).
다섯 번째 데이터 직원 시도는 13:34 KST에 유효한 독립 심사로 완료됐고,
시점·커버리지·실행 가능 가격·원문 조건 네 항목을 모두 미확인으로 판단해
`blocked`를 기록했다. 직원이 실제 고정 ETF 입력과 실행 프로필을 조회한
증거가 없다는 이유다. director는 두 번째 제안을 첫 보류 과제의 설계 보완안으로
판단했고, 입력 증거가 새로 확보되지 않았으므로 13:42 KST에 다시 `wait`를
기록했다. 제안이 요구하는 당일 종가 체결도 현재 고정 엔진의 시가 간 평가
계약과 다르다. 미션·예약·과학 시행은 0건이다.
[두 번째 과제 데이터 심사](evidence/research-programs-20260929/second-task-data-assessment.json).
[director 최종 판단](evidence/research-programs-20260929/second-task-final-decision.json).

04:08 UTC에 연구 워커·작업 배포기·Slack 소켓 컨테이너가 종료됐고 API와
Codex 런타임은 다시 올라왔다. 종료 원인은 이 읽기 전용 조사만으로 확정하지
않았다. 04:17 UTC에 배포 잠금, 활성 릴리스 `ac44bf5`, 승인 digest·이벤트,
실행 중 턴·단계·작업 0건을 재검사한 뒤 종료돼 있던 **동일한 세 컨테이너**를
재시작했다. 컨테이너 ID·이미지와 다른 핵심 서비스 ID는 유지됐고, 자동 데이터
시도가 다시 생성되는 것을 확인했다.
[복구 전후 영수증](evidence/research-programs-20260929/service-restart-recovery.json).
04:31 UTC에는 별도의 director 후속 응답 릴리스 `eec5e23`이 활성화됐다.
연구 워커가 가져온 프로그램 controller의 실제 import 해시는 앞선 `ac44bf5`
수정본과 같음을 확인했다. 이번 원문 이어 읽기 수정은 새 릴리스와 통합해
검증해야 한다.
[활성 릴리스 확인](evidence/research-programs-20260929/active-release-drift-20260929.json).

커밋 `cd6d293`에서 인용 원문이 아예 열리지 않은 경우에도 같은 직원 시도에서
첫 청크를 읽을 기회 한 번을 주도록 연구 controller를 수정했다. 같은 미완독
제안을 반복하면 기존처럼 거부하고, 원문 완독 검증은 유지한다. 새 운영 릴리스와
병합한 회귀 검사 48건, 전체 테스트 1,312 통과·13 건너뜀, lint 및 PR #87
서비스 CI가 통과했다. 활성 `eec5e23` 이미지를 바탕으로 실행 소스 한 파일만
바꾼 후보를 만들고 설치 패키지의 import 해시와 공식 ChatGPT runtime 경로를
검증했다. [소스 준비](evidence/research-programs-20260929/unread-source-overlay-stage.json),
[이미지 검증](evidence/research-programs-20260929/unread-source-overlay-build.json),
[실행 설정 후보](evidence/research-programs-20260929/unread-source-overlay-runtime.json),
[통합 검증](evidence/research-programs-20260929/unread-source-overlay-validation.json).
04:45 UTC 전환 사전 검사에서는 director의 Slack 후속 답변 턴 1건이 실행 중이라
적용 전에 중단됐다. 이 검사로 운영 설정이나 컨테이너가 변경되지는 않았다.
[안전 보류 영수증](evidence/research-programs-20260929/unread-source-cutover-held.json).
후속 답변들이 끝난 04:53 UTC에는 승인 digest·서명된 소유자 이벤트·대기 상태,
실행 중 턴·단계·작업·불확실한 모델 호출 0건과 배포 잠금 해제를 재확인했다.
**연구 워커 한 개만** `cd6d293` 이미지로 교체했고, 다른 서비스 컨테이너 ID는
유지됐다. 적용 후 설치 패키지에서 수정된 `controller.py` 해시가 실제로 import되고
공식 ChatGPT용 Codex runtime 직접 경로가 유지됨을 확인했다. 04:54 UTC에
프로그램은 계속 `active`, 두 과제는 모두 `waiting`, 미션·예약·과학 시행은 0건이다.
[전환 영수증](evidence/research-programs-20260929/unread-source-overlay-cutover.json),
[설치 코드 확인](evidence/research-programs-20260929/unread-source-overlay-post-check.json),
[프로그램 읽기 확인](evidence/research-programs-20260929/program-post-unread-source.json).

13:24 KST에는 승인된 ETF 입력을 고정 qdata API와 현재 S3 객체로 대조했다.
당시 20거래일 기준 cohort, 835거래일의 8,350개 원시 시가·종가·거래대금 행은
일치했다. 현재 S3 객체는 준비 당시 객체와 달라 조정종가 6,680행이 바뀌었고,
종목별 차이는 일정한 배율이었다. 원래 객체 버전 ID와 원천 공개시각·실제
체결 조건은 미확인이다. 상세 결과와 재현 스크립트는
[첫 과제 데이터 근거 공백](FIRST-TASK-DATA-GAP-20260929.md)에 기록했다.
데이터 직원의 `blocked`와 director의 `waiting` 판단을 변경하지 않았으며,
미션·예약·과학 시행은 여전히 0건이다.

05:27 UTC에는 승인된 프로그램 digest, ETF 입력 SHA 두 개, 실행 프로필 digest,
보호된 엔진 SHA에 묶인 데이터 증거 패킷을 연구 워커에 적용했다. 입력·엔진·
독립 대조 영수증·미확인 사항 노트를 운영 저장소의 별도 디렉터리에 배치하고
후보 컨테이너에서 실제 파일 바이트와 두 ETF envelope를 검증했다. 파일 해시
일치가 원천 공개시각이나 체결 가능성을 증명한다고 해석하지 않는다.
[패킷 배치](evidence/research-programs-20260929/etf-data-evidence-staged.json),
[후보 검증](evidence/research-programs-20260929/data-evidence-worker-qualified.json).

소스 커밋 `e3e26e9`는 로컬 전체 테스트 1,315건 통과·13건 건너뜀,
추가 회귀 13건 통과, lint, PR #87 서비스 CI 성공을 거쳤다. 서명된 소유자
승인과 실행 중 턴·단계·잡·미션·예약 0건을 재확인한 뒤 워커 하나만 교체했다.
다른 서비스 컨테이너 ID와 공식 ChatGPT runtime 경로는 유지됐다.
[최종 사전 검사](evidence/research-programs-20260929/data-evidence-cutover-precheck-final.json),
[워커 전환](evidence/research-programs-20260929/data-evidence-worker-cutover.json),
[설치 코드·DB 재확인](evidence/research-programs-20260929/data-evidence-worker-postcheck.json).

운영에서 패킷 목록을 포함한 새 연구자 제안 단계가 시작됐다. 기존 두 과제의
`waiting` 상태는 바뀌지 않았고 미션·예약·과학 시행은 0건이다. 새 데이터
직원 심사가 `ready`를 내더라도 패킷의 미해결 항목이 남은 동안 서비스가
실제 프로필의 승격을 거부한다. 원천 공개시각·원본 객체 버전과 실행 가격
조건을 해결해야 연구 실험으로 넘어갈 수 있다. 05:35 UTC의 읽기 영수증에는
연구자가 패킷 식별자·보호된 엔진·보고서 세 건을 완독하고 고정 입력 두 파일은
첫 청크만 읽은 것으로 기록됐다. 원문 두 건도 완독했다. 이 시점에 제안 단계는
진행 중이었고 새 데이터 심사나 과학 시행은 없다.
[직원 읽기 깊이](evidence/research-programs-20260929/data-evidence-researcher-read-depth.json).

05:43 UTC에는 연구자가 ETF `069500`의 고정 입력을 쓰는 세 번째 가설 과제를
등록했다. 이번 설계는 승인된 엔진의 당일 시가부터 다음 시가까지의 평가와
비용을 명시했다. 첫 두 과제는 계속 `waiting`이고 새 과제는 `proposed`다.
데이터 직원 1·2·4~6회차는 필수 패킷을 읽었지만 원문 출처 읽기 없이 인용해
거절됐고, 3회차는 구조화 산출물 형식 오류로 거절됐다.
[과제와 심사 세부 영수증](FIRST-TASK-DATA-GAP-20260929.md).

미등록 출처 ID를 패킷 경로로 혼동했을 가능성에 대비해 데이터 단계 프롬프트에
허용된 `evidence_sources.source_id`를 명시하고, 잘못된 ID의 보완 힌트를
**직원 시도당 한 번**으로 제한했다. 실제 원문 완독 요구와 데이터 준비 차단은
유지한다. 원래 실패 응답의 출처 ID는 보존되지 않아 혼동 원인은 추정이다.
로컬 전체 테스트 1,371건 통과·13건 건너뜀, lint, PR #87 서비스 CI 성공을
확인했다. [검증 영수증](evidence/research-programs-20260929/source-guidance-validation.json).

06:27 UTC에는 승인 digest·서명된 Slack 승인 이벤트·기존 프로그램 대기 상태와
실행 중 턴·단계·작업·불확실한 모델 호출 0건을 다시 확인했다. 다음 자동
재시도까지 여유가 있는 상태에서 **연구 워커만** `a09ce0f` 이미지로 교체했다.
다른 서비스의 컨테이너 ID는 유지됐고 설치 패키지의 소스 12개가 후보 릴리스
해시와 일치했다. 공식 ChatGPT runtime과 패킷 경로도 유지됐다.
[후보 검증](evidence/research-programs-20260929/source-guidance-worker-qualified.json),
[최종 사전 검사](evidence/research-programs-20260929/source-guidance-cutover-precheck.json),
[워커 전환](evidence/research-programs-20260929/source-guidance-worker-cutover.json),
[적용 후 확인](evidence/research-programs-20260929/source-guidance-worker-postcheck.json).
이 시점에 미션·예약·과학 시행은 0건이다. 새 안내를 이용한 데이터 직원의
다음 시도는 아직 완료되지 않았다.
