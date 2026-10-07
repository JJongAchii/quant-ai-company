# 첫 실제 연구의 검증·보고 모니터링

소유자의 `잘 모니터링해라` 요청에 따라 실제 모델 호출, 응답 접수, 독립 검증과 게시를 추적한다.

## 새로 발견한 멈춤

2026-10-07 11:41:31 KST에 시작한 두 번째 검증 호출은 11:45:02에 정상 완료됐다.
응답의 JSON 형태·packet digest·공식 provider thread는 맞지만 notes가 6,171자로
서비스의 6,000자 상한을 넘겨 접수가 거절됐다. 단계는 `waiting`이며 자동 재시도 시각은 없다.
첫 패킷은 정상 접수되어 있고 실제 과학 실행은 10월6일 완료한 한 건 그대로다.

## 준비한 수리와 적용 명세

- 요약의 작성 목표는 6,000자, 서비스의 제한된 여유는 8,000자로 둔다. 공백도 전체 길이에 포함한다.
- 이후 새 검증은 정식 frozen history에 포함된 전체 canonical lineage를 패킷으로 읽는다.
  모든 바이트·SHA·같은 provider session·qlab 최종 검증 조건은 유지한다.
- 현재 API와 회사 worker의 실제 immutable image 위에 두 Python 파일만 바꾼 이미지를 준비한다.
  다른 166개 Python 파일, 현재 계정/역할/compose, reporter·dispatch 변경과 3070 과학 release를 보존한다.
- 준비 영수증·전체 검사·정확한 PR105 변경을 root가 기존 배포/계속 진행 권한 안에서 검토한다.
  동일 backup lock, runtime pause, 외부 효과 drain, 기존 일관된 백업 후 API/worker만 바꾼다.
  이전 exact image와 compose 목록으로 되돌릴 수 있도록 보존한다.
- 캐시의 두 번째 응답을 원문 그대로 기존 `commit_stage`로 접수하는 PostgreSQL 트랜잭션을
  먼저 실행하고 롤백해 검사한다. 정확한 receipt SHA·request input digest·packet digest·thread를 확인한다.
  적용 시 실패 원인과 보존한 근거를 durable event에 남긴다. 모델을 다시 호출하거나 과학을 다시 실행하지 않는다.

계산 결과의 성과나 독립 감사 판정을 root가 만들지 않는다. 다음 정상 패킷의 호출과 **접수**를
모두 확인하고, 최종 감사·의미 검토·실제 게시까지 진행 상태와 오류를 기록한다.

## 검증 상태

첫 실제 PostgreSQL 패킷 회귀 검사는 9 pass/1 skip이다. skip은 고정 qlab 경로를 지정하지 않은 검사다.
실제 고정 qlab 경로를 지정한 전체 회사 검사: **1,831 pass / 14 skip**, 568.03초. 전체 lint 통과.
모델/Slack은 회귀 검사에서 모의했다. 실제 운영 결과는 별도 관측으로 확인한다.

첫 준비는 동시 worker 역할 호환 배포를 감지해 변경 전에 중단됐다. 최신 worker image/compose를
보존한 [비활성 이미지 준비](evidence/etf-exploration-20260930/audit-note-patch-prepared-20261007.json)와
[정확한 root 적용 검토](evidence/etf-exploration-20260930/audit-note-patch-apply-review-20261007.json)를 보존했다.
Apply operator는 모든 기존 native lane lock까지 획득해 백업 중 호출을 중단하지 않는다.
준비된 이미지와 source commit53ed9e3은 그대로다. 운영 반영과 응답 대사 영수증은 완료 후 추가한다.


## 최초 반영의 실제 import 검사 실패

14:12:46 KST에 source 복사 이미지의 API/worker 전환과 다른 서비스 복구를 완료했다.
실제 접수 트랜잭션 probe는 MAX_NOTES 검사에서 실패하여 DB 변경 없이 종료됐다.
Python은 /app/src가 아닌 /app/.venv/lib/python3.12/site-packages의 별도 설치본을 불러왔다.
설치본의 audit_delivery/mission_backend는 원래 SHA이고 한도는 여전히6,000자였다.
따라서 이 첫 source-copy 반영은 실제 응답 거절을 해결한 배포로 인정하지 않는다.

