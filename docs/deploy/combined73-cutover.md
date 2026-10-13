# combined73 cutover: briefing + daily video (PREPARED, NOT APPLIED)

Source: branch `release/combined73-video`, merge commit `7b37ac48824598a0c3c89b0bfd898574456bccf7`
(= snapshot `954fc7a5753803812c749ec1f0be06e338042119` of the live combined72 images on 51ce4f9, 3-way merged with 0764931: #132 always-body + #133 daily video).

Publication stays ON (`BRIEFING_PUBLISH_ENABLED=true`). Only news-worker, dispatch and briefing-data-worker are
replaced; video-worker is added. No other service (api, slack-socket, worker, maintenance, claude-runtime,
codex runtimes, account-gateway, data-watch, housing/quant/tech feeds, postgres) is recreated.

Execute only after an explicit owner go-ahead, while no edition is running
(`active_editions=0`, `pending_brief_deliveries=0`), outside 15:50-18:25 KST and 05:30-08:00 KST.

## Variables (all shell steps as root: `sudo -i`)

```sh
M=7b37ac48824598a0c3c89b0bfd898574456bccf7
OLD=51ce4f91f141e65f909ac783e590ee0585dea3fa
STAGE=/var/lib/quant-company/releases/analyst-combined73-${M:0:12}
C72=/var/lib/quant-company/releases/analyst-combined72-51ce4f91f141
R=/opt/quant-company/releases/$OLD/deploy
ENVF=/var/lib/quant-company/config/runtime.env
# Same installed file list the running briefing services were created with (labels on quant-company-news-worker-1).
BASE="-f $R/compose.yaml -f $R/research.compose.yaml -f $R/autonomous-research.compose.yaml -f $R/model-accounts.compose.yaml -f $R/data-watch.compose.yaml"
DC="docker compose -p quant-company --project-directory $R --env-file $ENVF --profile briefing"   # briefing-data-worker is in profile briefing
```

deploy/compose.yaml is byte-identical between 51ce4f9 and 0764931 (only Dockerfile.video, video.compose.yaml and env
examples were added under deploy/), so the installed 51ce4f9 deploy files remain the base. runtime.env is NOT edited:
every changed setting lives in the candidate overlay's `environment: !override` maps (as in combined72).

## 0. Preconditions (read-only)

```sh
docker ps --format '{{.Names}} {{.Image}} {{.Status}}' | grep -E 'news-worker|dispatch|briefing-data-worker|video-worker'
free -m; uptime; df -h /
ls -ld /var/lib/quant-company/{video,media-auth,video-assets,video-models}   # all exist, owner 10001
ls -A /var/lib/quant-company/media-auth | wc -l   # Runway session must be provisioned (login-runway) before episodes can render
docker image inspect quant-company:$M-combined73-0 quant-company:$M-combined73-1 quant-company:$M-combined73-2 \
  quant-company:$M quant-company-video:$M --format '{{.RepoTags}} {{.Id}}'
$DC $BASE -f $STAGE/candidate.compose.yaml -f $STAGE/video-worker.compose.yaml --profile video --profile claude config --quiet && echo OK
```

## 1. Build (staging only; no running service touched)

```sh
mkdir -p $STAGE/source && git -C <checkout> archive $M | tar -x -C $STAGE/source   # or upload the archive
QCTX=$(grep -E '^QDATA_BUILD_CONTEXT=' $ENVF | cut -d= -f2-); QCOMMIT=$(grep -E '^QDATA_COMMIT=' $ENVF | cut -d= -f2-)
# base app image + video image (nice, one build at a time; host is a 2-vCPU burstable)
nice -n 19 docker buildx build --target app --build-context qdata=$QCTX \
  --build-arg RELEASE_COMMIT=$M --build-arg QDATA_COMMIT=$QCOMMIT -f $STAGE/source/deploy/Dockerfile \
  -t quant-company:$M $STAGE/source
nice -n 19 docker buildx build --build-arg COMPANY_APP_IMAGE=quant-company:$M \
  -f $STAGE/source/deploy/Dockerfile.video -t quant-company-video:$M $STAGE/source
# per-service briefing images: combined72 image + the merged src/quant_company tree (same overlay pattern as combined72)
for i in 0 1 2; do
  mkdir -p $STAGE/profiles/$i/src && cp -a $STAGE/source/src/quant_company $STAGE/profiles/$i/src/
  printf 'FROM quant-company:%s-combined72-%s\nCOPY --chown=10001:10001 profiles/%s/src/quant_company/ /opt/company/src/quant_company/\nENV COMPANY_CODE_COMMIT=%s\nLABEL org.opencontainers.image.revision=%s\n' \
    $OLD $i $i $M $M > $STAGE/Dockerfile.overlay-$i
  nice -n 19 docker buildx build -f $STAGE/Dockerfile.overlay-$i -t quant-company:$M-combined73-$i $STAGE
done
```

Overlay validity: the locked non-video runtime dependencies of 0764931 are identical to 51ce4f9
(`uv export --no-dev --extra lake` diff is empty) and no first-party file exists in any combined72 image that is absent
from the merged tree, so `COPY` of the full merged `src/quant_company` yields exactly the merged source in all three
images. Verify per image: `docker run --rm --network none --entrypoint sh IMG -c 'cd /opt/company/src && find quant_company -type f ! -name "*.pyc" -exec sha256sum {} +' | sort -k2` must equal the same listing of `$STAGE/source/src`.

## 2. Backups (before any change)

```sh
TS=$(date -u +%Y%m%dT%H%M%SZ); B=/var/lib/quant-company/backups/pre-combined73-$TS; mkdir -p -m 700 $B
cp -p $C72/candidate.compose.yaml $B/combined72-candidate.compose.yaml       # rollback overlay
cp -p $ENVF $B/runtime.env                                                    # unchanged, kept for audit (mode 600)
for s in news-worker dispatch briefing-data-worker; do docker inspect quant-company-$s-1 > $B/$s.inspect.json; done
docker compose -p quant-company exec -T postgres sh -c 'pg_dump -U "$POSTGRES_USER" -Fc "$POSTGRES_DB"' > $B/quant_company.dump
pg_restore -l $B/quant_company.dump | head -3 && ls -la $B
```
(Do not overwrite /var/lib/quant-company/backups/pre-video-20261010T130243Z.)

## 3. Cutover

```sh
# 3a. migrate once (video schema + #132/#133 briefing schema; idempotent CREATE ... IF NOT EXISTS)
$DC $BASE -f $STAGE/candidate.compose.yaml run --rm --no-deps briefing-data-worker quant-company migrate
# 3b. replace exactly the three briefing services
$DC $BASE -f $STAGE/candidate.compose.yaml up -d --no-deps --no-build news-worker dispatch briefing-data-worker
# 3c. start the video worker (mem 2048m, 2 CPU, VIDEO_RENDER_WORKERS=2)
$DC $BASE -f $STAGE/candidate.compose.yaml -f $STAGE/video-worker.compose.yaml --profile video --profile claude \
  up -d --no-deps --no-build video-worker
```

## 4. Readback

```sh
docker ps --format '{{.Names}} {{.Image}} {{.Status}}' | grep -E 'news-worker|dispatch|briefing-data-worker|video-worker'
for s in news-worker dispatch briefing-data-worker video-worker; do
  docker inspect quant-company-$s-1 --format "$s {{.State.Status}} oom={{.State.OOMKilled}} restarts={{.RestartCount}}"
  docker logs --since 10m quant-company-$s-1 2>&1 | grep -ciE 'traceback|nondeterminism|out of memory'
done
docker exec quant-company-dispatch-1 ls /state/video          # read-only mount present
docker exec quant-company-news-worker-1 python -c "from quant_company.config import Settings as S; s=S(); print(s.briefing_publish_enabled, s.briefing_kr_close_enabled, s.briefing_us_close_enabled, s.video_enabled, s.video_upload_enabled)"
# expect: True True True True False
```
Confirm the other services' container IDs/StartedAt are unchanged versus the pre-change `docker ps`.

## Rollback (step by step)

Code rollback is image + overlay only; the migration is additive (new video tables/columns) and is not reverted.

```sh
# R1. stop only the new video worker
$DC $BASE -f $C72/candidate.compose.yaml -f $STAGE/video-worker.compose.yaml --profile video --profile claude stop video-worker
$DC $BASE -f $C72/candidate.compose.yaml -f $STAGE/video-worker.compose.yaml --profile video --profile claude rm -f video-worker
# R2. restore the three briefing services from the combined72 overlay (old images, old env, no video mount)
#     images: quant-company:51ce4f91f141e65f909ac783e590ee0585dea3fa-combined72-0 (news-worker),
#             ...-combined72-1 (dispatch), ...-combined72-2 (briefing-data-worker)
$DC $BASE -f $C72/candidate.compose.yaml up -d --no-deps --no-build news-worker dispatch briefing-data-worker
#     (if $C72/candidate.compose.yaml were lost: use $B/combined72-candidate.compose.yaml)
# R3. readback as in step 4; expect combined72-0/1/2 images, US close false, no VIDEO_* keys.
# R4. only if the database itself is damaged (not for a code rollback): restore $B/quant_company.dump with
#     pg_restore --clean --if-exists after stopping writers - requires a separate owner decision.
```
IMPORTANT before R2: the combined72 dispatch has no `video_files`/`video_review` gate and would post such a row as a
plain chat message. Between R1 and R2, settle any pending video rows (only rows the video feature itself created):

```sh
docker compose -p quant-company exec -T postgres sh -c 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -v ON_ERROR_STOP=1 -c "
UPDATE outbox o SET status='"'"'stale'"'"', error='"'"'rolled_back_combined73'"'"' FROM messages m
 WHERE m.id=o.id AND m.kind IN ('"'"'video_files'"'"','"'"'video_review'"'"') AND o.status IN ('"'"'pending'"'"','"'"'sending'"'"') RETURNING o.id;"'
```
Video jobs interrupted by R1 can be inspected later with `quant-company video status` / `video reconcile`
after a re-cutover.

## Staging status at preparation time (2026-10-10)

- Overlay files prepared locally: `~/work/pr-bodies/combined73-candidate.compose.yaml` and
  `~/work/pr-bodies/combined73-video-worker.compose.yaml`; copy them to `$STAGE/candidate.compose.yaml` and
  `$STAGE/video-worker.compose.yaml`. Both were validated read-only against the installed 51ce4f9 deploy files and
  runtime.env (`config --quiet` via stdin): OK.
- Not yet done: server staging directory, image builds, branch push (see the release report).
- Watch before cutover: news-worker uses 161/192 MiB and postgres 230/256 MiB at idle; pg_dump and the extra video
  imports may need headroom.

## 추가 운영 항목 (2026-10-10 보완)

### 순서 요약
1. 사전 확인(0) → 빌드(1) → 백업·pg_dump(2)
2. **migrate**(3a): 브리핑 3개 서비스를 바꾸기 전에 한 번만 실행한다. 추가만 하는 변경(brief_calls.attempt 열, 유니크 키 (edition_id,phase,attempt), video_* 7개 테이블)이라 아직 돌고 있는 combined72 이미지와 호환된다. 진행 중인 판이 없을 때 실행한다.
3. 브리핑 3개 서비스 교체(3b) → 영상 작업자 시작(3c) → 확인(4)
4. Runway 로그인(아래) → 월요일 확인(아래)

### news-worker 메모리 한도 상향
- 현재 192MiB 중 161MiB(84%)를 쓰고 있다. #132(항상 본문: 원문 사실 목록 렌더)와 #133(VideoStore enqueue를 브리핑 flush에서 호출)이 news-worker 프로세스에 코드를 더한다.
- 새 오버레이는 news-worker를 **320MiB**(mem_limit 335544320, swap 640MiB)로 올렸다. 근거는 다음과 같다.
  - 현재 사용량에 약 1.5~2배 여유를 둔다.
  - 호스트 한도 합계: 기존 5,120MiB + 영상 작업자 2,048MiB + 상향분 128MiB = 7,296MiB / 7,816MiB. 실제 사용량은 약 2.1GiB다.
- 교체 뒤 확인: `docker stats --no-stream quant-company-news-worker-1`이 한도의 80% 아래인지, `docker inspect -f '{{.State.OOMKilled}}'`이 false인지.
- postgres(256MiB 중 230MiB)는 pg_dump 중에 늘 수 있다. dump는 `nice`로 돌리고, 끝난 뒤 OOM 여부를 확인한다. 한도 변경은 이번 범위가 아니다.

### 되돌리기 전 영상 메시지 무효화
위 'IMPORTANT before R2'의 SQL을 R2 전에 반드시 실행한다. 대기·전송 중인 video_files/video_review outbox 행을 stale로 바꾼다. 옛 dispatch는 이 종류를 모르므로 일반 글로 보낼 수 있다.

### Runway 로그인(한 번, 소유자 허용 클릭)
- 서버 media-auth 폴더(/var/lib/quant-company/media-auth)는 아직 비어 있다. 영상 작업자가 시작된 뒤 다음을 실행한다.
  - 노트북: `ssh -L 8766:127.0.0.1:8766 <SERVER>` (로그인하는 동안 유지)
  - 서버: `$DC $BASE -f $STAGE/candidate.compose.yaml -f $STAGE/video-worker.compose.yaml --profile video --profile claude exec video-worker quant-company video-auth runway --listen-host 0.0.0.0`
- 출력된 URL을 소유자가 노트북 브라우저에서 열어 '허용'을 누른다. 비밀번호는 운영자가 입력하지 않는다.
- 설정은 `VIDEO_RUNWAY_WORKSPACE_ID=68440252`(Runway 웹 팀 ID)다. 로그인 뒤 작업자가 잔액·작업공간을 확인한다. 다르면 runway_account로 막히고 스레드에 알림이 온다.

### 서버 임시 폴더 정리
- 소스 추출용 임시본 `/tmp/c73src.5p3e`(ubuntu 소유, 약 2GB, 세 이미지의 /opt/company와 .venv)를 지운다. 비밀값은 들어 있지 않지만 디스크를 차지한다.
  - `rm -rf /tmp/c73src.5p3e`

### 월요일 10/12 확인
- **아침판**(미국 마감 기준): 등록이 05:20 수집 / 05:40 고정 / 06:55 기한인지, 본문 발송 시각(목표 06:10~06:40), 본문 여부(대체 안내문 아님)
- **아침 영상**: 브리핑 스레드에 mp4·썸네일·srt·업로드 문안이 06:50 전에 도착하는지(공개 07:00), 단계별 소요(대본·검토·음성·렌더·전달), Runway 사용 크레딧
- **마감판**: 15:50 수집 / 16:10 고정 / 17:25 기한, 본문 발송(목표 약 16:50)
- **마감 영상**: 17:50 전 도착(공개 18:00)
- **자원**: video-worker 렌더 중 메모리·CPU 최대치, news-worker·postgres OOM 여부, 디스크 여유(10GB 이상)
- **이상 시**: 브리핑 본문 발송은 영상 실패와 무관하게 계속돼야 한다(enqueue 격리). 스레드의 '영상 없음, 본문은 발송됨' 알림을 확인한다.
