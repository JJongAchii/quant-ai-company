# housing-feed — 서울·경기 주택분양 공고

공식 공개 페이지를 읽어 서울·경기 아파트 분양과 접수 일정을 Slack으로 전달한다.
API 키·유료 API·모델 호출은 사용하지 않는다. 실제 청약 접수와 자격 확인은 공고에 지정된 공식 사이트에서 진행한다.

## 수집 범위

- 청약홈: 아파트 분양주택, 무순위·불법행위 재공급·임의공급을 포함하는 APT 잔여세대 목록.
  서울·경기 필터로 최근 1년 공고를 조회하고 접수 종료일이 지나지 않은 공고의 상세 일정을 읽는다.
- LH: 공공분양·신혼희망타운 등 분양주택의 공고중 목록. 서울·경기 지역만 선택한다.
  목록의 마감일은 게시판 마감일이며, 청약 마감으로 사용하지 않는다. 공급일정 표의 신청일시를 따로 읽는다.
- SH: 주택분양 게시판 최근 3페이지의 모집·공급 공고. 최근 14일의 해당 공고를 전달한다.
  첨부 문서에만 있는 세부 일정은 추측하지 않는다. 이 소스의 자동 일정 알림은 지원하지 않는다.

청약홈 가격은 **주택형별 최고가·만원**, LH 가격은 제공되는 경우 **주택형별 평균가·원**으로 표시한다.
개인 소득·무주택·청약통장 조건을 판정하지 않는다. GH 별도 게시판, 임대주택, 오피스텔,
아직 모집공고가 나오지 않은 분양 예정 정보까지 모두 포함하는 서비스는 아니다.
공식 페이지 구조 변경이나 접근 실패는 수집 오류로 기록하며, 공고 0건으로 처리하지 않는다.

## 알림 정책

- 각 소스는 성공한 수집 후 1시간 뒤 다시 조회한다. 오류 시 30분 뒤 재확인한다.
- 신규·변경 공고를 08:00~21:00 KST에 개별 발송한다. 채널 전체 발송 시도 간격은 최소 1분이다.
- 첫 연결 때 현재 접수 예정·진행 중인 공고도 발송한다. 종료된 과거 공고는 발송하지 않는다.
- 전날과 당일 알림은 확인된 접수 시작일·종료일에 대해 09:00부터 처리한다.
  Temporal이 5분 대기 간격으로 확인하므로 수집·장애에 따라 늦어질 수 있다.
  15시까지 복구하지 못한 해당 날짜 알림은 버린다. 09시 이후 처음 수집한 공고는
  신규 공고 메시지로 안내하고 같은 날 중복 일정 알림을 추가하지 않는다.
- 공고 내용이 바뀌면 변경 공고를 만들며, 이전 버전의 미발송 메시지는 발송 직전에 차단한다.
  이미 게시한 내용은 기록으로 남는다. 철회 여부를 추측하는 알림은 발송하지 않는다.
- 소스 오류 또는 3시간 이상 미확인 공고는 재확인까지 발송을 보류한다.
- Slack 429는 같은 ID로 재시도한다. 타임아웃·5xx·잘못된 성공 영수증·전송 중 종료는
  `uncertain`으로 보존하며 운영자가 대조하기 전 자동 재발송하지 않는다.

## 연결 및 운영

기존 Reporter 앱을 새 `#housing-feed`에 추가한다. 별도 AI 직원이나 Slack 앱을 만들 필요는 없다.
워크스페이스·허용 채널·소유자 설정을 기존 배포와 일치시킨다.
주택 피드의 발송 허용 목록은 `HOUSING_FEED_ALLOWED_CHANNELS`로 별도 지정한다.
기존 피드의 정책 digest가 바뀌지 않도록 공용 `SLACK_ALLOWED_CHANNELS`는 수정하지 않는다.
이 채널은 분양 알림 수신용이며 대화형 직원의 새 지시 접수 채널로 등록하지 않는다.

