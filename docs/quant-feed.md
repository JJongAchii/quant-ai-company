# Quant Scout 운영

기본은 수집·발송 모두 꺼짐. `QUANT_FEED_ENABLED`, `QUANT_FEED_PUBLISH_ENABLED`,
`QUANT_FEED_CHANNEL_ID`, `QUANT_FEED_OWNER_USER` 및 기존 Slack 허용 목록을 설정한다.
채널은 뉴스·테크 채널과 달라야 한다. `QUANT_FEED_SOURCES_FILE`은 선택적인 소스 파일이며
사용 시 해당 worker/dispatch에 동일 파일을 읽기 전용으로 마운트해야 한다.

```sh
uv run --frozen quant-company quant-feed probe
uv run --frozen quant-company quant-feed status
# 별도 검증 DB + 허용된 채널/소유자, publish=false 상태에서만:
uv run --frozen quant-company quant-feed preview --live
```

`probe`는 실제 공개 소스만 읽으며 모델·Slack·DB를 사용하지 않는다. `preview --live`는
실제 구독 호출 한 단계를 실행하고 결과를 DB에 저장하지만 발송하지 않는다. 심사→근거 검사까지
여러 번 실행할 수 있다. 운영 worker와 같은 DB에서 preview를 병행하면 동일 영속 큐를 사용한다.
발송이 켜져 있으면 preview는 거절한다. preview 결과는 활성화 후 자동 재게시하지 않는다.

컨테이너는 `--profile quant-feed`로 명시적으로 추가한다. 기존 2 GiB 정적 예산에는 포함하지 않는다.
마이그레이션 후 `quant-company quant-feed-worker`를 실행한다. dispatch는 두 영속 workflow를
시작한다: `company-quant-feed-collection-v1`, `company-quant-feed-editorial-v1`.
큐는 기존 이름에 `-quant-collection`, `-quant-model`을 붙인다. 정상 idle은 장애가 아니다.

`status`의 source `last_success/error`, 문서 state, blocked call, outbox `uncertain`을 구분한다.
새 자료 없음과 HTTP/파싱/원문 접근 오류는 별개다. `held`는 품질·접근·불명 결과 보류이며,
`preview`는 발송 없이 두 단계 통과, `queued`는 아직 Slack 완료가 아니다.
발송 완료는 outbox `delivered` 및 `sent_ts`로만 확인한다.

자료 하나가 막혀도 다른 자료는 진행한다. 불명 모델/Slack 호출은 원래 ID·영수증을 조사하며
새 ID로 자동 재시도하지 않는다. 근거 없는 보류 해제 SQL이나 과거 피드 대량 재게시를 하지 않는다.
소스/정책 변경은 대기 중 발송을 stale 처리하므로 변경 전 상태를 확인한다.

운영 활성화 전: 구독 인증·실제 원문 브리프·별도 근거 검사·전용 봇·Linux PDF 제한·3개 슬롯 부하를
확인한다. 가용 메모리 256 MiB 미만이 지속되거나 OOM/재시작이면 quant 활성화를 보류한다.
뉴스·회사 worker의 기존 실행/중지 상태는 승인 없이 바꾸지 않는다. 48시간 안정성은 실제 관찰
기간이 지난 뒤 보고하며, 즉시 테스트를 장기 관찰로 표현하지 않는다.
