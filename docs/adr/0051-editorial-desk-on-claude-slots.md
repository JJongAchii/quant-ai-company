# 편집 업무를 Claude 구독의 자체 실행 자리로 옮긴다

## 배경

- 소유자(2026-11-17): Codex 구독을 낮추려 한다. "가장 최적의 방향으로 진행해줘. 또한 트렌드나 quant scout 등 너무 좋은 모델로
  안해도되. 브리핑이나 영상 등이 지금은 가장 중요한 상태여서 그거만 문제없이 가장 좋은 퀄리티를 뽑아낼 수 있기만 하면되."
- 운영 Codex 영수증(최근 7일, 읽기 전용 집계): 약 1,870건 중 뉴스 편집 801, 뉴스 선별 643, Quant Scout 320, 브리핑 55,
  트렌드 23, 직원·검색 약 25, 영상 그림 22. Claude 구독(Max 20x)은 같은 기간 약 280건으로 대부분 영상이다.
- Claude 실행기는 CLI를 한 번에 하나만 돌렸다(`.runtime.lock` 하나). 영상은 매일 07:10–09:15, 16:50–19:00쯤 모델을 쓰고
  그동안 새 Claude 회사 호출은 기다린다. 이대로 옮기면 뉴스가 장 시작 전후로 2시간씩 멈춘다.
- Codex 실행기는 이미 요청 ID로 업무를 나눠 따로 잠근다: `news-brief-` → brief, `quant-feed-` → quant, 그 밖의 `news-` → news.
- 2026-11-17 비교(company-editorial-model-eval)의 960초 작성 제한은 평가 도구에만 있던 값이다. 운영 브리핑의 작성 단계는
  `phase_deadline`으로 회차 마감까지 약 67–72분을 쓴다.

## 결정

- **Claude는 Opus 5.5와 Sonnet 5.5다.** 요청에 고정된 모델로 공급자를 고르고, 장애가 나도 다른 공급자로 다시 실행하지 않는다.
- **브리핑·뉴스 데스크·Quant Scout는 자기 실행 자리를 쓴다.** Codex와 같은 접두사·잠금 이름(`.runtime-brief.lock`,
  `.runtime-news.lock`, `.runtime-quant.lock`)을 쓴다. 이 셋은 영상 우선 대기를 거치지 않고, 영상도 이 셋을 기다리지 않는다.
  직원 업무는 기존 공유 자리와 영상 우선을 그대로 쓴다.
- **브리핑 단계는 회사 업무 계약이다.** `brief_*` 계약을 Claude가 받고, Codex와 같은 엄격한 출력 스키마와 회차 마감을 쓴다.
  뉴스 데스크는 600초 상한이다. 회사 업무는 API 크레딧 레인으로 가지 않는다.
- **배정은 검토된 두 단계로 바꾼다.** 각 단계는 preview digest로 한 번에 적용된다. 기존 Codex 강도는 그대로 둔다.
  - 1단계 `POST /v1/model-assignments/claude-editorial`: market_brief → Opus 5.5 xhigh, news_screening → Sonnet 5.5 low,
    quant_scout → Sonnet 5.5 high.
  - 2단계 `POST /v1/model-assignments/claude-reporter`: reporter(뉴스 편집·트렌드)와 news_search → Opus 5.5 medium.
    1단계 뒤 구독 사용률을 보고 적용한다.
  - 뉴스 편집이 Sonnet이 아닌 이유: 제한 시험에서 Sonnet medium의 뉴스 검토 2건과 트렌드 1건이 도메인 계약을 어겼고(기사가
    막힌다), Opus medium은 같은 종류의 입력 6건이 모두 통과했다. 선별과 Quant Scout의 Sonnet 출력은 4건 모두 통과했다.
  - Quant Scout는 직원 배정에서 빠진다. 세 작업의 대상이 겹치지 않아 어느 순서로 적용해도 서로를 덮지 않는다.
- **구독 사용률을 보인다.** CLI가 알려 주는 5시간·주간 사용률을 상태 파일로 남겨 `상태`와 경보(80%·95%·한도 거절)에 쓴다.
- **claude-runtime 자원**: CLI가 최대 넷 동시에 돌므로 1536m·CPU 1.0. CLI 하나의 최고 사용량은 266MB였다(2026-11-17).
- 영상·쇼츠·뉴스레터·음성·그림·썸네일 설정은 바꾸지 않는다.

## 결과

- Codex에 남는 새 호출은 영상 그림 생성(주 약 22건, 영상 설정 유지)뿐이다. 이미 저장된 요청은 원래 모델로 끝난다.
- 영상과 편집 업무가 같은 구독 한도를 나눈다. 주간 한도에 걸리면 영상도 멈춘다. 공개 단가 환산으로 추가 부담은 1단계 주
  약 $110–150, 2단계 주 약 $190이다(현재 영상 중심 주 약 $200). 대체 실행은 없으므로 사용률을 보며 단계를 밟고,
  ChatGPT 플랜은 Claude 단독 운영을 1–2주 본 뒤 낮춘다. 낮춘 뒤에는 배정 복원으로 뉴스 물량을 Codex가 받기 어렵다.
- 배정 변경은 브리핑 정책 digest를 바꾸므로 회차가 진행 중인 시간(06:00–08:50, 15:45–18:20 KST)을 피해 적용한다.
- 절차: [docs/runbooks/claude-editorial.md](../runbooks/claude-editorial.md)
