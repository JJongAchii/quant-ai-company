# data-watch 운영

기존 데이터 직원의 Slack 신원을 사용한다. 새 구독·모델·서버 없이 현재 회사 worker의
전용 Temporal queue에서 목록·상태·예약을 처리한다. 기본값은 비활성이다.
[회사 채널 설계](project/SLACK-COMPANY-DESIGN.md)의 두 번째 구현이며,
[검증 기록](project/DATA-WATCH-VALIDATION.md)은 실제 운영 인수와 구별한다.

## 채널에서 보는 내용

- 전체 목록은 30분 단위로 `qdata.api.dataset_catalog()`를 읽는다. 신규·변경 객체와
  이전 조회 실패만 `inspect_dataset(sample_rows=0)`으로 확인한다. 한 번에 최대 4개를
  순차 조회하므로 초기 목록 전체의 날짜 확인에는 여러 tick이 걸린다. 대기 항목은 미검사다.
- 객체 업로드 시각, parquet 통계의 데이터 기준일, 회사의 검사 시각을 따로 보여 준다.
  종목별 거래일 누락 여부를 parquet 날짜 최댓값만으로 단정하지 않는다.
- 매일 09:00 KST 이후 첫 tick에 당일 요약을 한 번 만든다. 중단 중 누락된 날짜는 소급
  발송하지 않는다. 정상 운전 시 tick 간격은 60초 + 실제 조회 시간이다.
- 장애·변경된 오류·재검사 복구를 같은 장애 스레드에 기록한다. 같은 오류의 반복 조회는
  새 글을 만들지 않는다. 전달 영수증이 확인되기 전에는 후속 글을 만들지 않는다.
- 설정된 소유자의 채널 메시지는 데이터 직원이 책임진다. `상태`, `목록`, `status`, `list`는
  모델 없이 저장된 기록을 읽는다. 일반 질문은 기존 데이터 직원의 typed proposal 경로를 쓴다.
  `data_watch_status {}` 도구도 이 소유자와 채널 안에서만 사용할 수 있다.

### 최신성 기준

기본값은 **기준 미등록**이다. 평일을 거래일로 간주하거나 업로드 시각을 공표 시각으로
대체하지 않는다. 검토된 데이터별 날짜·갱신 마감이 준비되면 아래 형식의 JSON 배열을
`data-watch-contracts.json`으로 제공한다. 예시는 형식 설명이며 실제 거래 달력의 증거가 아니다.

```json
[
  {
    "dataset": "krx_etf",
    "date_column": "date",
    "evidence": "검토된 원문 달력과 공표 정책의 문서 경로·버전",
    "valid_until": "2026-09-23T12:00:00+09:00",
    "dates": [
      {"data_date": "2026-09-21", "expected_after": "2026-09-21T18:00:00+09:00"},
      {"data_date": "2026-09-22", "expected_after": "2026-09-22T18:00:00+09:00"}
    ]
  }
]
```

마감이 지난 가장 최근 날짜와 실제 데이터 최댓값을 대조한다. 통계가 불완전하면 미검사,
달력 적용 전이나 만료 후에는 기준 미등록이다. 휴장일은 검토한 달력의 날짜 목록에서 제외한다.
월별·비정기 데이터도 데이터 기준일과 공개 예상 시점을 명시한다. 달력 파일과 정책 digest는
전송 직전까지 대조하므로 설정이 바뀐 과거 대기 메시지는 전송하지 않는다.

## 핵심 연구 입력 상세 검사

초기 대상은 `kr-etf-p11-replay-v1`의 승인된 **고정 입력 6개**다. 현재 소유자·허용된 채널·
project revision·원래 승인·등록 recipe manifest가 모두 일치하는 연구만 연결한다.
일일 검사 ID는 연구·정확한 입력 범위·운영 정책·UTC 날짜(09:00 KST 경계)에 고정된다.
전체 레이크 최신 버전을 이 입력 대신 사용하지 않는다.

기존 pull worker에서 `data_watch_enabled`를 켜면 별도 제한된 subprocess가 검사한다.
회사 서버에는 원본 데이터를 보내지 않고 typed receipt만 제출한다. 연구 heartbeat는
계속 처리한다. 실제 실행 범위는 다음과 같다.

| 범위 | 검사 |
|---|---|
| warmup 2012–2013 / dev 2014-01-01–2026-08-31 | 등록된 prices.parquet, meta.parquet, snapshot.json만 읽음 |
| 정확한 입력 | 6개 파일의 SHA-256을 읽기 전후 대조; 경로 이탈·symlink 거절 |
| parquet 모든 행의 선택 필드 | date/ticker 필수·결측·복합키 중복·범위 밖 날짜 |
| prices 선택 필드 | adj_close/close/value 존재, 결측·비유한 값, 비양수 가격·음수 거래대금 |
| JSON | 고정 hash 및 JSON 구문; 연구 결과나 모델을 실행하지 않음 |

