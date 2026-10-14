# 운영 상태 질의와 운영 알림

Slack에서 서버 작업의 진행 상황을 물으면 봇이 실제 운영 상태를 읽고 답하게 한다. 서버에 문제가
생기면 전용 운영 채널에 경고하고, 해소되면 같은 스레드에 알린다. 모든 경로는 **읽기 전용**이다.
봇은 컨테이너를 재시작하거나 작업을 재시도할 수 없다. 기본값은 모두 비활성이다.
[요약과 검증 기록](project/OPS-WATCH-20261014.md)

## 구성

| 부분 | 위치 | 하는 일 |
|---|---|---|
| 호스트 수집기 | [`deploy/ops_probe.py`](../deploy/ops_probe.py) + [service](../deploy/quant-company-ops-probe.service)/[timer](../deploy/quant-company-ops-probe.timer) | 1분마다 컨테이너 상태·health·재시작·OOM, 디스크·메모리·스왑, 백업·정리·analyst 모니터·release 유닛 결과, 컨테이너별 최근 2분 Traceback 개수와 예외 클래스명, 공용 환경값 불일치를 모아 `ops_host_snapshots`에 기록한다 |
| 상태 도구 | `ops_status {}` ([`ops/status.py`](../src/quant_company/ops/status.py)) | 호스트 스냅샷과 PostgreSQL에서 브리핑·영상·연구 실행·Slack 발송·모델 작업·모델 계정·열린 경고를 6KB 이하로 요약한다 |
| 감시 | [`ops/watch.py`](../src/quant_company/ops/watch.py) | dispatcher 루프가 60초마다 규칙을 평가해 `ops_incidents`를 갱신하고 운영 채널에 알린다 |
| 비상 경로 | `ops_probe.py --fallback` | PostgreSQL·dispatch가 멈췄거나 감시가 10분 넘게 기록되지 않으면 Slack에 직접 한 번 알린다 |

감시는 Temporal이나 모델에 의존하지 않도록 dispatcher 안에서 돈다. 그래서 Temporal Cloud 장애도
알릴 수 있다. dispatcher가 멈춘 경우는 호스트 수집기가 대신 알린다.

## Slack에서 묻기

- **브리핑 스레드**: 답글로 "영상 언제 나와?"처럼 물으면 Analyst가 `ops_status`로 그 회차의
  브리핑·영상 상태를 먼저 읽고 답한다. `상태`라고만 쓰면 모델 없이 회차 현황을 바로 답한다.
- **운영 채널과 다른 허용 스레드**: Director가 `ops_status`로 확인하고 답한다. 운영 채널에서
  `상태`라고 쓰면 모델 없이 서버 현황 요약을 바로 답한다. 채널 최상위 글은 Director를 멘션한다.
- 호스트 스냅샷이 3분보다 오래됐거나 없으면 컨테이너 상태를 "알 수 없음"으로 답한다.
  정상으로 추정하지 않는다.

## 경고 규칙

경고(🟠)는 연속 2회 확인돼야 알리고, 치명(🔴)은 바로 알리면서 오너를 멘션한다. 상태형 경고는
연속 2회 정상으로 확인돼야 같은 스레드에 "✅ 해소"를 남긴다. 사건형 경고(마감 놓침, 발송 결과 불명
등)는 한 번만 알리고 조회 기간이 지나도 "해소"라고 쓰지 않는다. 한 번에 새 경고는 최대 5건만 열고,
나머지는 다음 분에 연다. 경고 스레드에 답글로 물으면 Director가 현재 상태를 확인해 답한다.

| 등급 | 조건 |
|---|---|
| 🔴 | 되돌리기가 끝나지 않은 릴리스 실패 · postgres·dispatch·slack-socket·worker 중지 · 컨테이너 4개 이상 동시 중지(한 건으로 묶음) · OOM 종료 · 30분 안에 자동 재시작 3회 이상 · 디스크 여유 10GB 미만 · 발송 대상 브리핑 `missed`/`blocked` · 마감 10분이 지난 진행 중 브리핑 · 영상 `blocked`/`uncertain`/`delivery_failed`/`delivery_uncertain` · 게시 마감이 지난 진행 중 영상 |
| 🟠 | 그 밖의 컨테이너 중지·unhealthy · 30분 안에 자동 재시작 1~2회 · 2분 안에 Traceback 3건 이상 · 공용 환경값 불일치 · 디스크 여유 20GB 미만 · 메모리 여유 400MB 미만 · 스왑 1GB 초과 · 백업·정리·모니터 유닛 실패 · 호스트 수집기 5분 이상 멈춤 · Slack 발송 결과 불명·막힘 · 10분 넘게 밀린 발송 · quota·한도가 아닌 사유로 막힌 모델 작업 · 모델 작업 일시정지 · 연구 실행 failed/uncertain · 연구 워커 10분 이상 무응답 · 모델 계정 로그인 필요 · 개선봇 신호 끊김 · 자동 릴리스 보고(아래) |

### 자동 릴리스 보고

수집기는 릴리스 도구가 15분마다 쓰는 `/var/lib/quant-company/release-status.json`(ADR 0045)을 읽는다.
필요한 필드만 남기고, 사유 문구에 들어 있는 자격증명 형태의 문자열은 잘라낸다. 파일이 없으면 아무것도 알리지 않는다.

