# Reporter 주요 매체 확장

2026-09-21 KST. 사용자는 추가 뉴스 구독비 없이 주요 매체를 늘리되, 아시아·유럽 매체의
지역별 안배는 필요 없다고 정했고 "응 진행해"로 구현과 운영 반영을 승인했다.
국내 경제·산업·정책, 글로벌 경제·거시·금융, 중요한 세계 정세를 우선한다.
발생 지역으로 중요한 사건을 제외하지 않으며 `daily-brief` 정기 브리핑은 이 작업에 포함하지 않는다.

## 등록과 이용 범위

기존 BBC·CNBC·KBS World에 연합뉴스·SBS·이투데이를 추가한 **언론 매체 6곳, 언론 RSS 19개**다.
공식 피드 6개를 합하면 활성 피드는 25개다. 같은 언론사의 여러 피드를 독립 원천으로 세지 않는다.

| 추가 매체 | 피드 | 최종 운영 이미지에서 확인한 상태 |
|---|---|---|
| 연합뉴스 | 경제·마켓·산업·국제 4개 | 최근 기사와 표본 원문·발행 시각·기자 확인 |
| SBS | 경제·국제 2개 | 최근 기사와 `articleBody`·발행 시각 확인 |
| 이투데이 | 경제·금융·산업·국제 4개 | 최근 기사와 `articleBody`·발행 시각 확인 |

[배포 전 10개 피드와 원문 표본](evidence/hot-news-mainstream-qualified-sources.json),
[최종 운영 이미지의 25개 피드 검사](evidence/hot-news-mainstream-final-image-sources.json).
이 검사는 특정 시점의 접근·추출 확인이며 모든 기사의 가용성이나 중요 뉴스 누락률을 입증하지 않는다.