수정된 준비는 두 논리 모듈의 source와 실제 설치본을 함께 교체하고, **import한** MAX_NOTES8,000,
모듈 SHA, 나머지 설치 파일의 digest를 검사한다. 실제 설치본은 API183개/worker171개이며
각각의 나머지181개/169개 파일을 이전 그대로 유지한다. 이후 root 적용 검토와 같은 원문 접수
probe를 다시 수행한다. 과학 실행·원문·서명 예산과 worker release는 그대로다.

같은 호스트의 읽기 전용 관측기는45초마다 실행/접수/대기 원인/보고서 등록 상태만 보존하며,
최대4시간 동작한다. 모델 호출·회사 DB 변경·Slack 발송을 하지 않는다.


## 실제 반영과 응답 접수 확인

14:23:28 KST에 실제 설치 패키지의 두 모듈까지 반영했다. API/worker는 준비한 exact 이미지로
정상 기동했고 다른 모든 서비스 ID·image·환경/역할/프로필과 3070 release를 보존했다.
[반영 영수증](evidence/etf-exploration-20260930/audit-runtime-patch-applied-20261007.json)을 남겼다.

원문 접수 [롤백 트랜잭션](evidence/etf-exploration-20260930/second-audit-note-dry-run-20261007.json) 통과 후,
root가 기존 권한으로 정확한 수리를 검토했다. 14:24:07 KST에 event781로
[두 번째 원문 응답을 접수](evidence/etf-exploration-20260930/second-audit-note-reconciliation-applied-20261007.json)했다.
동일 provider thread·request/response/packet·canonical history·job/reservation을 보존했다.

14:24:08 KST에 정상 Temporal 흐름의 세 번째 검증 호출c7155b5d가 실제 시작됐다.
[14:24:35 관측](evidence/etf-exploration-20260930/first-trial-audit-third-call-started-20261007.json)에서
앞선 두 패킷 접수·73,633/250,658자 전달·대기 원인 없음·세 번째 실제 모델 실행을 확인했다.
전체 감사·의미 검토·보고서 등록은 아직 완료되지 않았다. 원래 거절 응답을 재호출하거나
과학 시행을 다시 실행하지 않았다. 후속 응답의 접수와 보고 단계도 계속 관측한다.


14:54:51 KST에 일곱 번째 패킷이 정상 자동 접수됐다. 모든250,658자가 전달·접수됐고
14:54:52에 정상 최종 감사 호출8dbf96d1이 실제 시작됐다.
[전체 바이트 전달 관측](evidence/etf-exploration-20260930/audit-all-bytes-delivered-final-call-running-20261007.json)에서
대기/오류 없이7패킷 완료와 최종 호출 실행을 확인했다. 판정의 접수와 qlab 대조, 의미 검토 및
보고서/실제 게시는 아직 남아 있다. 원문을 변경하거나 독립 판정을 operator가 쓰지 않았다.

## 최종 독립 감사 접수·검증 확인

15:05:46 KST에 최종 공식 응답8dbf96d1이 완료·접수됐다. 회사의 정상 경로가 생성한
고정 qlab37abfceb 검증 파일에서 **pass**, violation0/receipt violation0을 확인했다.
감사 원문·HTML·원문 receipt·package.json의 실제 SHA는 검증 영수증과 모두 일치한다.
검증 파일 SHA는 `4e8c5645a8a69e2ad44471ac96d43cff62f3ff1c63dda4ab590a8d2c2f66b185`다.
[실제 검증·후속 진행 관측](evidence/etf-exploration-20260930/first-trial-independent-audit-verified-meaning-running-20261007.json)을 보존했다.

