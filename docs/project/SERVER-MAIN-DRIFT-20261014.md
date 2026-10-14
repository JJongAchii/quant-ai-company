# 운영 서버와 main 차이 대조 (2026-10-14)

**스냅샷과 방법**

- 시점: 10:51~10:54 KST
- 기준 main: eed730e (#140 병합 직후)
- 범위: 운영 컨테이너 18개 중 postgres를 뺀 17개
- 증거: `docs/project/evidence/server-main-drift-20261014/`
- 읽기 전용으로 수집했다: `docker inspect`, 컨테이너 안 Python, `pg_dump --schema-only`, Temporal 조회.
- 비밀값은 호스트 밖으로 나가지 않았다. 실행마다 새로 만든 salt로 계산한 digest만 비교했다.

## 코드

- **파일 차이:** 각 컨테이너 안의 `quant_company` 파일을 git blob 해시로 계산해 같은 경로의 Git 이력과 대조했다(`code-drift.json`).
  - 모든 서비스가 main과 다르다. 같은 파일 100~222개, 뒤처진 파일 26~58개, main에만 있는 파일 13~118개였다.
  - 어느 서비스도 하나의 커밋과 일치하지 않는다.
- **Git 소스에 없는 버전 53개:** 줄 단위로 main과 대조했다. 모두 main에 이미 있는 기능을 옛 형태로 조합한 것이었다. 예외는 worker의 `company.py`에 들어간 Maintainer 입장 순서 변경 한 가지로, #134 계열이다.
- **원격 브랜치에만 있던 버전 12개:**
  - 연구 `audit_delivery.py`(`MAX_NOTES` 24000, main 8000)
  - Maintainer 6개 파일
  - 영상 5개 파일
  - 연구와 Maintainer는 #144로, 영상은 #140으로 main에 들어갔다.
- **PR별 배포 상태:** `pr-deployment-matrix.txt`에 있다. 각 PR이 추가한 줄 중 main에 남아 있는 줄이 각 서비스 파일에 있는지 표시했다.

## 의존성과 실행 파일

- **Python 패키지:** 모든 컨테이너가 main `uv.lock`과 같은 버전이다. video-worker의 cryptography만 다르다. 다만 6개 서비스(claude-runtime, data-watch, housing, maintenance, quant-codex-runtime, quant-feed)에는 브리핑 일정용 `exchange-calendars` 등 4개 패키지가 없다. main으로 바꿀 때는 이미지를 다시 빌드해야 한다.
- **CLI:** Codex 0.160.1과 Claude Code 2.1.280. main 고정값과 같다.

## 설정

- **환경값:** 147개 중 137개가 모든 서비스에서 같다(`env-consistency.json`). 다른 10개는 다음과 같다.
  - 일부러 다른 값: 자격 파일, 이미지 런타임 timeout, 퀀트 전용 런타임 주소
  - 메타데이터: 코드 커밋 표시, `PYTHONPATH`
  - 실제로 어긋난 값: `SLACK_ALLOWED_CHANNELS` 세 묶음, `QUANT_FEED_PUBLISH_ENABLED`, 브리핑 값 3개
- **실제 설정값 비교:** 같은 env와 비밀 파일로 main 코드를 격리 실행했다(`settings-main-vs-running.json`). 17개 모두 설정 로딩과 전체 모듈 import에 성공했다. 값이 바뀌는 기존 설정은 `company_max_project_tasks` 하나다(40→0). 이 값을 쓰지 않는 3개 서비스에서만 바뀐다.
- **main compose + `runtime.env` 렌더:** 그대로 렌더하면 다음이 바뀐다.
  - 영상 제작이 꺼진다.
  - 미국 장마감 브리핑이 꺼진다.
  - 렌더 병렬도와 Runway 작업공간이 기본값으로 돌아간다.
  - 필수 렌더 변수 5개가 없다.
  - `video-image-runtime`은 정의가 없다.
- **#145 반영 후:** 17개 중 13개가 운영과 정확히 같게 렌더된다(`config-parity.json`). 나머지는 동작이 같거나 의도한 차이다.
  - 이미지 기본 명령과 같은 command
  - profile
  - 비밀 파일 전달 방식
  - `pids_limit`

## 실행 상태

- **Temporal:** 실행 중인 워크플로 20개(`temporal-running.json`)의 정의 파일이 모두 main과 같다. 교체해도 재생은 안전하다. 다만 `brief-data`, `accounts`, `data-watch` 큐는 전용 worker와 일반 worker가 함께 처리한다. 둘의 코드 버전이 달라 같은 활동이 어느 쪽에서 도는지에 따라 동작이 달랐다.
- **DB:** 1.5GB, 테이블 131개. 운영 스키마 복사본에 main 마이그레이션을 적용하니 성공했다. 추가만 있었다.
  - 연구 계획 테이블 7개가 생긴다.
  - `research_mission_stages`(143행)에 nullable 열·인덱스·제약이 하나씩 붙는다. 새 제약은 새 열이 있을 때만 적용돼 옛 코드와도 호환된다.
  - 큰 테이블은 바뀌지 않는다.
- **호스트:**
  - Git 밖 스크립트로 도는 `quant-company-trend-preview`가 10/13부터 매일 실패한다.
  - 10/12에 띄운 임시 감시 유닛이 실패 상태로 남아 있다.
  - Maintainer 릴리스 타이머는 실행 조건에서 계속 건너뛴다.

## 같은 날 조치

- **허용 채널:** slack-socket, account-gateway, worker의 허용 채널을 api와 같은 10개로 맞췄다. 이미지는 그대로 두었다. 기록은 `/var/lib/quant-company/releases/allowed-channels-20261014/`에 있다.
- **배포 동결:** 다른 세션에 운영 변경을 멈추라고 공지했다.
- **PR:** #144(운영 lineage를 main으로), #145(compose 정합), 스키마 digest, #147(릴리스 도구), ADR 0044.
