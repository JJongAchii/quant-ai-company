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
실제 고정 qlab 경로를 지정한 전체 회사 검사를 실행 중이다. 전체 lint는 통과했다.
준비와 운영 적용 및 응답 대사 영수증은 완료 후 이 문서와 같은 저장소에 추가한다.