정상 서비스가 financial_strategist의 meaning stage8987ff99를 생성했고 실제 읽기 호출들이
계속 완료되고 있다.15:13:06 관측에서 이 단계는 running/error 없음이다. 의미 판정·보고서
등록·실제 Slack 전달은 아직 완료되지 않았다. Root는 수익 수치나 과학 결론을 작성하지 않았다.
`read-first-trial-publication-progress-20261007.py`는 고정 trial의 검증 SHA, 자료 읽기 진행,
보고서 등록과 실제 director/outbox 전달 영수증만 읽는다. 모든 SQL은 read-only transaction이다.
이 통과 판정은 승인된 탐색 연구 범위이며, 역사적 알파·확증·운영 배치 승인이 아니다.

15:20:22에 meaning의 필수21개 파일을 모두 읽은 것을
[관측](evidence/etf-exploration-20260930/first-trial-meaning-all-evidence-read-next-call-running-20261007.json)했다.
이 관측만으로 다음 호출을 최종 판정 작성이라고 단정할 수 없다. 이후 실제 응답들은 승인 과제,
미션 이력과 이전 거절 사유의 추가 허용 원문을 읽었다.15:30:11 관측에서79개 읽기 조각과
mission/history.json의 전체215,342자 읽기가 완료돼 있다.15:31:22의 실제80번째 호출9e2bf588은
running이며 의미 판정·보고서 등록·Slack 전달은 아직이다.
[응답 대기 관측](evidence/etf-exploration-20260930/first-trial-meaning-response-pending-20261007.json)을 보존했다.
후속 proposal의 내용과 완료 여부는 실제 접수로만 확인한다.

## 해석 인용 계약의 실제 보류와 구체적 복구 검토

15:32:52에 meaning의80번째 공식 응답이 완료·접수됐다. 최종 게시 대조에서 test 근거가
일반 mission/history·challenge·rejection 파일6개를 인용해 `Test conclusion cites an artifact outside
the validated package`로 audit가 보류됐다. 의미 단계가 읽을 수 있는 경로와 판정에 인용할 수 있는
검증 묶음 경로가 다르다. 감사의 pass와 실제 파일 SHA는 유지되지만 보고서는 아직 등록되지 않았다.
[원인 관측](evidence/etf-exploration-20260930/first-trial-meaning-citation-contract-stop-20261007.json)을 보존했다.

API128MiB 안의 별도 검사 프로세스는 메모리 초과로 두 번 종료됐으며 DB 효과는 롤백됐다.
kernel06:40:42/06:41:29UTC 기록과 기존 stage/80turn/79read 보존을 확인했다. 앱은 계속 실행됐다.
API의 같은 immutable image·보호된 환경·volume과 DB 공통 network를 가진 한시적256MiB 컨테이너로
검사를 옮겼다. 환경값은 호스트의0600 임시 파일에서만 Docker에 전달하고 종료 후 삭제한다.
현재 운영 서비스의 메모리·권한·모델을 바꾸지 않는다. raw max→실제 요청 xhigh 변환도 기존대로 유지한다.

15:49:17의 [실제 PostgreSQL 롤백 검사](evidence/etf-exploration-20260930/first-trial-meaning-citation-correction-probe-20261007.json)가 통과했다.
[한정 operator](evidence/etf-exploration-20260930/repair-first-trial-meaning-citations-20261007.py)와
[root 검토](evidence/etf-exploration-20260930/first-trial-meaning-citation-correction-review-20261007.json)에 따라
같은 meaning attempt1의 기존 proposal을 그대로 남기고 정확한 인용 규칙/읽기 alias/기존 proposal을
다음 prompt에 넣어 일반 보완 turn81 한 건만 만든다. 기존 요청·응답·읽기·audit 판정/범위·job/예약과
서명 예산은 보존된다. 최종 인용·반론·판정은 독립 직원이 보완하며 root가 원문을 수정하거나
판정을 쓰지 않는다. 게시 조건은 유지한다. 운영 적용과 보완 응답의 실제 수신은 아직 확인 전이다.
