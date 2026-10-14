# CI를 통과한 main은 스스로 운영에 반영된다

## 배경

- ADR 0044로 운영은 main 커밋 하나로 돈다(2026-10-14 13:55 KST 전환, `5454e3d`). 하지만 반영은 운영자가 `deploy/release.py`를 손으로 실행해야 했다. main이 앞서 나가도, 누가 컨테이너를 손으로 바꿔도 아무도 알 수 없었다.
- 운영 서버는 2 vCPU, 8GiB 한 대다. 브리핑과 그 영상은 정해진 시간에 돈다.
  - AM: 미국 장마감 + 20분에 수집, + 115분에 발행.
  - PM: 한국 장마감 + 20분(15:50 KST)에 수집, 17:25에 발행.
- 전체 릴리스는 이미지 빌드 약 10분과 서비스 17개 교체가 필요하다.
- 영상 렌더(Fargate)는 별도 이미지와 CloudFormation 작업 정의로 돈다. 호스트에는 CloudFormation 권한이 없고, 바꾸려면 소유자 승인이 필요하다.

## 결정

1. **15분마다 확인한다.** systemd 타이머 `quant-company-release-auto.timer`가 매시 02/17/32/47분에 `/opt/quant-company/current/deploy/release.py auto`를 실행한다. 실행하는 코드는 지금 운영 중인 릴리스의 것이다.
2. **매번 보고한다.** 결과는 `/var/lib/quant-company/release-status.json`(0644)에 쓴다. 결정이나 대상이 바뀔 때마다 `release-journal/auto.jsonl`에도 남긴다. 기록하는 내용:
   - 운영 커밋, CI를 통과한 최신 main 커밋과 그 사이 커밋 수
   - 운영이 자기 릴리스와 다른 곳(drift)
   - 렌더 이미지 커밋
   - 운영자 단계가 필요한 호스트 파일
   - 결정(`action`)과 사유
3. **대상은 CI를 통과한 main의 최신 커밋 하나다.**
   - GitHub에서는 Maintainer 앱으로 읽기 전용 토큰(contents, actions read)만 만든다. 이 토큰으로 `Quant Company CI`의 main push 성공 실행을 찾는다.
   - 그 커밋이 운영 커밋의 후손(`ahead`)일 때만 대상으로 삼는다.
   - 코드는 GitHub tarball로 받는다. pax 헤더의 커밋을 확인하고 빌드 입력(deploy, src, pyproject.toml, uv.lock, .dockerignore)만 푼다.
   - codeload 리디렉트에는 설치 토큰을 보내지 않는다.
4. **스스로 할 수 없는 변경이면 멈추고 알린다.**
   - `render_required`: 렌더 이미지 커밋(`VIDEO_RENDER_COMMIT`)과 다른 렌더 입력이 있다. 렌더 입력은 렌더 작업이 import하는 모듈(`video.remote`부터 정적으로 추적), 디자인 자산, `Dockerfile.render`, `render.json`, 의존성이다. 운영자가 렌더 이미지와 작업 정의를 먼저 바꾸고 `VIDEO_RENDER_COMMIT`을 올리면 이어서 진행한다.
   - `operator_required`: 다음 중 하나다.
     - 서비스가 늘거나 줄었다.
     - quant-data 고정 커밋이 바뀌었다.
     - `runtime.env`에 없는 값 때문에 compose가 렌더되지 않는다.
   - `held_for_drift`: 운영이 자기 릴리스와 다르다. 손으로 배포했거나 `runtime.env` 변경을 적용하지 않은 경우다.
   - `refused`: main이 운영 커밋의 후손이 아니다.
   - 운영자가 설치하는 호스트 파일(systemd unit, Caddyfile, storage cleanup 등)이 바뀌면 `operator_files`로 알린다. 이것만으로는 릴리스를 막지 않는다.
5. **시간 창과 진행 중인 작업을 피한다.**
   - 반영을 시작하는 기본 창은 KST 10:00–14:30, 19:40–23:00이다(`RELEASE_AUTO_WINDOWS`). 끝은 다음 브리핑 수집(15:50, 05:20)보다 한 시간 넘게 앞선다.
   - 턴, 브리핑 집필이나 모델 호출, 영상 제작이 진행 중이면 기다린다(`waiting_for_quiet`).
   - 백업이 호스트 잠금을 쥐고 있으면 기다린다.