매체가 명시한 개인·비상업 RSS 이용 범위에서 소유자의 Slack 뉴스 읽기를 지원한다.
짧은 사실 요약과 출처 링크를 전달하며 전문·사진·영상 재배포, AI 학습, 상업적 뉴스 공급에
사용하지 않는다. 상업적 콘텐츠 라이선스나 포괄적인 AI 이용 허락을 취득했다는 뜻이 아니다.
[연합뉴스 RSS](https://www.yna.co.kr/rss/index), [저작권규약](https://www.yna.co.kr/policy/copyright),
[SBS RSS](https://news.sbs.co.kr/news/rss.do), [이투데이 RSS](https://www.etoday.co.kr/rss/).

## 직접 연결하지 않은 후보

| 후보 | 이번 확인 및 처리 |
|---|---|
| Reuters·AP | 운영 서버의 홈페이지 일반 HTTPS 요청이 각각 401·403. 별도 계약·API 키 없음. 직접 수집 매체로 등록하지 않음 |
| Bloomberg·FT | 최신 RSS 수신은 성공했지만 표본 원문 403. 본문 수집과 게시 근거로 등록하지 않음 |
| WSJ | 검사한 `RSSMarketsMain.xml`은 2025-01-27 이후 새 기사 없음. 현재 뉴스 피드로 등록하지 않음 |
| MarketWatch | RSS 수신 성공, 표본 원문 401. 직접 수집에 등록하지 않음 |
| 한국경제·매일경제·동아일보 | RSS 제공과 별도로 약관에서 자동 수집 제한을 확인. 이번 자동 본문 수집에서 제외 |
| Yahoo Finance | RSS·표본 본문은 읽혔지만 자동 접근 제한 및 RSS 내용 변경 제한 확인. 자동 요약 원천으로 등록하지 않음 |
| 이데일리 | 공식 홈페이지가 안내하는 RSS는 HTTP. HTTPS 대응 주소 연결 실패. 현재 HTTPS 수집기에 등록하지 않음 |
| CNA·DW·France 24 등 | 사용자의 지역 안배 불필요 결정에 따라 추가하지 않음 |

[주요 후보 기술 검사](evidence/hot-news-mainstream-candidates.json),
[이용 범위 판단 근거](evidence/hot-news-mainstream-source-policy.json).
이는 검사한 경로의 결과이며 해당 매체의 모든 이용 경로가 불가능하다는 선언이 아니다.

## 수집·검색·편집

추가 비용을 발생시키는 뉴스 API·구독·서버 구매 없이 기존 RSS 수집과 ChatGPT 구독을 사용한다.
10분 RSS, 새 자료가 있는 경우 5분 편집, 30분 보완 검색 및 사용자 업무 우선 순위를 유지한다.
검색에서 주요 글로벌 경제 매체가 다룬 사건을 발견하고 허용 매체의 공개 보도를 찾도록 변경했다.
최대 3회 네이티브 검색·6개 후보의 기존 한도, 반환 URL 검증·원문 조회·실제 발행 시각 확인을 유지한다.
검색에 주요 매체 이름을 넣는 것은 해당 매체의 직접 API·원문 연결이나 모든 기사 감시를 뜻하지 않는다.
검색 스니펫과 읽지 못한 원문은 사실의 근거가 아니다.

SBS의 `cooper`·`plink` 추적 매개변수는 기사 식별에서 제외하며 `news_id`는 보존한다.
실제 구독 검색에서 발견한 인쇄용 `endPagePrintPopup.do`도 표준 `endPage.do`로 정규화한다.
다른 호스트의 동명 매개변수는 변경하지 않는다. 실제 RSS 형식과 검색 URL로 하나의 DB 기사가
생기는 회귀 검사를 추가했다. 등록된 일반 매체의 귀속 보도, 독립 원천 확인, 민감 주장 보류,
사건별 중복·후속 처리와 불확실한 외부 효과 영수증 보존은 그대로다.

신규 피드의 최초 lookback은 120분으로 설정한다. 기존 피드의 정상 수집 신선도 상한은 24시간이다.
피드 전체 기사 중에는 회사 홍보·생활 정보도 있으므로 실제 원문을 읽은 편집 단계에서 중요도를
판단한다. 소스 확장으로 검토 대기량이 늘 수 있으며 모든 기사 즉시 전달을 보장하지 않는다.

## 검증과 운영 상태

먼저 수행한 로컬 회귀 검사는 480건 통과했다. 이미 승인되어 운영에 반영된 PR #40의
연구 작업 연결을 최신 main에서 병합한 뒤, 최종 코드로 **648 passed, 2 skipped, 1 live deselected**를
확인했다. 실제 PostgreSQL 14·Temporal을 사용하며 이 회귀 검사의 모델·Slack은 모의다.
lint 및 PostgreSQL 16을 사용하는 GitHub CI도 통과했다.
[초기 회귀 검사](evidence/hot-news-mainstream-tests.json),
[통합 회귀 검사](evidence/hot-news-mainstream-integrated-tests.json),
[CI](https://github.com/JJongAchii/quant-ai-company/actions/runs/35562896990).

배포 전 실제 기사 5건의 구독 검토는 홍보성 자료 2건 제외, 정치적 의혹·교전 당사자 주장·익명
취재 3건 보류로 완료됐다. 검색도 실제 네이티브 검색을 실행하고 공개 매체 후보를 반환했다.
이 미리보기는 Slack 0회이며 뉴스 게시 성공으로 세지 않는다. 미리보기에서 발견한 SBS 인쇄용
URL 중복은 위 정규화와 회귀 검사로 수정했다.
[실제 구독 미리보기](evidence/hot-news-mainstream-codex-preview.json).

운영 애플리케이션 커밋은 `0e89d998022abfb626cf29a4ff99012cd83ffb74`다. 초기 배포 묶음의
최상위 경로 형식 오류는 교체 전 검사에서 중단됐으며, 기존 서버는 그대로 유지됐다.
`release/` 접두사와 압축 해제 검사를 보완하고 최신 main을 포함해 다시 빌드했다.
백업을 남기고 배포했으며 PostgreSQL 컨테이너, 직원 11명의 설정, Slack 인증과
기존 연구 연결 설정·마운트를 보존했다. 8개 서비스, 뉴스 플래그 3개와 자동 실행 타이머를 확인했다.
[배포 준비와 복구](evidence/hot-news-mainstream-stage.json),
[배포 영수증](evidence/hot-news-mainstream-release.json),
[서버·연구 연결 보존 검사](evidence/hot-news-mainstream-host-health.json).

사용하지 않는 Docker 빌드 캐시 4.749 GB를 정리해 디스크 여유를 2.1 GB에서 5.8 GB로 확보했다.
태그가 있는 배포 이미지 68개와 실행 중인 컨테이너는 보존했다.
[캐시 정리 영수증](evidence/hot-news-mainstream-cache-cleanup.json).

새 피드 10개 모두 자동 수집에 진입했다. 14:09 KST 첫 자동 구독 검토는 일반 채용 공고를
제외하고 **추가한 세 매체의 기사 4건을 실제 Slack에 전달**했다. 서울 주택정책에 관한 SBS
기사 2개는 하나로 묶었고, 정치적 의혹은 제외했다. 연합뉴스의 원화국제결제망 시범운영 보도와
이투데이의 가격정보 서비스·산업 원료 등록 보도도 매체 귀속을 표시해 전했다.
이는 해당 기관의 원문을 직접 읽었다는 주장이 아니다.

4건 모두 Slack의 본문·Reporter 발신자·`client_msg_id`·발송 시각이 DB 영수증과 일치했고
발송 시도는 각 1회였다. 조회한 채널 기록에서 각 ID는 1회 확인됐다. 수동 테스트 게시물은 0건이다.
이 관찰을 영구적인 exactly-once 전달 보장으로 확대하지 않는다.
[자동 검토와 발송 영수증](evidence/hot-news-mainstream-delivery.json),
[실제 Slack 대조](evidence/hot-news-mainstream-slack-match.json),
[원화국제결제망 게시물](https://achiisquantresearch.slack.com/archives/C0C2J1SSX09/p1789967348007399).

이투데이 기사 1건은 피드와 원문의 발행 시각 충돌로 게시 근거에서 제외됐다. 등록된 6개 분야
모두 최근 기사를 제공하는 일반 언론 피드가 있으나, 이 검사는 개별 기사 내용에 대한 분야 판정이나
중요 뉴스의 완전 수집을 증명하지 않는다. 첫 자동 게시 4건의 분야는 경제·금융·산업이며,
중요도 판단의 정밀도와 장기 누락률은 이번 짧은 운영 관찰로 측정하지 않았다.
[분야별 가용성과 제외 기사](evidence/hot-news-mainstream-coverage.json).

14:17 KST까지 추가 피드 10개 모두 최초 성공 후 600초 이상 지난 다음 자동 수집에 성공했다.
자동 구독 검토는 2회 완료됐고, 수집·편집·검색 Temporal 워크플로는 모두 실행 중이며
관찰한 실행 이력의 activity 실패는 0건이다. 발송 대기·결과 불명도 0건이다.
이 관찰은 첫 두 주기의 인수이며 장기 가용성 검사는 아니다.
[반복 수집·편집 인수](evidence/hot-news-mainstream-continuity.json).
