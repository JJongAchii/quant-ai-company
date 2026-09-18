# 첫 회사 연구: 국내상장 ETF 월간 파일럿

상태: **B01 차단 뒤, 사용자 승인으로 종료자료를 회수하고 첫 8종목의 부분 입력 연결·결손 검사를 완료했다. 3종목의 역사 규약과 지급액·종목 대응 해석이 남아 있다.** 전체기간 성과와 후보 성과는 미측정이고, 기준선 재실행은 아직 시작하지 않았다.

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

## 종료자료 보강: 현재까지 확인한 사실

사용자의 후속 승인 원문은 **"오키 진행해"**다. 직전 제안은 종료 ETF의 실제 환매금액·지급일과
정지기간 평가 근거를 보강한 뒤 같은 기준선을 재실행하는 것이었다. 이 승인과 기존 명세의
digest를 회사 증거와 운영 source에 연결했다. 연구기간·유니버스·비용·3070 실행 조건은 그대로다.

- KRX KIND에서 첫 8종목 모두의 **상장폐지 공시와 해지상환금 지급 공시**를 회수했다.
  지급 공시에 적힌 상환기준가격·과표기준가격·좌당 과세분배금을 각각 보존했다. 이들은 서로
  다른 필드이며, 개인 증권계좌의 세후 입금액을 확인한 것은 아니다.
- KOFIA의 역사 기준가격 조회 80건에서 실제 NAV 48행을 회수했다. 원문 XML과 정규화한
  행 digest를 함께 보존했고, 독립 조사 결과와 정규화 값도 대조했다. 조회일과 역사 기준일을
  혼동하지 않는다. 조회 결과가 없는 날짜를 0 또는 직전값으로 채우지 않았다.
- 개별 신탁계약 5개(ARIRANG 4종·KODEX주식&골드)의 1좌 원 단위, 직전일 순자산을 사용하는
  NAV 산정 및 일별 공표 규정을 확인했다. KOSEF IT·KODEX Brazil·TIGER 브릭스의 당시 전체
  규약은 아직 회수하지 못했다. 규약상 공표 의무는 실제 과거 도착시각이나 정정 전 원본의
  보존을 입증하지 않으므로 별도 한계로 기록한다.
- 종목명과 운용사, KR7 거래소 ISIN, KR5 펀드코드 사이의 대응을 별도로 기록한다.
  특히 KOFIA의 ARIRANG 조선운송 표기에는 `송`이 빠져 있어 단순 문자열 일치로 처리하지 않는다.

입력 인벤토리 P02는 고정한 가격·메타 1,659,509행을 손실 없이 대조했다. 개발기간에 관측이
끝난 종목은 247개이고, 과거 보유 가능성을 넓게 잡은 점검 상한은 209개다. **209개를 실제
보유했다는 뜻은 아니다.** 연속 관측·변동성·월말 선택을 검사한 수치도 아니다. 실제 B01에서
문제가 확인된 보유 종목은 위의 8개다.

P02의 `0/209 ready`는 실행용 보충 입력을 주지 않은 상태에서의 결과다. 원천 자료가 209종목
모두에 없다는 판정으로 사용하지 않는다. 후속 부분 입력 연결 결과와 구별해 보존한다.
이 단계는 macOS에서 수행한 원천·스키마 점검이며 학습·백테스트 실행이 아니다.
완료 성과 실행 0회, 후보 0회, 기존 기술 실패 3/6은 유지했다.

자료 수집 코드·입력 계약·검사 및 실행 증거는 연구 저장소에 있다. 원본 레이크를 수정하지 않고
별도 보충자료로 보존한다. 전체 결과의 의미는 계속 `blocked / unmeasured`다.

후속 P03은 실제 보유 8종목을 B01 차단 증거에서 읽고, 지급 8건·상폐 8건·정지 6건·NAV 48건을
부분 입력으로 연결했다. 정지 공시의 모든 항목을 파서가 얻은 것은 아니므로 8건이라고 늘려
표시하지 않았다. 원문 221개를 복제·해시 대조했으며, 69개 source와 70개 event의 구조 검사에서
오류는 0개였다. 코드·기록의 실행 커밋은 `592db87`, 증거 커밋은 `0762a4f`다.

실제 보유 8개의 검사 결과는 아직 `0/8 ready`다. 이유를 다음과 같이 구분한다.

- **외부 원문 미확보:** `091200`, `104580`, `105020`의 당시 단위·NAV 산정·공표 규약.
  조사한 경로에서 확보하지 못한 것이며 모든 외부 출처에 존재하지 않는다는 뜻은 아니다.