6. **반영 절차는 손으로 하던 것과 같다.** prepare → build → migrate → apply → activate 순서다. apply는 그룹 순서로 진행하고, 서비스마다 조용해질 때까지 최대 10분 기다린다.
   - **멈춘 경우(`incomplete`):** 조용해지지 않았거나 잠금이 잡혀 있었다.
     - 서비스가 하나라도 옮겨졌으면 다음 실행이 끝까지 진행한다. 이때는 시간 창과 GitHub에 매이지 않는다.
     - 아무것도 옮겨지지 않았으면 처음 조건부터 다시 본다.
   - **실패한 경우(`failed`):** 그 밖의 모든 실패다.
     - 이미 옮긴 서비스를 이전 릴리스로 되돌린다. 되돌리기가 끝나지 않으면 다음 실행들이 계속 되돌린다.
     - 그 커밋은 다시 시도하지 않는다. 다시 시도하려면 `release.py auto --retry`를 실행한다. 다음 main 커밋은 다시 대상이 된다.
   - **성공한 경우:** 도구가 만든 릴리스 중 최근 3개를 남기고 나머지와 그 이미지를 지운다. 현재 릴리스, 실행 중인 컨테이너가 쓰는 릴리스, 렌더 커밋의 릴리스는 지우지 않는다.
7. **켜고 끄는 것은 `runtime.env` 값 하나다.**
   - `RELEASE_AUTO_ENABLED=true`일 때만 반영한다.
   - 그 밖에는 같은 판단을 하되 보고만 한다(`would_release`, `would_resume`, `would_restore`).
   - 손으로 릴리스하기 전에는 false로 둔다.
8. **알림은 운영 상태 감시(#146)가 이 파일을 읽어 보낸다.** 알리는 경우는 다음과 같다.
   - 상태가 `failed`, `error`, `render_required`, `operator_required`, `held_for_drift`, `drift`, `refused`, `no_green_main` 중 하나일 때
   - `incomplete`가 1시간을 넘을 때
   - CI를 통과한 main이 24시간 넘게 반영되지 않았을 때
   - 상태 파일이 45분 넘게 갱신되지 않았을 때
   - `operator_files`가 비어 있지 않을 때
9. **이전 Maintainer 릴리스 실행기는 끈다.** 대상은 `quant-company-release.timer`와 `maintenance_release.py`다. 2026-10-06부터 ExecCondition으로 매번 건너뛰고 있었고, ADR 0044의 단일 경로와도 맞지 않는다.

## 결과

- **main 병합이 곧 운영 반영이다.** 병합 후 다음 창에서 반영된다. 반영하고 싶지 않은 변경은 main에 합치지 않거나, `RELEASE_AUTO_ENABLED=false`로 멈춘다.
- **직접 변경이 드러난다.** 운영 서버를 직접 바꾸면 drift로 보이고 자동 반영도 멈춘다.
- **운영자 단계는 남는다.** 렌더 이미지와 호스트 unit은 여전히 운영자가 한다. 그 필요는 상태 파일과 알림이 알린다.
- **디스크와 빌드 시간:** 릴리스마다 모든 이미지를 새 커밋으로 다시 빌드한다. 지금 Dockerfile은 커밋 라벨(`ARG RELEASE_COMMIT`)이 의존성 설치보다 앞에 있다. 그래서 릴리스마다 약 4.7GB의 층을 새로 만들고, 빌드는 약 10분 걸린다.
  - 최근 3개만 남기므로 디스크 사용은 일정 범위 안에 머문다.
  - 다음 과제는 의존성 층을 커밋 라벨보다 앞으로 옮기는 Dockerfile 변경이다. 빌드 시간과 디스크를 함께 줄인다.
- **처음에는 보고 모드로 설치한다.** 판단이 맞는지 확인한 뒤 소유자 승인으로 반영을 켠다.
