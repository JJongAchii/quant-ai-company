# 뉴스·트렌드·브리핑·Quant Scout의 Claude 전환 준비

소유자는 Codex 구독을 낮추려 하고(2026-11-17), Codex 사용 비중을 최소화하도록 요청했다. 이어서 "트렌드나 quant scout 등
너무 좋은 모델로 안해도되. 브리핑이나 영상 등이 지금은 가장 중요"라고 정했다. 직원 전환([PR #241](https://github.com/JJongAchii/quant-ai-company/pull/241))
위에 쌓아, 남은 Codex 업무 대부분을 Claude 구독으로 옮길 수 있게 했다. 영상 설정은 그대로다.

상태: 구현·로컬 검증·제한 실제 시험 완료, PR 검토 대기. main 병합·정식 릴리스·runtime.env 값·배정 적용은 아직 하지 않았다.
이 문서를 운영 전환 완료 증거로 쓰지 않는다. 절차: [runbook](../runbooks/claude-editorial.md), 결정: [ADR 0051](../adr/0051-editorial-desk-on-claude-slots.md).

## 무엇이 바뀌나

| 업무 | 지금(Codex) | 전환 뒤(Claude 구독) |
| --- | --- | --- |
| 뉴스 편집·트렌드(`reporter`) | gpt-6-astra medium | Sonnet 5.5 medium |
| 뉴스 선별 | gpt-6.1-sol low | Sonnet 5.5 low |
| 뉴스 검색(2026-09-21부터 멈춰 있음) | gpt-6.1-sol low | Sonnet 5.5 low |
| Quant Scout | gpt-6.1-sol high | Sonnet 5.5 high (직원 배정에서 옮김) |
| 아침·마감 브리핑 | gpt-6-astra xhigh | Opus 5.5 xhigh |
| 영상·쇼츠·메일·그림 | 그대로 | 그대로 |

- 실행 자리: Claude 실행기는 CLI를 하나만 돌렸다. 이제 브리핑·뉴스 데스크·Quant Scout가 Codex와 같은 자기 자리를 쓴다.
  영상이 모델을 쓰는 07:10–09:15, 16:50–19:00에도 뉴스가 멈추지 않고, 영상도 이들을 기다리지 않는다.
- 브리핑: Codex와 같은 엄격한 출력 스키마, 회차 마감(작성 단계 약 67–72분), 검증기 오탐 7건 수정(회귀 검사 포함),
  일정 범위·명시된 시간대·fact_placements 지침. 실제 근거 부족은 계속 거절한다.
- 뉴스: 분석 기사라도 확인 가능한 사실(공식 통계·공시·확정된 정책·시장 자료)은 게시하도록 편집 지침을 보완했다.
- 구독 사용률: CLI가 알려 주는 5시간·주간 사용률을 `상태`에 보여 주고 80%·95%·한도 거절 때 #ops-alerts로 알린다.
- claude-runtime: 메모리 1536m·CPU 1.0(운영 runtime.env의 `CLAUDE_MEMORY_LIMIT=512m`은 운영자가 바꿔야 한다).

## Codex에 남는 것

새 호출은 영상 그림 생성(주 약 22건)뿐이다. 최근 7일 약 1,870건 기준 약 99%가 줄어든다. 이미 저장된 요청은 원래 모델로 끝난다.
Plus에서 그림 생성 모델·한도를 쓸 수 있는지는 확인하지 못했다. 예비 Pro $100 계정은 최근 30일 호출이 0건이다.

## 검증

- 실제 PostgreSQL·subprocess·ASGI HTTP와 합성 CLI 응답: 뉴스 선별→편집, 브리핑 계획→사실 정리→작성→검토의 producer-consumer,
  자리 분리, 영상 우선 예외, 마감, 공급자 검사, 배정 preview·적용, 구독 사용률 상태·경보(`tests/test_claude_editorial.py`, `tests/test_ops.py`).
- 제한 실제 시험: [trial.json](evidence/claude-editorial-20261117/trial.json). 운영 CLI 버전으로 고정 입력 5건을 한 번씩만 보냈다.
- 전체 비-live 회귀와 Ruff: [validation.json](evidence/claude-editorial-20261117/validation.json).

장기 구독 용량, Plus의 대기 시간, 운영 배포·배정은 검증하지 않았다.