입력 총 파일 크기 512 MiB, 파일별 400만 행, row group 비압축 크기 64 MiB,
batch 8192행, subprocess 180초 경보/부모 190초 제한이다. 해시는 전후 두 번 읽으며
parquet 내용도 읽으므로 512 MiB는 입력 파일 크기 한도이고 누적 I/O 바이트 한도는 아니다.
중복키 인덱스는 워커의 임시 SQLite 파일에 두고 작업 종료 시 지운다. 모델·Slack·DB·AWS
자격 증명은 검사 subprocess 환경에 전달하지 않는다.

이는 명시한 필드의 전체 행 검사다. 전체 거래 달력 대비 누락, prices/meta 간 모든 키의
대응, 모든 metadata 속성의 의미, 인과성, 전략 타당성, 성과·투자 적합성을 검증한 것이 아니다.
범위를 넘는 파일이나 미설치 reader는 `미검사/접근 실패`로 남긴다. 오래된 고정 입력을
최신 레이크처럼 지연으로 판정하지 않는다. 이상 값 발견은 원본 자동 수정 허가가 아니다.

API는 원래 요청 ID·scope digest·6개 해시·lease·검사 시각·측정값과 오류 목록을 대조한다.
동일 영수증 재제출은 멱등적이며 이미 확정된 결과를 다른 결과로 덮어쓰지 않는다.
읽기 전용 검사 lease는 만료 후 새로 배정할 수 있다. 연구 실행·유료 호출의 재시도 정책과
혼동하지 않는다. 워커 전송 응답이 유실되면 저장된 동일 영수증을 다시 제출한다.

## 설정·활성화

회사 DB migration을 먼저 적용한다. 허용 목록에 전용 채널 ID와 소유자를 넣고 기존 데이터
직원을 해당 채널에 참여시킨다. 기존 데이터 앱의 signed ingress/Socket Mode 설정을 재사용한다.

```dotenv
DATA_WATCH_ENABLED=true
DATA_WATCH_PUBLISH_ENABLED=false
DATA_WATCH_CORE_ENABLED=false
DATA_WATCH_CHANNEL_ID=C_REPLACE
DATA_WATCH_OWNER_USER=U_REPLACE
```

처음에는 수집만 켜고 운영 인증 API `GET /v1/data-watch`의 검사 범위를 확인한다.
Slack 게시를 켜려면 `DATA_WATCH_PUBLISH_ENABLED=true`를 모든 회사 프로세스에 적용한다.
달력을 등록할 때는 `deploy/data-watch.compose.yaml`을 함께 사용하고 회사 설정 디렉터리의
`data-watch-contracts.json`을 모든 프로세스에 동일하게 읽기 전용 mount한다. 미등록 상태로
유지하려면 파일/overlay를 생략하거나 빈 배열 `[]`을 사용한다.

상세 검사는 기존 `deploy/research.compose.yaml`과 승인된 ETF 연구 연결이 준비된 상태에서
`DATA_WATCH_CORE_ENABLED=true`, 기존 워커 설정의 `data_watch_enabled=true`를 함께 적용한다.
회사 API/worker의 research service가 꺼져 있으면 상세 요청을 만들거나 수락하지 않는다.
기존 worker token과 loopback tunnel만 재사용하고 새 credential을 만들지 않는다.
워커의 등록된 research Python에는 pyarrow가 필요하다. 연구 코드·입력 경로는 변경하지 않는다.

Slack 게시·전송 불명확 상태·복구 글·소유자 질문·실제 3070 결과 수신을 실제 운영에서 확인한
다음에만 활성화 인수 완료로 기록한다. 현재 PR의 로컬 검사가 그 인수를 대신하지 않는다.

## 개선봇 연결과 복구

전달된 장애 스레드의 `data_watch_problem` 이벤트는 기존 improvements 관측 파이프라인에서
한 번만 진단 케이스로 연결한다. 활성 장애와 허용된 소유자만 대상으로 하며 일반 미검사나
기준 미등록은 자동 개선 작업을 만들지 않는다. 개선 기능이 비활성이면 데이터 스레드에 남는다.
회사 저장소 밖 수집기·원본 데이터 수정은 별도 승인된 작업으로 진행해야 한다.

catalog/descriptor 원문 영수증, 폐기한 오래된 descriptor, 상세 검사, 장애 상태,
Slack outbox 영수증은 PostgreSQL에 남는다. `uncertain` Slack 쓰기는 자동 재전송하지 않는다.
운영자가 실제 메시지 존재 여부를 확인하기 전에는 그 뒤의 전송도 보류한다.
기능을 끄면 새 실행과 대기 게시가 차단되며 이미 보관한 기록은 삭제하지 않는다.
