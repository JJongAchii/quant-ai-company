# Quant Scout 게시 재개 및 실제 전달 확인

2026-09-30. 게시를 재개했고, 강화된 심사를 통과한 첫 실제 Slack 전달을 확인했다.
과거 게시물은 유지했고, 미게시 preview와 보류 문서를 재전송하거나 초기화하지 않았다.

## 확인된 결과

- 17:32:46 KST: 설치된 품질 v20 / `5defb8c`를 그대로 사용해 게시 설정만 활성화.
- 19:59:56 KST: 정상 Temporal 흐름에서 원문 심사 → 별도 검수 → 수정 → 재검수 후 전달.
- [실제 게시물](https://achiisquantresearch.slack.com/archives/C0C3K8ZB9PB/p1790765996984039):
  *From Factor Models to Deep Learning: Machine Learning in Reshaping Empirical Asset Pricing*.
  원발표 2024-03-11인 리뷰다. 최신 논문 또는 신규 전략 성과라고 주장하지 않는다.
- Quant Scout 신원, 전송 영수증, Slack permalink가 확인됨. 발송 시도 1회.
- 카드 2,088자 / 32행. 핵심·왜 읽나·연구 설계와 결과·주의점·적용 전 항목을 구분.
- 기존 Robeco 게시물과 미게시 preview 11건 유지. 관련 없는 일반 AI·시장 코멘터리는 탈락.

## 운영 및 검증

기존 최초 활성화 도구는 게시물 0건을 전제한다. 이번에는 historic delivered 1건을
허용하는 별도 `resume` 경로를 구현·시험했다. 현재 품질 검증 기록과 봇 신원,
진행 중 호출·미확정 전송이 없는 상태를 확인한 뒤 API·dispatch·Quant worker의 게시
설정만 적용했다. 같은 이미지와 품질 정책을 유지했고, 새 이미지 빌드·증설은 하지 않았다.

재개 도구 테스트 48개, 실제 PostgreSQL·Temporal 회귀 6개가 통과했다. 회귀 테스트의
Slack은 모의다. 반면 `LIVE-DELIVERY.json`은 실제 운영 DB 및 Slack API의 결과다.
[재개 도구 CI 36690096059](https://github.com/JJongAchii/quant-ai-company/actions/runs/36690096059)는
`558e855`에서 성공했고, 설치된 `5defb8c`의 CI 36679570477 재실행 2회차도 성공했다.

21:03:20 KST 호스트 점검에서 Quant 서비스 정상·재시작 0·OOM 없음,
가용 메모리 약 1.85 GiB·디스크 약 8.11 GiB를 확인했다.
뉴스·주거·회사 worker와 모델 런타임의 ID·이미지는 유지됐다.
maintenance는 같은 ID·이미지를 유지했지만 이후 재시작 수가 1→3으로 변했다.
이번 작업이 maintenance를 재시작한 것은 아니며, 재시작 원인을 추정하지 않는다.

사용자가 제외한 48시간 관찰 타이머는 inactive/disabled다. 기다림·타이머가 승인
조건은 아니며, 48시간 무중단 관찰을 완료했다는 주장도 하지 않는다.

## 증거와 해석 범위

- `RESULT.json`: 이번 재개의 종합 결과. 이전 `RESULT-v20.json`은 역사적 기록으로 보존.
- `LIVE-DELIVERY.json`: 현재 정책의 4단계 영수증과 실제 Slack 전달.
- `HOST-READBACK.json`: 재개 영수증 해시, 서비스 및 자원 점검.
- `verify_delivery.py`, `verify_host.py`: 실제 실행한 읽기 전용 점검. 모델 재호출·게시·DB 수정 없음.

봇 권한은 `chat:write`뿐이어서 Slack 채널 history를 별도로 읽지는 않았다.
실제 전송 성공 영수증과 Slack의 permalink 반환으로 전달을 확인했다.
별도 AI 검수는 독립 재현·투자 수익성 검증이 아니며, 한 건의 전달 성공으로
전체 피드의 정확도나 완전한 연구 커버리지를 주장하지 않는다.
글이 길거나 근거 범위를 충족하지 못하면 여전히 보류한다. 분량·게시 수를
맞추기 위해 품질 기준을 낮추거나 보류된 호출을 다시 실행하지 않았다.
