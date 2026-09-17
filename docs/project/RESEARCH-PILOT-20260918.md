# 첫 회사 연구: 국내상장 ETF 월간 파일럿

상태: **실제 직원 준비·보완 업무 완료, discovery brief 사용자 승인 기록 완료, 3070 실행 준비 중**. 전략 성과는 미측정이다.

- [연구 스레드](https://achiisquantresearch.slack.com/archives/C0C2B9EUEGM/p1789633942673909)
- 회사 프로젝트: `9aac0de4-2b97-5195-a720-287d324234f3`
- 운영 코드: `9fe9786c46c16d7aed2cf0b5fc1bed08d7e3b3a6`
- 연구 작업공간: `company-kr-etf-pilot`, 개인 연구 저장소: `JJongAchii/quant-lab`

## 이번에 확인한 연결 문제

운영자가 Slack에 root 메시지를 직접 게시했지만 DB 프로젝트를 연결하지 않아, 사용자의
"시작하면 되?"는 기존 업무의 후속 입력으로 연결되지 않았다. 이후 멘션만 있는 두 메시지는
멘션을 제거하면 지시 본문이 비어 있다. root 게시 자체는 연구 접수 증거가 아니었다.

사용자가 승인한 해당 스레드를 소유자·채널 범위 안에서 회사 프로젝트로 등록하고, 별도의
operator event와 원문 지시를 남겼다. Slack 서명 이벤트를 재생하거나 사용자 발언을 위조하지 않았다.
이제 해당 스레드의 일반 후속 입력이 같은 프로젝트에 연결된다.

research-center를 기존 허용 채널에 추가했고, 총괄·금융전략·국내연구·데이터 네 앱을 모두
채널에 추가했다. 새 서버나 유료 앱을 생성하지 않았다.

## 실제 작업과 경계

총괄이 세 직원에게 경제적 가설, 연구 명세, 데이터 계약을 각각 위임했다. 직원은 실제 Codex
런타임으로 답하고 원문 출처 ID·artifact를 남긴다. 전체 167만 행 품질 조사 증거는 별도의
lake-surveyor가 조회한 결과를 운영자가 공급한 것이다. 데이터 직원의 자체 조회와 구별한다.

현재 직원 도구에는 shell·3070 제출·백테스트가 없다. 첫 연구의 커밋·워커 실행·회수는 승인된
개인 연구 규약을 사용해 운영자가 연결한다. 이를 회사 직원의 자율 실행 기능으로 표시하지 않는다.

연구 명세 제안은 `quant-lab/docs/research/company-kr-etf-pilot/RESEARCH-BRIEF.md`에 있다.
사용자가 "제안한 명세로 실행"이라고 승인한 기간·기준선·비용·목적함수·탐색 예산을 고정했다.
실제 승인 원문과 frozen brief digest를 연구 저장소와 회사 증거에 보존했다.

## 증거

- `evidence/research-center-thread.json`: root 게시와 사용자 원문 요청.
- `evidence/research-center-channel-config.txt`: 허용 채널 변경과 서비스 재기동.
- `evidence/research-center-dispatch-20260918.json`: 실제 프로젝트/작업 접수.
- `evidence/kr-etf-lake-survey-20260918.json`: 가격·메타 조사, 원천 식별자와 한계.
- `evidence/research-center-brief-correction.json`: 운영자의 공통 명세 보완 요청.
- `evidence/research-center-preparation-verified-20260918.json`: DB 산출물과 Slack 실제 readback.
- `evidence/research-pilot-approval-20260918.json`: 실제 사용자 승인 원문과 명세 digest.
- `evidence/research-pilot-approval-runtime-20260918.json`: 회사 DB의 승인 이벤트·출처 및 Slack 전달 확인.

## 실제 인수 결과

운영 코드의 실제 Codex 호출로 업무가 진행됐다. 8개 작업(모델 호출 없는 초기 등록 1개 포함)이
종료됐고, 7개 artifact와 21개 outbox 게시를 Slack에서 다시 읽어 대조했다. 네 직원의 실제
Slack identity가 참여했고, 총괄의 최종 답변에 소유자 태그가 붙은 것을 확인했다.

첫 연구 초안에는 공통 목적의 이탈과 정의 혼동이 있었다. 국내연구는 초과수익 평균을 primary로
제안했고, 총괄은 참여율 분모를 전일 하루 거래대금으로 요약했다. 오래된 profile과 역사 meta의
범위도 충분히 구별되지 않았다. 최초 배정에 정확한 목적함수·공통 명세 원문을 공급하지 않은
운영자 입력도 원인이다. 직원 전문 절차에 절대수익 원칙이 있어도 이탈이 발생했으므로,
프롬프트가 존재한다는 사실만으로 실제 준수가 입증되지는 않았다.

커밋된 명세를 공통 출처로 공급하고 국내연구·데이터에 각각 한 번 보완을 요청했다. 새 결과는
stress 비용 후 절대 CAGR, ADV20, meta/profile 구분, M1/M2/M3 및 종료 규칙을 반영했다.
이전 artifact는 삭제하지 않고 새 artifact가 상충 제안을 대체한다고 명시했다. 총괄의 최종
통합본에서도 해당 정정을 확인했다. 이는 한 실제 사례의 수정 확인이며 일반적인 오류 자동
탐지·전문성 인증 또는 자율 연구 완주가 아니다.

과학 실행 0회, 독립 누수 감사·성과 HTML 리포트는 아직 없다. 승인된
`quant-lab` 연구 명세 커밋은 `8f2403986463714c1cf39ac748ce39d7b8395faa`다.
문서 SHA-256: `0a4ce1555e4e040d5af6977ce72be2878594c39d160e453de20d5a4cd7911cf6`.
원본 제안 문서는 수정하지 않고 별도 `APPROVAL.json`으로 승인 상태를 기록한다.
회사 프로젝트에도 `operator_discovery_approved` 이벤트와 실제 승인 출처
`operator:etf-discovery-approved-20260918`을 등록했다. 구현·3070 qualification·개발 실행·회수·감사·보고를 진행한다.
