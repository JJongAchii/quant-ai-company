# 자료 기반 연구 프로그램 운영 준비

프로그램 승인은 자료에서 새 과제를 만들 권한과 누적 예산을 함께 승인한다. 기존
과제의 승인·진행 상태를 이 기능으로 옮기거나 다시 실행하지 않는다.

## 직원 도구

총괄의 `research_control` 도구에 다음 동작을 추가했다.

- `program_catalog`: 실제 등록 프로필과 `ResearchProgram` JSON schema를 조회한다.
- `program_draft` + `spec`: 전체 명세를 고정하고 기존 Slack 승인 버튼을 게시한다.
- `program_status`: 과제, 검증 상태, 누적 실험과 계산시간 예약/정산을 조회한다.

모델은 승인 동작을 호출할 수 없다. 승인 버튼은 사용자·채널·스레드·버전·digest에
묶인다. 취소는 자식 과제의 새 실행을 멈추고 진행 중 작업의 취소/대사를 요청한다.

`envelopes`마다 시장, 고정 `MissionSpec`, 유한한 `search.max_total_trials`가 필요하다.
프로그램에는 `max_total_trials`, `max_compute_seconds`, `max_missions`,
`max_parallel_missions`, 검토할 `source_ids`가 필수다. `include_quant_feed`는 동일
소유자의 독립 검토를 통과한 원문을 추가한다. 기존 유효한 결과와 부정적 결과도 후속
과제의 출처로 제공한다. 예산이 부족하면 진행 중 결과의 감사·보고를 끝내고 대기한다.

## 국내 ETF·주식 프로필 준비

기존 [연구 배포 절차](autonomous-research.md)의 백업·release registry·롤백을 따른다.
새 `kr-etf-research-v2`, `kr-stock-research-v2` 프로필은 기존 프로필 옆에 등록한다.
두 프로필은 같은 전송 규약을 사용하고 각자의 입력 SHA와 런타임을 고정한다.

`python -m quant_company.research.domestic_profile --help`가 준비 명령이다. 실제
신뢰한 quant-lab Git bundle/SHA/commit, 검증된 3070 runtime JSON, 시장별 입력 디렉터리,
자격/평가 시간 제한과 **새 출력 경로**를 전달한다. 명령은 고정 evaluator·통계 consumer와
수정 가능한 `candidate.py`/`config.json`을 격리 Git checkout에 기록하고,
`server-profile.json`, `worker-profile.json`, `identity.json`을 생성한다.
서버의 bundle 경로는 배포 컨테이너에서 읽을 경로로 재검증해 배치한다.
이 명령은 워커 활성화·과학 실험·승인을 실행하지 않는다.

입력은 `warmup.json`, `development.json` 배열이다. 필수 열은 `date`, `ticker`,
`market` (`kr_stock`/`kr_etf`), `open`, `close`, `adj_close`, `value`, `available_at`,
`tradable`이다. qdata API로 확보한 당시 일별 유니버스를 사용하고, 최신 종목 목록을
과거로 적용하지 않는다. `available_at`과 `tradable`은 검증된 자료에서 만들어야 한다.
날짜 누락·거래정지·상장폐지 정산·현금 배당을 원래 자료가 증명하지 않으면 원문 재현이나
총수익 측정을 주장할 수 없다. 이 adapter는 해당 가격 결손/거래 불가 경로를 거절한다.

가설 코드 API는 `weights(history, config) -> {ticker: weight}` 또는
`predict(history, config) -> {ticker: score}`다. `history`에는 이전 세션까지 공개된
행만 들어간다. 모델 학습은 이 history와 승인된 런타임 안에서 수행하며 훈련/검증 구간,
라벨 만기, 정규화·모델 선택은 독립 감사 대상이다. 데이터 파일 자체를 직접 여는 후보
코드는 거부해야 한다. 이 callback 경계만으로 후보의 무누수를 증명하지 않는다.

과학 평가는 현재 예측 점수와 다음 시가 간 가격수익의 횡단면 상관을 지원한다.
평가 명세의 `replication`은 원문의 **동일한 추정량·시장·데이터·기간·방법**을 증명해야
한다. 다른 추정량의 논문을 이 evaluator에 넣어 원문 재현이라고 부르지 않는다.

## 활성화와 인수

### 승인된 데이터 증거 패킷

