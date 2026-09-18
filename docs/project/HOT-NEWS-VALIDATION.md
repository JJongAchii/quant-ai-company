# Reporter 구현과 확인 범위

이 문서는 운영 활성화 전의 역사적 검증 기록이다. 이후 실제 배포·Slack 게시 결과는
[Reporter 운영 활성화](HOT-NEWS-ACTIVATION.md)에 기록했다.

2026-09-19 KST. `hot-news`의 연속 뉴스 수집·검토·사건별 발송과 **Reporter / @reporter** 앱 설정을
구현했다. 실제 구독 모델로 원문 기반 게시 후보 생성까지 확인했다.
**운영 배포, Reporter 앱 설치, 실제 Slack 발신은 수행하지 않았다.**
구현과 검토 대상은 [draft PR #33](https://github.com/JJongAchii/quant-ai-company/pull/33)에 있다.

## 구현

- 약 10분 간격 RSS/Atom 수집, 기사 버전·시각·원문 해시·수집 오류를 PostgreSQL에 보존.
- 모델 작업과 별도 Temporal 수집 queue, 편집은 기존 사용자 우선순위·공통 예산·동시 실행 1개 사용.
- 동결된 요청과 근거를 바탕으로 typed 검토 제안 검증, 보류·제외·미리보기·발송 대기를 구분.
- 사건별 최초 게시 및 후속·정정 스레드, 원문 인용과 링크, 발표·확인 시각, Reporter 후속 질문.
- 설정 변경·만료·본문 부재·독립 근거 부족 시 발송 제한. 결과 불명 호출·발신은 자동 반복하지 않음.
- 기본 설정에서 수집·발송은 꺼져 있으며, `daily-brief` 정기 발송은 포함하지 않음.

설계는 [ADR 0027](../adr/0027-continuous-news-reporter.md), 설정·소스·명령은 [운영 안내](../news.md),
전용 앱은 [reporter.json](../../slack-apps/reporter.json)에 있다.

## 증거

| 검사 | 실제 사용한 것 | 결과와 한계 |
|---|---|---|
| [소스 연결](evidence/hot-news-source-probe.json) | 공개 인터넷 RSS | 연준·BBC·뉴시스 국제/경제·KBS World 5개 피드 성공. ECB는 로컬 TLS 검증 실패 |
| [첫 모델 검사](evidence/hot-news-live-codex-initial-hold.json) | 공개 RSS·연준 원문·임시 PostgreSQL·공식 Codex 구독 | 웹 메뉴가 본문을 밀어낸 잘림을 모델이 발견해 보류. 게시 후보 생성은 확인하지 못함 |
| [수정 후 모델 검사](evidence/hot-news-live-codex.json) | 공개 RSS·기사 영역 추출·임시 PostgreSQL·공식 Codex 구독 | 원문 1,055자에서 인용·한국어 사실·의미를 포함한 후보 1건 생성, 검증 후 미리보기 보존. Slack outbox 0건 |
| [자동 검사](evidence/hot-news-tests.json) | 실제 로컬 PostgreSQL·Temporal, 모의 모델·모의 Slack | 446 passed, 2 skipped; ruff·wheel 통과. skip 사유·파일 해시는 영수증 참조. 실제 Slack/클라우드 배포 증거 아님 |

실제 모델 검사는 기존 공식 ChatGPT 인증을 이용했다. API 키나 새 유료 API로 전환하지 않았고,
Codex 자식 프로세스에 Slack·DB 자격 증명을 전달하지 않았다. 두 번의 검사는 각각 1회 호출이었다.
자격 검사에서는 실제 피드의 가장 최근 원문을 실제 날짜 그대로 사용하되, **72시간 신선도 범위와
합성으로 설정한 재개 상태**를 썼다. 기본 운영 범위 24시간 및 첫 기동 120분을 통과한 실시간
속보라고 표시하지 않았다. 실제 모델 응답·인용·원문 해시를 보존하며 첫 보류 기록을 덮어쓰지 않았다.

회귀 검사에는 원문 추출 실패, 피드 중복·변경·오래된 기사, 제한된 소스의 본문 제외,
가짜 인용·원천 중복·미등록 사건 거절, 채널·소유자·정책 변경, 미리보기의 지연 발송 방지,
구독 한도와 사용자 업무 우선순위, 모델/Slack 결과 불명, 원 글·후속 게시와 Reporter 스레드
라우팅이 포함된다. 실제 Temporal에서 요청 동결 후 실패·재시도, 이력 재생, worker 재시작 후
타이머 복구, **모델 작업 중 별도 수집 queue의 진행**을 검사했다.

재현 명령:

```bash
uv sync --frozen
uv run --frozen ruff check .
uv run --frozen pytest -q tests deploy/test_deployment_contract.py evals/test_financial_fixtures.py
uv run --frozen quant-company news probe --output .local/news-sources.json
uv run --frozen python scripts/qualify_news.py --live --output .local/news-live.json
```

검사용 PostgreSQL은 이 작업에서 만든 전용 로컬 cluster와 임시 DB다. 실제 사용자 DB에는
마이그레이션·기사·업무를 쓰지 않았다. 이 checkout에는 선택적 `../quant-data`가 없으므로
기존 lockfile을 사용하는 `--frozen`으로 실행했다. 연구 저장소는 수정하지 않았다.

## 활성화 전 남은 일

1. 실제 `hot-news` 채널 ID와 허용 사용자·앱 권한을 확인하고 Reporter 앱을 설치·초대한다.
   서버 비밀 저장소에 앱 자격 증명을 추가하고, 마운트된 역할 파일에도 Reporter를 추가한다.
2. 민간 매체별 자동 요약·Slack 전달 이용 범위와 본문 추출을 확인한다. 현재는 메타데이터
   수집 상태이며 게시 근거로 사용하지 않는다. ECB의 TLS 실패도 운영 환경에서 다시 확인한다.
3. 소스 구성·앱·미리보기 결과를 검토한 뒤 운영 코드와 설정을 반영하고 실제 채널에서
   새 글·후속 스레드·사용자 질문·재시작·발송 영수증을 인수한다.

공식자료 한 건의 성공이 포괄적인 글로벌 뉴스 커버리지나 장시간 가용성을 뜻하지 않는다.
인용의 의미상 지지, 통신사 전재 판별, 사건 통합은 모델 판단이 남아 있다. 운영 경보,
장기 보관 정책, 기사 누락률·중요도 선별의 장기간 평가는 아직 검증하지 않았다.