```dotenv
HOUSING_FEED_ENABLED=true
HOUSING_FEED_PUBLISH_ENABLED=false
HOUSING_FEED_CHANNEL_ID=C_ACTUAL_HOUSING_CHANNEL
HOUSING_FEED_OWNER_USER=U_ACTUAL_OWNER
HOUSING_FEED_ALLOWED_CHANNELS=["C_ACTUAL_HOUSING_CHANNEL"]
```

```bash
uv run --frozen quant-company housing-feed probe --output .local/housing-probe.json
uv run --frozen quant-company migrate
uv run --frozen quant-company housing-feed collect
uv run --frozen quant-company housing-feed status
```

`probe`는 공개 사이트만 읽으며 DB·Slack에 접속하지 않는다. `collect`는 DB에 실제 조회와
공고를 보존하며 publish 설정이 켜진 경우 발송을 준비한다. `status`로 소스별 성공·실패와
발송 영수증을 확인한다. 준비한 메시지를 보내려면 `dispatch` 프로세스가 필요하다.

배포 시 **같은 버전의 dispatcher와 housing-feed-worker**를 사용한다. 기존 버전 dispatcher는
새 메시지 종류의 검증을 지원하지 않으므로 수집·발송 활성화보다 먼저 교체해야 한다.
Compose의 `housing-feed` profile을 켜면 전용 worker가 실행된다. 기존 dispatcher가
`company-housing-feed-v1` Temporal workflow를 시작하고, 전용 `-housing-feed` queue에서 실행한다.
수집 worker에는 DB·Temporal 자격증명만 전달하며 Slack·Codex 자격증명은 전달하지 않는다.

공개 페이지와 채널 접근을 확인한 후 `HOUSING_FEED_PUBLISH_ENABLED=true`로 켠다.
중지할 때는 두 설정을 false로 바꿔 worker와 dispatcher에 함께 반영한다. 영수증은 보존한다.

## 검증

```bash
TEST_DATABASE_URL=postgresql://localhost:55483/postgres uv run --frozen pytest -q tests/test_housing_feed.py tests/test_housing_feed_temporal.py
uv run --frozen ruff check .
```

테스트 DB는 임시 DB를 생성·삭제할 수 있는 전용 PostgreSQL이어야 한다.
자동 검사의 HTTP와 Slack 응답은 fixture이며 실제 Slack 게시와 구분한다.
실제 공개 소스·운영 연결 결과는 `docs/project/evidence`에 기록한다.

### 제한된 운영 배포

`deploy/housing_feed_release.py`는 승인된 operator 경로다. 실행 중인 release commit을 `--base`,
리뷰한 회사 commit을 `--commit`으로 지정하고 실제 channel·owner를 전달한다.
`stage`에 `--archive`와 로컬에서 확인한 `--sha256`을 추가한다. 기존 DB와 연구 worker를 보존하고
새 app image를 만든 뒤 housing 테이블만 추가하고 발송 비활성 상태에서 공개 소스 네 곳을 수집한다.
`activate`는 stage 영수증·기존 runtime.env digest·workspace·Reporter 채널 접근을 다시 확인한다.
기존 dispatcher와 새 housing collector만 시작하며 다른 서비스의 컨테이너 ID·실행 상태를 검사한다.
두 단계 모두 공용 `.backup.lock`을 사용하므로 다른 배포·백업이 진행 중이면 종료한다.
기존 영수증이 있거나 base/config가 달라졌을 때 자동으로 덮어쓰거나 재활성화하지 않는다.

영수증은 서버 `/var/lib/quant-company/releases/housing-feed-<commit>.json`에 남는다.
실패 시 새 프로세스를 멈추고 housing pending은 stale, sending은 uncertain으로 보존한 뒤
이전 runtime.env와 dispatcher를 복구한다. 이미 게시된 메시지는 되돌리지 않는다.
최종 실제 Slack readback과 Temporal 상태는 별도 운영 증거로 보존한다.