운영자는 `/state/research/provisioned/data-evidence/registry.json`에 아래 형식의
패킷 목록을 둔다. `input_files`에는 이미 승인된 `warmup.json`·`development.json`
같은 파일만 넣고, 경로는 같은 `data-evidence` 디렉터리 안의 절대 경로를 쓴다.
`engine`은 승인된 프로필의 보호된 실행 엔트리포인트 원본이다. `reports`는
출처·수집 시점·유니버스·결손·실행 조건의 검증 영수증과 미확인 사항을 담는다.

```json
{
  "schema_version": 1,
  "packets": [{
    "schema_version": 1,
    "program_digest": "<approved program SHA-256>",
    "envelope": "etf_strategy",
    "execution_profile_digest": "<approved profile SHA-256>",
    "lake_id": "<approved lake ID>",
    "input_files": {"warmup.json": {"path": "/state/research/provisioned/data-evidence/warmup.json", "sha256": "<approved input SHA-256>"}},
    "engine": {"path": "/state/research/provisioned/data-evidence/engine.py", "sha256": "<approved entrypoint SHA-256>"},
    "reports": {"limitations.json": {"path": "/state/research/provisioned/data-evidence/limitations.json", "sha256": "<report SHA-256>"}},
    "blocking_gaps": ["Original publication time remains unverified"]
  }]
}
```

서비스는 현재 승인 명세와 패킷의 프로그램·프로필·레이크·입력·엔진 해시를
대조하고, 모든 파일 바이트를 다시 해시한 뒤 관리 저장소의 불변 파일로 복사한다.
데이터 직원은 패킷 식별자, 실행 코드, 보고서 원문을 이번 시도에서 끝까지 읽어야
심사를 제출할 수 있다. 실제 읽기 청크는 `research_stage_reads`에 남는다.
입력 전체도 직원에게 읽기 전용으로 제공되지만 완독을 자동 주장하지 않는다.
패킷의 존재나 파일 해시 일치만으로 `ready`가 되지 않는다. 실제 프로필에서
패킷이 없거나 `blocking_gaps`가 남아 있으면 서비스도 `ready`를 거부한다.
원천 공개 시점과 체결 가능성이 미확인이라면 `blocked`가 맞다. 이미 `waiting`인 과제는 자동
승격하지 않으며, 새 증거 버전은 새로운 과제 제안을 시작할 수 있다.

현재 ETF 패킷의 미확인 사항은
[ETF 데이터 증거 노트](../project/evidence/research-programs-20260929/etf-data-evidence-note.json)에
기록했다. 종가 체결을 쓰는 제안은 승인된 시가 체결 엔진과 별도로 검토한다.

1. 실제 서버/3070 commit·등록 프로필·진행 중 job을 관측하고 동시 변경과 통합한다.
2. 일회용 DB의 migration 반복 실행, 전체 `uv run pytest`, `uv run ruff check .`,
   실제 3070의 새 프로필 격리 자격검사를 통과시킨다. 각 결과의 정확한 commit을 기록한다.
3. 배포본·이전 release·설정 백업·새 입력/프로필 식별자와 첫 프로그램의 완전한 명세를
   검토 자료로 제시한다. 새로운 비용·데이터 권한·실험 예산을 암묵적으로 추가하지 않는다.
4. 검토 후 승인된 배포만 활성화하고, 소유자의 프로그램 승인을 수신한다.
   선택적으로 `RESEARCH_LIBRARY_CHANNEL_ID`를 허용된 research-library 채널로 설정한다.
   설정하지 않으면 보고서는 기존 연구 스레드와 PostgreSQL 출처에 남는다.
5. 실제 ETF와 주식 각각 두 개 이상의 다른 실험, 이전 반론에 따른 후속 선택,
   원문 재현/가설검증/전략개선, 독립 감사·해석, 보고서 원본 대조와 Slack 수신을 확인한다.
6. 롤백 시 새 프로그램을 멈추고 구 버전으로 서버/워커 release를 복원한다. 추가 테이블과
   승인·결과·예약을 삭제하지 않는다. 불명확한 실행/발송은 영수증으로 대사한다.

실제 연구에서 유망한 후보가 없어도 완료할 수 있다. 합성 fixture 검사와 실제 모델·자료·
Slack 검사를 구분하고, 아직 실행하지 않은 인수 항목은 완료로 표시하지 않는다.
