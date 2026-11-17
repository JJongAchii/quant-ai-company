# 뉴스·트렌드·브리핑·Quant Scout의 Claude 전환 준비

소유자는 Codex 구독을 낮추려 하고(2026-11-17), Codex 사용 비중을 최소화하도록 요청했다. 이어서 "트렌드나 quant scout 등
너무 좋은 모델로 안해도되. 브리핑이나 영상 등이 지금은 가장 중요"라고 정했다. 직원 전환([PR #241](https://github.com/JJongAchii/quant-ai-company/pull/241))
위에 쌓아, 남은 Codex 업무 대부분을 Claude 구독으로 옮길 수 있게 했다. 영상 설정은 그대로다.

상태: 구현·로컬 검증·제한 실제 시험 완료, PR 검토 대기. main 병합·정식 릴리스·runtime.env 값·배정 적용은 아직 하지 않았다.
이 문서를 운영 전환 완료 증거로 쓰지 않는다. 절차: [runbook](../runbooks/claude-editorial.md), 결정: [ADR 0051](../adr/0051-editorial-desk-on-claude-slots.md).

## 무엇이 바뀌나

| 단계 | 업무 | 지금(Codex) | 전환 뒤(Claude 구독) |
| --- | --- | --- | --- |
| 1 | 아침·마감 브리핑 | gpt-6-astra xhigh | Opus 5.5 xhigh |
| 1 | 뉴스 선별 | gpt-6.1-sol low | Sonnet 5.5 low |
| 1 | Quant Scout | gpt-6.1-sol high | Sonnet 5.5 high (직원 배정에서 옮김) |
| 2 | 뉴스 편집·트렌드(`reporter`) | gpt-6-astra medium | Opus 5.5 medium |
| 2 | 뉴스 검색(2026-09-21부터 멈춰 있음) | gpt-6.1-sol low | Opus 5.5 medium |
| - | 영상·쇼츠·메일·그림 | 그대로 | 그대로 |

2단계는 1단계 뒤 구독 사용률을 보고 적용한다. 뉴스 편집은 처음에 Sonnet으로 계획했지만, 제한 시험에서 Sonnet의 뉴스 검토
2건과 트렌드 1건이 도메인 계약을 어겨(운영에서는 그 기사가 막힘) Opus로 바꿨다.

- 실행 자리: Claude 실행기는 CLI를 하나만 돌렸다. 이제 브리핑·뉴스 데스크·Quant Scout가 Codex와 같은 자기 자리를 쓴다.
  영상이 모델을 쓰는 07:10–09:15, 16:50–19:00에도 뉴스가 멈추지 않고, 영상도 이들을 기다리지 않는다.
- 브리핑: Codex와 같은 엄격한 출력 스키마, 회차 마감(작성 단계 약 67–72분), 검증기 오탐 9건 수정(회귀 검사 포함),
  일정 범위·명시된 시간대·fact_placements 지침. 실제 근거 부족은 계속 거절한다.
- 뉴스: 분석 기사라도 확인 가능한 사실(공식 통계·공시·확정된 정책·시장 자료)은 게시하도록 편집 지침을 보완했다.
- 구독 사용률: CLI가 알려 주는 5시간·주간 사용률을 `상태`에 보여 주고 80%·95%·한도 거절 때 #ops-alerts로 알린다.
- claude-runtime: 메모리 1536m·CPU 1.0(운영 runtime.env의 `CLAUDE_MEMORY_LIMIT=512m`은 운영자가 바꿔야 한다).

## Codex에 남는 것

두 단계 뒤 새 호출은 영상 그림 생성(주 약 22건)뿐이다. 최근 7일 약 1,870건 기준 1단계에서 약 55%, 2단계 뒤 약 99%가 줄어든다. 이미 저장된 요청은 원래 모델로 끝난다.
Plus에서 그림 생성 모델·한도를 쓸 수 있는지는 확인하지 못했다. 예비 Pro $100 계정은 최근 30일 호출이 0건이다.

## 검증

- 실제 PostgreSQL·subprocess·ASGI HTTP와 합성 CLI 응답: 뉴스 선별→편집, 브리핑 계획→사실 정리→작성→검토의 producer-consumer,
  자리 분리, 영상 우선 예외, 마감, 공급자 검사, 배정 preview·적용, 구독 사용률 상태·경보(`tests/test_claude_editorial.py`, `tests/test_ops.py`).
- 제한 실제 시험(운영 CLI 2.1.280, 각 한 번): [1차](evidence/claude-editorial-20261117/trial.json) 5건,
  [2차](evidence/claude-editorial-20261117/trial-round2.json) 6건. 요약은 아래 표.

| 입력 | 모델·강도 | 시간 | 결과 |
| --- | --- | --- | --- |
| 아침 브리핑 작성(87,990자) | Opus xhigh | 939초 | 필수 사실 42/42 배치, 시간 추정 0건, 항목 6건 근거 부족으로 제외(파이프라인이 검토·수정) |
| 저녁 브리핑 작성(85,692자) | Opus xhigh | 1,846초 | 필수 사실 52/52 배치(지난 비교: Astra 0, Opus 960초 초과), 시간 추정 0건, 항목 4건 제외 |
| 뉴스 검토 2건 | Sonnet medium | 21·41초 | 둘 다 계약 위반 |
| 트렌드 후보 묶음 1건 | Sonnet medium | 57초 | 계약 위반(근거 없는 배경 19건) |
| 뉴스 검토 2건 | Opus medium + 새 지침 | 20·74초 | 둘 다 통과, 게시 0/4·4/7(지난 비교 Opus 0/4·1/7, Astra 2/4·3/7) |
| 운영 선별 2건 | Sonnet low | 20·16초 | 둘 다 통과, 10/12·7/9 남김 |
| 운영 Quant 검토 2건 | Sonnet high | 5·7초 | 둘 다 통과, 기각·기각(운영 Codex는 기각·보류) |

  저녁 브리핑 작성 1,846초는 운영 작성 단계의 여유(약 67분) 안이다. 다만 다른 단계도 Codex보다 느리면 회차 커밋이
  지금(아침 07:04–07:13, 마감 16:46–16:57)보다 20–40분 늦어지고, 뒤따르는 영상 시작도 그만큼 늦어질 수 있다(추정).
  시간이 문제가 되면 소유자가 `모델 지정 브리핑 claude-opus-5-5 high`로 강도를 낮출 수 있다(시험하지 않음).
  시험에서 새로 찾은 오탐(5년째·6주 연속)도 고쳤다.
- 전체 비-live 회귀(`tests`+`deploy`, 실제 PostgreSQL 14): 3,593건 중 3,446 통과·147 건너뜀·실패 0, Ruff 통과
  ([validation.json](evidence/claude-editorial-20261117/validation.json)).

장기 구독 용량, Plus의 대기 시간, 운영 배포·배정은 검증하지 않았다.