- **공시 해석 미완료:** 8종목의 상환기준가격과 투자자 과세 단계의 관계. 개인 증권계좌의
  실제 세후 입금 증명을 discovery의 추가 필수 조건으로 요구하는 것은 아니다.
- **내부 대조 미완료:** 운용사·명칭·규약·가격 자료를 묶은 KR5/KR7 대응 검토. 두 코드를
  함께 적은 단일 문서가 반드시 있어야 한다는 조건은 두지 않는다.

5개 규약 종목의 NAV 날짜·단위·가용 상한·조정계수 검사는 충족하지만, 위 미해결 항목 때문에
실행 입력 준비 완료로 표시하지 않았다. 원문 해시 검사는 경제적 해석의 타당성을 인증하지 않는다.
새 입력과 시뮬레이터의 연결도 아직 구현하지 않았다.

파일럿 검사는 **42개**와 ruff가 통과했다. 메인 스레드에서도 P02/P03 산출물 16개, P03 source
69개의 실제 해시, 정확한 실행 커밋의 코드, 이전 SEARCH 기록 보존을 대조했다. 수집 원문과
부분 입력을 기존 비공개 S3 백업 버킷에 보관하고 다시 내려받아 해시가 일치하는 것을 확인했다.
맥북에만 남는 자료가 아니며 새 서버·버킷은 생성하지 않았다.

관련 회사 증거:

- `evidence/research-pilot-terminal-repair-approval-20260918.json`
- `evidence/research-pilot-terminal-repair-runtime-20260918.json`
- `evidence/research-pilot-terminal-disclosures-20260918.json`
- `evidence/research-pilot-terminal-nav-20260918.json`
- `evidence/research-pilot-terminal-rules-20260918.json`
- `evidence/research-pilot-terminal-unit-review-20260918.json`
- `evidence/research-pilot-terminal-rule-followup-20260918.json`
- `evidence/research-pilot-terminal-artifact-verification-20260918.json`
- `evidence/research-pilot-terminal-source-backup-20260918.json`
- `evidence/research-pilot-terminal-input-backup-20260918.json`
- `evidence/research-pilot-terminal-review-dispatch-20260918.json`
- `evidence/research-pilot-terminal-review-correction-20260918.json`
- `evidence/research-pilot-terminal-review-summary-20260918.json`
- `evidence/research-pilot-terminal-summary-dispatch-20260918.json`
- `evidence/research-pilot-terminal-review-verified-20260918.json`

### 실제 직원 검토에서 드러난 보고 문제

총괄이 데이터·금융전략·국내연구에 각각 위임했고, 세 직원 모두 실제 Codex 작업을 완료하고
artifact를 남겼다. 금융전략은 공통 NAV와 개인별 원천징수를 연결한 해석을 직접 명시와 구분했고,
정확한 개인 세율·계좌 증명을 discovery의 새 조건으로 요구하지 않았다. 데이터는 단일 문서의
KR5/KR7 동시 기재를 필수로 만들지 않고 복합 대응 검토가 필요하다고 기록했다.

국내연구 초안은 전체 8종목의 NAV 48행을 규약 확보 5종목의 자료로 잘못 표현했다. 해당 부분집합은
39행이다. 또한 자기 `read_source` 실행이 DB에 없는데 다른 직원의 조회 결과가 들어온 공유 문맥을
자신의 직접 조회처럼 서술했다. 운영자가 두 오류를 구체적으로 정정했다. 제공된 자료를 읽는 것과
직접 도구를 실행했다는 주장은 다르며, source ID 인용만으로 후자가 입증되지 않는다.

첫 총괄 보고는 긴 자료를 읽다 작업별 8턴 한도에 도달해, 실제로 완료된 세 직원 검토를 종합하지
못했다. 이 보고를 정상 인수로 처리하지 않았다. 운영자가 실제 파일 해시와 DB 작업·artifact를
대조한 짧은 검증본을 새 source로 등록하고, 추가 위임 없는 별도 정정 보고를 접수했다. 이는 보고
복구이며 런타임의 문맥 보존·도구 실행 주체 추적·턴 한도 문제가 코드로 해결됐다는 뜻은 아니다.

정정 보고는 실제 Codex 2턴으로 완료됐다. 이번 검토·정정의 전체 5개 작업, 5개 artifact,
17개 Codex turn과 Slack 20개 게시를 대조했다. 각 게시의 본문·직원 identity 및
[정정 최종 보고의 소유자 태그](https://achiisquantresearch.slack.com/archives/C0C2B9EUEGM/p1789693390164159?thread_ts=1789633942.673909&cid=C0C2B9EUEGM)를
Slack에서 다시 읽어 확인했다. 이전 실패 보고와 초안은 삭제하지 않고 정정 관계를 남겼다.

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
