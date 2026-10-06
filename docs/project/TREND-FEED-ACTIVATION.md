# 한국 검색 트렌드 운영 활성화 완료

2026-10-06. 사용자가 최초 3일 대기 조건을 **즉시 실제 브리핑 발송·직접 확인 후 활성화**로 변경했다.
현재 자료로 만든 8개 주제를 21:31 KST에 발송하고 실제 원문·본문·링크·영수증·채널 화면을 확인했다.
정기 운영을 21:44 KST에 활성화했다. 첫 정기 발송은 **10월 7일 08:00 KST**이고 이후 매일 최대 8개다.
이후 사용자 선호에 따라 **일반 스포츠 제외**를 22:36 KST에 운영에 반영했다.
기존 실제 자료의 14개 후보 중 스포츠 10개를 제외하고 4개를 선택했다.
후보가 적으면 8개를 채우기 위해 스포츠를 넣지 않는다. [스포츠 제외 검증·운영 기록](TREND-FEED-SPORTS.md).
실제 Temporal의 다음 확정 timer도 같은 시각으로 확인했다. 3일 관측을 완료했다고 간주하지 않는다.

초기에는 운영 서버에 키를 연결하고 두 대상 프로세스를 미리보기 모드로 전환했다.
백업은 S3에 보존했으며, 기존 Slack 항목·역할·모델 요청·다른 컨테이너를 보존했다.
실제 실행에서 뉴스 워커의 128 PID 한도가 부족한 것을 발견해 256으로 조정했다.
Google 실제 수집·네이버 트렌드/뉴스 응답·Temporal workflow 3개를 확인했으며,
연결 시험 1건에 이어 이번 실제 브리핑 1건을 전달했다. 현재 `TREND_FEED_PUBLISH_ENABLED=true`다.
[즉시 검증](evidence/trend-feed-20261006/immediate-review.json),
[운영 전환](evidence/trend-feed-20261006/immediate-activation.json),
[활성 실행·실제 예약](evidence/trend-feed-20261006/immediate-active-probe.json).

## 준비한 대상

