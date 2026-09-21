# 지속형 자율 연구 — 구현 검증과 배포 검토

**구현·검증·워커 준비 완료. 운영 활성화와 첫 실제 연구 인수는 대기 중이다.**
기준일 2026-09-21. 구현 승인 `Implement the plan.` / qws `fcec7879dbf7`.
새 과학 실험을 실행한 수는 0이다.

## 검토할 변경

회사 저장소의 PostgreSQL/Temporal 경로에 가설·독립 반론·수정/선택·개발·실험·해석·최선·
감사·보고·후속 연구를 연결했다. 일반 대화의 8회/40개 제한은 승인된 연구 단계를 끊지 않는다.
기술 실패는 backoff와 대사로 재개하며 횟수 상한을 두지 않는다. 사용자 업무를 우선하고,
실행 중 실험은 강제 선점하지 않는다. 독립 감사가 확인되기 전에는 성과를 일반 대화에 공개하지 않는다.

director·금융전략·연구·검증은 Astra max, 개발은 Sol max, 데이터는 Terra high 정책을 유지한다.
전문 절차와 직원별 평가 피드백을 연구 문맥에 연결하고 반복된 계약 오류를 기존 개선 BOT에 남긴다.
핵심 가설·반론·선택은 연구 스레드에, 최종 보고·필수 결정은 소유자를 태그해 게시한다.

기존 PR #42의 P11 승인·복구·실제 인수 변경과 최신 main의 Reporter·maintenance 변경을 함께
포함한다. PR #42를 별도 중복 병합할 필요는 없다. 이번 PR의 후속 문서 커밋과 실제 시험한
실행 코드 pin은 구별한다.

## 고정한 버전

| 대상 | 정확한 commit |
|---|---|
| 회사 실행 코드 | `6d786b67c0da0c4ca05448536a6145147f9408d5` |
| 연구 adapter / 독립 qlab | `857e4243c54d86067c2bf8519eee1162cbd02189` |
| qdata | `d6d7d0ed066ec49541e9acdd657c9ec5692ffc52` |
| 통합한 회사 main | `c5de4c789a5ce6b609f85b7114776481e4040944` |

[release/identity.json](evidence/autonomous-research-20260921/release/identity.json)에 source bundle,
공개 실행 프로필, Python·라이브러리 mount와 6개 입력 파일 식별자를 고정했다. 운영 배포가
다른 commit을 선택하면 별도 정확한 pin으로 release를 다시 준비해야 한다.

## 실제 검증

| 검사 | 결과·실제 범위 |
|---|---|
| 회사 전체 회귀 | **985 passed, 5 skipped, 1 deselected**; lint 통과. 실제 일회용 PostgreSQL·로컬 Temporal 사용 |
| 누적 과학 범위 종료 | 주기 재개 후에도 전체 budget 보존, 마지막 해석·감사 완료 후 대기, 기존 manifest 직렬화 보존 |
| ETF producer → 회사 consumer | 합성 입력 **57개 통과**, 평가·가격 결손·입력/해시·정규 로그 경계 포함 |
| 실제 3070 protocol | bubblewrap 2단계, network/home/env/Git 차단, 업로드 ACK 유실·poller 재구성 후 중복 실행 차단 |
| 실제 3070 ETF 연결 | 최종 두 repo pin, 실제 NumPy/pandas/Arrow·Parquet·보호 evaluator, 자격/평가 두 단계·ZIP consumer 통과 |
| 연구 저장소 전체 gate | 3070 `make gate` **exit 0, 83.97초**; exact qdata lock·단위·누수·감사·원장·control 검사 |
| 회사 독립 감사 r2 | pass, 28파일 scope `0c6e83e09e69`; 실제 HTML receipt·qlab.control 검증 pass |
| ETF 독립 감사 r2 | pass, 19파일 scope `9621c244eb27`; 실제 가드 21개·HTML receipt·qlab.control 검증 pass |
| 입력 보존 | 3070의 warmup/dev 6파일, 총 109,333,328바이트 SHA 일치. 해시만 확인, 실제 성과 계산 없음 |
| 워커 release 준비 | 실제 격리 clone·AST·import·설정 roundtrip 통과. active config 동일, 서비스 재시작 없음 |