- **동작과 커밋마다 한 번만 알림** (사건형, `failed`는 시도(`started_at`)마다):
  - `failed`, `error`, `render_required`, `operator_required`, `held_for_drift`, `drift`, `refused`, `no_green_main`
  - 운영자가 설치해야 하는 호스트 파일(`operator_files`)이 바뀐 경우
- **상태형** (정상으로 돌아오면 해소를 남김):
  - `incomplete`가 1시간 넘게 이어짐
  - `releasing`이 2시간 넘게 이어지거나, 한 단계(`step`)가 60분 넘게 그대로임. 진행 중인 릴리스는 단계가 바뀔 때만
    상태 파일을 쓰고 타이머는 그동안 실행되지 않으므로, 45분 갱신 지연으로 보지 않는다.
  - CI를 통과한 main이 24시간 넘게 반영되지 않음
  - 상태 파일이 45분 넘게 갱신되지 않음
- `would_release`, `waiting_for_window`, `waiting_for_quiet`, `waiting_for_lock`, `up_to_date`는 정상 대기라서 알리지 않는다.
  운영 채널의 `상태` 답에는 현재 릴리스와 판단이 한 줄로 나온다.

release 유닛의 `exec-condition` 결과는 모델 배정 가드가 실행을 건너뛴 정상 상태라 경고하지 않는다.

## 배포 창

계획된 배포 중에는 `/var/lib/quant-company/ops/deploy-window.json`에 다음을 기록한다. 지정한 서비스의
중지·재시작은 경고 대신 "배포 중"으로만 표시된다. `until`이 지나면 다시 경고한다.

```json
{"until": "2026-10-14T03:10:00+00:00", "services": ["api", "worker"], "reason": "audit note release"}
```

`services`에 `"*"`를 넣으면 전체 서비스가 대상이다. `until`에는 시간대를 반드시 적는다.

## 설정

`/var/lib/quant-company/config/runtime.env` 한 곳에 둔다. `compose.yaml`의 공통 환경 블록이 앞의 네 값을
모든 앱 서비스에 전달하고, `OPS_FALLBACK_ENABLED`는 호스트 수집기만 읽는다.

```
OPS_STATUS_ENABLED=true          # ops_status 도구와 '상태' 즉시 응답
OPS_WATCH_ENABLED=true           # 운영 채널 경고(dispatch)와 비상 경로(호스트)
OPS_ALERT_CHANNEL_ID=C...        # 전용 채널. SLACK_ALLOWED_CHANNELS에 포함, 다른 기능 채널과 달라야 함
OPS_WATCH_OWNER_USER=U...        # SLACK_ALLOWED_USERS에 포함
OPS_FALLBACK_ENABLED=true        # 호스트 비상 경로. 오너가 명시적으로 결정할 때만 켠다
```

- 운영 채널을 만들고 Director 봇을 초대한다. 게시 신원은 Director이고, 새 Slack 앱이나 자격증명은 필요 없다.
- 반영은 `deploy/release.py`로 한다(ADR 0044). `ops_status`는 모델 출력 계약(`ToolRequest`)에 새 이름을
  추가하므로, 이 기능이 들어간 커밋으로 **codex-runtime까지** 교체돼야 켤 수 있다. codex-runtime이 자기
  이미지의 계약으로 모델 답을 검증하기 때문이다.
- 스키마는 릴리스의 `migrate`가 만든다(`CORE_SCHEMA`). 수집기는 테이블을 만들지 않는다.
- 수집기는 활성 릴리스의 `/opt/quant-company/current/deploy/ops_probe.py`를 그대로 실행한다. 따로 복사하지
  않는다. service·timer만 `/etc/systemd/system/`에 한 번 두고 timer를 활성화한다. 그 전에 같은 경로로
  `--print`를 실행해 읽기 전용 출력을 확인한다. 이 파일이 없는 릴리스에서는 유닛이 조건 미충족으로 건너뛴다.
- 비상 경로는 dispatch 말고 두 번째로 Slack에 보내는 경로다. 그래서 `OPS_WATCH_ENABLED`와 별개로
  `OPS_FALLBACK_ENABLED=true`가 있을 때만 동작한다. 켜기로 한 결정은 반영 기록에 남긴다. 꺼져 있으면
  수집기는 스냅샷만 기록한다.
- 비상 경로는 호스트 비밀 파일 `secrets/slack-credentials.json`의 Director 토큰을 메모리로만 읽고,
  `chat.postMessage` 한 번만 호출한다. 결과가 불명확한 전송은 다시 보내지 않는다. 상태는
  `/var/lib/quant-company/ops/fallback-state.json`에 남는다.
- 끝까지 확인하려면 `ops_probe.py --publish --synthetic-incident`를 두 번 실행한다. "테스트 경고"가
  열리고, 이후 일반 수집 두 번 뒤에 해소가 기록된다.

## 기록하지 않는 것

- 환경값 원문: 공용 키 값은 실행마다 새로 만든 salt로 SHA-256 digest의 앞 16자만 남긴다. 같은 실행 안에서만
  비교할 수 있다. `ops_status`와 Slack에는 서비스 묶음만 나가고 digest는 나가지 않는다.
- 로그 줄: 개수와 예외 클래스명만 남기고 원문은 남기지 않는다.
- 비밀값: `ops_status` 결과는 출처로 저장하기 전에 비밀값 패턴 검사를 통과해야 한다.