- 기존 회사 서버: AWS 서울 `quant-company-host-4gb-20260928`, 새 인스턴스 없음.
- 기존 워크스페이스: `T0C1YRDRPNF`, 소유자 `U0C250E23NW`.
- [전용 채널](https://app.slack.com/client/T0C1YRDRPNF/C0C6WTA9ECV): `C0C6WTA9ECV`.
- [전용 앱](https://api.slack.com/apps/A0C7VRH8JV6): `A0C7VRH8JV6`, `chat:write` 단일 권한.
- 실제 네이버 검색어 트렌드·뉴스 인증 성공. 키는 Git 밖의 로컬 0600 파일에 보관 중.
- 소비 프로세스: 기존 `news-worker`, `dispatch`. 다른 실행 중인 프로세스는 대상에서 제외한다.

운영 서버는 여러 기능의 개별 릴리스가 함께 실행된다. 전체 회사 이미지를 일괄 교체하지 않고
두 대상의 **실제 이미지에서 추출한 소스**에 검토된 트렌드 변경만 합친 후보를 만들었다.
기존 시장 브리핑·모델 배정·뉴스 기능을 보존하고, 부모 이미지의 의존성·환경·명령·사용자·
기존 revision label을 유지한다. 별도 trend label과 파일별 before/after SHA-256으로 변경을 식별한다.

[실제 서버 staging 영수증](evidence/trend-feed-20261006/production-stage.json),
[소스 manifest](evidence/trend-feed-20261006/production-source-manifest.json).
staging과 [실제 미리보기 전환](evidence/trend-feed-20261006/production-cutover.json)을 구분해 기록한다.

## 최초 운영 연결과 미리보기 전환 기록

1. 배포 잠금, 실행 중인 릴리스 여부, 두 대상의 이미지·컨테이너 ID·설정 digest를 다시 확인한다.
   검사 후 다른 작업이 대상을 바꿨으면 중단하고 새 기준을 검토한다.
   기존 뉴스 모델 작업·대기 outbox가 비어 있는 전환 구간을 사용한다.
2. 기존 백업 경로로 DB·설정·영수증을 보존한다. 토큰은 백업·Git·모델 입력에 포함하지 않는다.
3. 기존 Slack secret에 `trend_scout` 항목만 추가하고 기존 항목을 대조해 보존한다.
   NAVER secret은 서버의 `/var/lib/quant-company/secrets/trend-naver-credentials.json`에 설치한다.
   부모 secrets 디렉터리의 root 접근 제한과 읽기 전용 마운트를 유지하며, `news-worker`만 읽는다.
   역할 설정에는 발송 전용 `active=false` 항목만 추가하고, 허용 채널에 새 ID를 추가한다.
4. 트렌드 테이블을 기존 앱 DB 계정 권한으로 추가한다. 운영 DB에 합성 후보나 가짜 날짜를 넣지 않는다.
5. 기존 compose 파일·개별 override·모델 배정·메모리 한도를 보존한 채 두 대상만 전환한다.
   다음 설정으로 실제 수집·편집·08시 확정을 시작한다.

```dotenv
TREND_FEED_ENABLED=true
TREND_FEED_PUBLISH_ENABLED=false
TREND_FEED_CHANNEL_ID=C0C6WTA9ECV
TREND_FEED_OWNER_USER=U0C250E23NW
TREND_FEED_NAVER_ENABLED=true
```

`news-worker`에만 `/run/secrets/trend_naver_credentials`를 마운트한다.
후속 릴리스·백업·복구에서도 이 overlay를 유지하는 호스트 경로를 확인해야 한다.
Codex 실행기에는 Slack·NAVER·DB secret을 추가하지 않는다.

6. 실제 Temporal의 수집·편집·확정 workflow와 DB의 Google 수집·네이버 응답을 대조한다.
   기존 프로세스의 ID·설정·상태가 보존됐는지도 확인한다. 최초 날의 이력 부족을 표시한다.
7. 발송 범위가 승인됐으면 새 채널에 연결 확인 메시지 **1건**을 보내 실제 채널·`ts`를 기록한다.
   내용은 `Trend Scout 연결 확인 완료. 3일간 미리보기 검증 중이며 정기 브리핑은 아직 시작하지 않았습니다.`다.
   불명 결과는 재전송하지 않고 실제 Slack과 영수증을 대조한다.

## 즉시 실제 발송 검증과 활성화

최초 3일 미리보기 계획은 사용자 요청으로 즉시 발송·직접 확인 기준으로 대체했다.
원래 승인된 INTENT-v1과 0일 관측 기록을 보존하고 새 INTENT-v2에 이 변경을 기록했다.
실제 시계를 변경하거나 가짜 아침 자료를 만들지 않았다.

- 21:24 KST 실제 관측을 동결하고 같은 네이버 응답 안에서 추이를 계산했다. 수집 이력 부족을 표시했다.
- 실제 운영 Reporter의 `gpt-6-astra`/`high` 구독 호출 1건이 `codex`로 정상 완료됐다.
  당일 트렌드 모델 요청 1개, 네이버 요청 19개로 제한 안이다.
- 허용 원문 6건 중 최종 8개 주제의 배경 설명 4개를 원문과 직접 대조했다.
  원문이 없는 주제의 연관성 미확인 기사 링크를 키워드 뉴스 검색 링크로 바꿨다.
  기존 입력·모델 응답은 보존하고 발송 전 본문 수정의 before/after를 DB event에 남겼다.
- 실제 브리핑은 stable ID `012716d1-68d5-52fe-839e-047a2c9b99ef`로 PostgreSQL에 먼저 커밋했다.
  `delivered`, 시도 1회, 실제 Slack `ts` `1791289915.099149`를 확인했다.
  화면의 전체 본문과 8개 링크가 일치하고 동일 ts의 메시지가 1개임을 확인했다.
  [실제 메시지](https://achiisquantresearch.slack.com/archives/C0C6WTA9ECV/p1791289915099149).
- [동일 부모 이미지의 수정본](evidence/trend-feed-20261006/immediate-image.json)은
  `trend_feed/editor.py` 한 파일만 변경했다. 220개 파일의 재고와 부모 실행 설정을 대조했다.
  추가 PostgreSQL 회귀 28개와 Ruff를 통과했다. 두 대상만 새 이미지·발송 플래그로 전환했고,
  다른 컨테이너 ID와 기존 실행 설정·키 마운트·뉴스 PID256/발송 PID128을 보존했다.

즉시 브리핑은 `geo=KR-manual`의 별도 운영 점검이며 기존 당일 아침 슬롯은 만료 상태로 보존했다.
이 점검의 `send_at`은 보강 마감으로 사용했고 명시 승인된 즉시 발송은 `status` outbox로 전달했다.
해당 행의 음수 delivery delay는 정기 08시 발송 지연 측정값이 아니다.
미리보기를 소급 발송하지 않는다. 다음 정기 07:30 동결·08:00 확정·09:00 만료는 그대로 유지한다.

호스트 timer는 이제 첫 실제 **10월 7일** 정기 발송 영수증을 매일 08:10·08:20·08:30 KST에 관측한다.
아직 다음 날짜의 행이 없을 때도 JSON null로 처리하도록 보완했고 실제 systemd 실행 성공을 확인했다.
[첫 발송 관측기](evidence/trend-feed-20261006/immediate-first-delivery-watch.json).
첫 정기 발송 시각은 아직 지나지 않았으며 이를 발송 완료로 기록하지 않는다.

운영 전환과 PID 수리 각각의 직후에는 두 대상 외 컨테이너가 보존된 것을 대조했다.
이 관측 기간에 별도 `codex-upgrade` label의 두 모델 런타임 교체도 발생했다.
해당 두 컨테이너의 실행 설정·마운트는 동일하고 모델 런타임에 새 Slack/NAVER 키를 추가하지 않았다.
이 변화와 현재 건강 상태는 [별도 관측](evidence/trend-feed-20261006/model-runtime-peer-change.json)에 기록했다.
기존 로컬 Codex 0.154.0 자격검사를 새 모델 런타임의 실호출 검증으로 간주하지 않는다.
이후 이번 즉시 브리핑에서 실제 운영 모델 요청의 정상 완료와 원문 근거를 별도로 확인했다.

## 중단과 복구

트렌드의 수집·발송 플래그를 모두 끄고 두 대상을 기존 이미지·설정으로 복구한다.
추가 테이블과 모델·Slack 영수증은 보존해 불명 요청을 자동 재실행하지 않는다.
원래 실행 중이던 릴리스·백업 timer 상태를 복구한다. 네이버 overlay를 지원하지 않는 구버전으로
되돌릴 때는 네이버 활성화 플래그도 함께 끈다.

Slack 구형 verification token은 새 토큰 활성화 후 페이지 재로드까지 확인했다.
[교체 영수증](evidence/trend-feed-20261006/slack-token-rotation.json)은 변경 여부만 보존하며 값은 기록하지 않는다.

## 원격 코드 발행 상태

GitHub 원격 저장소는 현재 공개로 확인돼 `AGENTS.md`의 비공개 source-of-truth 설명과 다르다.
사용자가 추후 비공개 전환을 직접 하겠다며 현재 공개 상태에서 원격 업로드를 명시적으로 승인했다.
코드와 새 운영 증거를 회사 저장소의 작업 브랜치에 올리고 [PR #113](https://github.com/JJongAchii/quant-ai-company/pull/113)을 열었다.
후속 요청으로 3일 대기를 즉시 실제 발송 점검으로 바꿨고 운영 활성화를 완료했다.
PR의 현재 범위와 완료 근거를 이 기준으로 갱신하고 리뷰 가능 상태로 전환했다. 첫 정기 발송 관측 timer는 운영 중이다.
[원격 확인](evidence/trend-feed-20261006/repository-publication.json).