전체 회사 검사의 skip에는 opt-in 3070 검사와 live 구독 검사가 포함된다. 실제 qlab을 연결한
감사·발행 통합은 로컬에서 실행했고, GitHub CI의 private qlab 부재 skip과 구별한다.
3070 테스트의 수치는 합성 fixture이며 전략 성과로 해석하지 않는다. runtime fixture의
Pydantic 임시 객체 직렬화 경고와 기존 FastAPI/Starlette 경고는 기록했다.

원본 로그·영수증:
[회사 검사](evidence/autonomous-research-20260921/checks-6d786b67.json),
[회사 로그](evidence/autonomous-research-20260921/suite-6d786b67.log),
[ETF gate](evidence/autonomous-research-20260921/etf-gate.json),
[gate 로그](evidence/autonomous-research-20260921/etf-gate.log),
[최종 실제 ETF 워커](evidence/autonomous-research-20260921/3070-etf-final.json),
[회사 감사 검증](evidence/autonomous-research-20260921/company-audit-r2-control.json),
[ETF 감사 검증](evidence/autonomous-research-20260921/etf-audit-control.json).

연구 레포의 Mac pre-push hook은 3070에서 동일한 필수 `make gate`가 통과한 뒤 명시적으로
건너뛰었다. Mac에서 ML 테스트를 실행하지 않는 워크스페이스 규칙을 따르며 GitHub CI는
다시 같은 gate를 실행한다. 기존 다른 연구의 누락 산출물 감사는 gate의 범위 밖 경고로
기록돼 있으며 이번 판정으로 그 연구를 다시 검증했다고 주장하지 않는다.

## 발견하고 수리한 문제

- 구형 회귀 fixture가 새 pin·승인 검증 진입점을 빠뜨려 전체 검사에서 실패했다. 실제 운영
  확인을 완화하지 않고 fixture를 맞춘 뒤 전체 검사를 다시 통과시켰다.
- Git bundle 생성에 광고된 ref 대신 raw SHA를 써 실제 워커 검사가 실패했다. HEAD ref와
  동일 commit 확인으로 고쳤다.
- sandbox가 먼저 여는 stdout/stderr를 ETF adapter가 기존 산출물로 오인했다. 정규 로그
  2종만 허용하고 기존 결과·symlink·directory는 계속 거절한다. 실제 워커 재검사로 확인했다.
- 주기별 budget만으로는 등록 가설 소진 후 잘못된 제안을 반복할 수 있었다. 명세의 선택적
  누적 과학 budget을 추가해 해석·감사·보고 뒤 새 명세를 기다리도록 했다.

이전 실패 기록과 이전 독립 판정은 보존했다. 새 판정의 `supersedes`로 승계하며 digest를
손으로 갱신해 통과시킨 기록은 없다.

## 남은 실제 인수와 결정

[배포·복구 절차](../runbooks/autonomous-research.md),
[첫 연구 명세](FIRST-AUTONOMOUS-RESEARCH-BRIEF.md)를 검토한 뒤 운영 활성화와 새 명세를 승인한다.
첫 명세는 국내 ETF 피처 6개, 개발 2014~2026년 8월, 편도 10/30bp, 주기당 4회·누적 6회를 제안한다.
이번 준비가 회사 공통 호출 한도나 기술 재시도 한도를 되살리지 않는다.

운영 서버의 새 Docker target 빌드·활성화, 실제 구독 직원들의 최소 2회 연구, 사용자 Slack
승인, 실제 새 성과 보고·후속 선택은 **미수행**이다. 고정 P11의 과거 성공으로 대신하지 않는다.
실제 poller 프로세스 강제 종료는 이번 합성 protocol 검사에 포함되지 않았다.

단일 2GB Lightsail의 장시간 연구·보고 부하와 실제 구독의 감사 문서 처리량은 첫 실행에서
관측한다. 3070이 꺼지면 연구 계산은 대기한다. 글로벌·가상자산 실행, 임의 모델 학습,
paper/live 운용, 모든 공개 시스템보다 우수한 성과는 이 완료 범위에 없다.
