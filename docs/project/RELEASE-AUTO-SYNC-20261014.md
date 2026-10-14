# main 자동 동기화 (2026-10-14)

ADR 0045의 구현과 운영 확인 기록이다. 결정과 이유는 [ADR 0045](../adr/0045-ci-green-main-releases-itself.md)에 있다.

## 만든 것

- `deploy/release.py auto`: 15분마다 운영과 CI를 통과한 main을 비교한다. 결과는 `/var/lib/quant-company/release-status.json`에 쓴다. `RELEASE_AUTO_ENABLED=true`일 때만 반영하고, 아니면 판단만 보고한다.
- `deploy/quant-company-release-auto.{service,timer}`: 매시 02/17/32/47분에 실행한다.
- `verify`가 compose 렌더를 서비스마다 하지 않고 한 번만 한다(`config --hash "*"`).
- 승인을 기다리는 영상 작업(`awaiting_approval`)은 video-worker를 바쁘게 만들지 않는다. 사람의 승인을 기다릴 뿐 워커가 하는 일이 없다.

## 운영 서버에서 확인한 것 (보고 모드, 05:26 UTC)

[근거](evidence/release-auto-sync-20261014/host-report-run.json). 임시 상태 디렉터리로 실행했고, 운영의 서비스·파일·설정은 바꾸지 않았다.

- GitHub 앱 토큰은 contents·actions 읽기만 받았다. CI 성공 main은 `17d7693`이고, 운영 `5454e3d`보다 커밋 2개 앞이다.
- tarball을 codeload에서 받았다. 커밋을 확인한 뒤 빌드 입력만 풀었다.
- drift 없음: 17개 서비스 모두 자기 릴리스와 커밋, 설정, 패키지 파일이 같다.
- 렌더 입력 변경 없음. compose는 `runtime.env`로 렌더된다. 진행 중인 작업도 없다.
- 결정은 `would_release`였다. 전체 판단에 24.7초 걸렸다.

## 운영 반영과 타이머 설치 (06:21 UTC)

[근거](evidence/release-auto-sync-20261014/host-release-ed77918.json).

- **릴리스:** #151을 병합한 main `ed77918`을 손으로 릴리스했다. 서비스 입력(코드, 의존성, Dockerfile, compose)은 `5454e3d`와 같았고, 이미지의 커밋 표시만 바뀌었다.
  - 걸린 시간: 빌드 10분, 그룹 7개 교체 12분.
  - 결과: 17개 모두 정착했고 되돌린 서비스는 없다. verify 17/17. PM 수집(06:50 UTC)보다 29분 앞서 activate했다.
- **보고 모드 타이머 설치:** `runtime.env`에 `VIDEO_RENDER_COMMIT=5454e3d…`를 넣었다(작업 정의 :9의 렌더 이미지 커밋). 첫 실행 결과는 `up_to_date`이고 drift는 없다.
- **이전 타이머 정리:**
  - 이전 실행기 `quant-company-release.timer`를 껐다.
  - 10/06에 게시 전환이 끝난 뒤 매일 실패하던 `quant-company-trend-preview.timer`를 껐다.
  - 실패로 남아 있던 일회성 관찰 unit을 정리했다. 실패한 unit은 이제 0개다.
- **디스크:** 이번 릴리스로 여유 공간이 39G에서 33G가 됐다.

## 남은 일

1. 보고가 맞는지 확인한 뒤 소유자 승인으로 `RELEASE_AUTO_ENABLED=true`를 켠다.
2. 운영 상태 감시(#146)가 상태 파일을 읽어 Slack으로 알린다. 알림 채널은 소유자가 만든다.
3. Dockerfile에서 의존성 층을 커밋 라벨보다 앞으로 옮긴다. 지금은 릴리스마다 약 10분 빌드가 들고, 디스크를 약 6GB(이미지 층 약 4.7GB + 빌드 캐시) 쓰며, 외부 저장소(PyPI, apt, Playwright)에 기대야 한다.
