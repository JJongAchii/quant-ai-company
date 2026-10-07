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
