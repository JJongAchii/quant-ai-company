# 첫 회사 연구: 국내상장 ETF 월간 파일럿

상태: **승인 명세로 3070 기준선 실행을 시도했고, 보유 ETF의 가격·종료 현금흐름 근거 부재로 차단됐다**. 전체기간 성과와 후보 성과는 미측정이다.

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
- `evidence/research-pilot-execution-blocked-20260918.json`: 실제 3070 차단 원인·identity·원천 대조와 공식 공시 확인.
- `evidence/research-pilot-result-dispatch-20260918.json`: 차단 결과의 총괄·데이터·국내연구 검토 접수.
- `evidence/research-pilot-artifact-verification-20260918.json`: 회수한 38개 artifact digest와 승인·실행 identity 대조. 독립 누수 감사는 아니다.
- `evidence/research-pilot-result-verified-20260918.json`: 실제 3개 검토 작업·3개 artifact·7개 Codex turn·9개 Slack 게시 readback과 총괄 최종 태그.

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

승인된
`quant-lab` 연구 명세 커밋은 `8f2403986463714c1cf39ac748ce39d7b8395faa`다.
문서 SHA-256: `0a4ce1555e4e040d5af6977ce72be2878594c39d160e453de20d5a4cd7911cf6`.
원본 제안 문서는 수정하지 않고 별도 `APPROVAL.json`으로 승인 상태를 기록한다.
회사 프로젝트에도 `operator_discovery_approved` 이벤트와 실제 승인 출처
`operator:etf-discovery-approved-20260918`을 등록했다. 이후 구현·3070 qualification·기준선 실행·회수까지 수행했으며, 아래 데이터 차단을 확인했다.

## 승인 후 실제 실행 결과

- 실행 코드: `c736cb2075405f708e2aae448c8107122b99a2d1`.
- 호스트: `worker` / `DESKTOP-5T00NAF` / RTX 3070. 다른 작업과 분리한 checkout에서 실행했다.
- 초기 warmup qualification과 고정 코드·config 재qualification은 모두 성공했다. 각 단계는
  2012–2013년만 읽었고 성과를 계산하지 않았다. 최종 워커 인과성·회계 검사 19개가 통과했다.
- 개발 입력은 S3 원천 ETag와 제한된 snapshot SHA-256으로 고정했다. 모든 실행 결과는
  rsync로 회수했다. qdata/S3 원본을 수정하지 않았다.
- 기준선 B01을 1회 시도했으나 전체기간 성과 산출 전에 차단됐다. 성과를 산출한 실행은 0회,
  후보 실행은 0회다. 부분기간 CAGR도 계산하거나 저장하지 않았다.

최초 평가 불가일은 **2015-02-23**이다. 보유 8종목
`091200`, `104580`, `105020`, `161520`, `161530`, `161540`, `161550`, `166060`은
승인 개발 snapshot에서 2015-02-17이 최종 관측일이다. 문제 날짜에 다른 ETF 166행은 존재한다.
가격·메타 원천 대조에서 조인 손실이나 전체시장 휴장일 누락으로 설명되지 않았다.
원본 `blocker.json`은 직전 실제 주문·미체결과 code/config/host identity를 보존한다.
해당 output에는 `start.json`, `run_meta.yaml`, `blocker.json`만 있고 성과 계열·objective는 없다.

진단용으로 확인한 [ARIRANG 4종의 공식 안내](https://www.plusetf.co.kr/customer/notice/detail?n=17204)는
2월 23일 상장폐지, 3월 4일 해지상환금 지급을 예정했다.
[TIGER 브릭스의 KRX 공시](https://kind.krx.co.kr/external/2015/01/07/000083/20150107000172/68629.htm)는
같은 상장폐지일과 2월 24일 지급을 예정했다. 이는 **5종목의 공지된 일정** 확인이며,
8종목 모두의 실제 지급액·확정 지급일 검증이 아니다. 이 공시를 backtest 입력으로 추가하지 않았다.
마지막 종가로 일괄 현금화할 근거도 확보하지 못했다.

승인 명세의 "근거 있는 평가·청산 처리가 없으면 해당 실행은 차단한다" 조건을 적용했다.
결과는 `blocked / unmeasured`이며 `no-candidate`, 경제적 기준 미달 또는 전략 손실 판정이 아니다.
완료된 성과 artifact가 없어 D2 독립 누수 감사와 성과 HTML 차트는 생성하지 않았다.
이는 19개 구현 검사가 독립 연구 감사나 전략 적격성을 대신한다는 뜻이 아니다.

## 재개 조건과 검증의 한계

해당 종목의 식별 연속성·종료 사건, 거래정지 기간의 근거 있는 평가, 실제 주당 환매금액과
지급일, 조정가격 단위와 현금 정산의 일관성을 확보해야 한다. 이 입력과 처리 코드를 고정해
qualification을 다시 통과한 뒤 같은 기준선부터 재개한다. 유니버스·기간을 바꾸는 경우에는
새 범위 승인이 필요하다. 현재 연구 입력과 qdata 원본은 그대로 유지했다.

연구 레포의 전체 `make gate`는 무관한 `strategies/scalerank/v03/uv.lock` 최신성 검사에서
막혔다. root lock 검사·파일럿 ruff·인과성 검사는 통과했다. 레포 규약이 허용하는 명시적
`--no-verify`로 연구 브랜치만 전송했고 원인과 로그를 연구 증거에 남겼다.
전체 gate 통과나 main 병합·운영 배포로 표시하지 않는다.

차단 증거를 회사 공통 source로 등록해 실제 총괄·데이터·국내연구가 검토했다.
3개 작업과 3개 artifact, 실제 Codex turn 7개가 완료됐고 9개 게시를 Slack에서 다시 읽어
본문과 직원 identity를 대조했다. 총괄의 최종 답변에는 소유자 태그가 붙었다.
[같은 스레드의 최종 보고](https://achiisquantresearch.slack.com/archives/C0C2B9EUEGM/p1789687609496389?thread_ts=1789633942.673909&cid=C0C2B9EUEGM).
이 검토는 제공된 실행 증거를 읽고 연구 상태·재개 조건을 대조한 것이며,
직원들이 원천 레이크나 공시를 다시 조회한 독립 감사로 표시하지 않는다.

연구 코드·근거는 [quant-lab PR #16](https://github.com/JJongAchii/quant-lab/pull/16),
회사 협업·인수 기록은 [quant-ai-company PR #30](https://github.com/JJongAchii/quant-ai-company/pull/30)에 있다.
