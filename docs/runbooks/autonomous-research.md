# 지속형 연구 배포·복구

운영 반영은 준비된 PR과 아래 명세를 검토한 뒤 한다. 이 문서 작성은 배포 승인이 아니다.
기존 [연구 워커 절차](research-worker.md)와 상태 백업 도구를 사용한다. 새 서버는 만들지 않는다.

## 배포 묶음

다음 식별자를 하나의 배포 영수증에 고정한다.

- 회사 exact commit과 Git bundle SHA-256, 고정 qdata commit.
- 연구 quant-lab exact commit과 source bundle SHA-256.
- `AdaptiveExecutionProfile` 전체와 digest: 보호된 평가 진입점·코드 경로,
  warmup/dev 입력 이름, 격리 Python·라이브러리 mount digest와 단계 시간 제한.
- 실제 입력 6개 SHA-256과 lake ID; 개인 홈·전체 lake·sealed 경로를 매핑하지 않는다.
- 서버 `ServerResearchProfile`과 `research-qlab.json`, 워커 `AdaptiveProfile`과 release registry.
- 이전 서버 image/env/config와 이전 워커 release/config. 현재 진행 중 작업의 기존 commit도 보존한다.

검토 자료에는 비밀정보를 넣지 않는다. 토큰·AWS·Slack·Codex 인증은 기존 secret 경로로만 연결한다.
source bundle은 기존 신뢰한 quant-lab checkout에서 만들며 후보가 공급한 다운로드 주소를 사용하지 않는다.

## 활성화 전 준비

1. 운영 서버/3070의 현재 release와 진행 중 job을 읽어 확인한다. 동시 배포가 있었다면 실제
   최신 커밋과 통합한 후 다시 검증한다. Reporter·maintenance 설정을 과거 예제로 덮어쓰지 않는다.
2. 변경 커밋에서 전체 회사 검사, 합성 ETF producer→consumer, 실제 3070 sandbox 검사를 수행한다.
   실제 qlab이 없는 CI의 skip과 실제 로컬 qlab 검증을 구별한다.
3. `scripts/prepare_research_runtime.py`로 3070의 기존 P11 Python 3.11에서 오프라인 런타임을
   새 경로에 준비한다. 허용한 numpy/pandas/pyarrow와 전이 패키지만 복사한다. profile의
   소스 경로는 준비 완료 후 이동하지 않는다. 격리 import·parquet roundtrip을 확인한다.
4. 서버에 `research/provisioned/` 번들을 준비하고, `research-audit/qlab`에 고정된 **독립 clone**을
   준비한다. `.git`이 다른 worktree 경로를 가리키는 복사본은 사용하지 않는다. 앱 UID 10001이
   읽고 Git clean/head를 검사할 수 있어야 한다. qlab core와 정책은 읽기 전용 mount다.
5. `config/research-profiles.json`의 bundle 경로는 컨테이너 기준 `/state/research/provisioned/...`로,
   `config/research-qlab.json`은 `root=/opt/research-audit/qlab`, 승인된 qlab commit,
   `python_executable=/app/.venv/bin/python`으로 준비한다. 실행 프로필 공개 부분은 서버·3070 동일해야 한다.
6. 워커의 기존 전송·token·state 경로를 보존한다. `prepare_release`로 새 회사 코드와 설정을 함께
   검증하고 이전 release도 registry에 등록한다. 아직 active config를 바꾸지 않는다.
7. 기존 백업/배포 잠금 하에 일관된 DB·설정·research·research-audit·profile 백업을 만든다.
   비밀 파일은 기존 별도 복구 경로로 보존한다. PostgreSQL 컨테이너를 교체하지 않는다.

## 검토 후 활성화 순서

1. 연구 job 제출을 잠시 멈추고 진행 중 실행의 ID·lease·프로세스 상태를 기록한다.
2. exact release의 `autonomous-research` Docker target을 빌드한다. git와 잠긴 PyYAML을 포함한다.
   compose는 기본 + `research.compose.yaml` + `autonomous-research.compose.yaml` 순서다.
   `docker compose config --quiet`를 사용해 출력에 비밀 환경변수를 노출하지 않는다.
3. 새 DB 스키마를 migrate한다. 표준 서비스 절차에서 서버 앱만 교체한다. 기존 역할 설정을
   보존하고 director/금융/연구/검증 Astra max, engineer Sol max, data Terra high를 확인한다.
4. 워커의 준비된 release를 `activate_release`로 원자적으로 선택하고 기존 poller만 정상 재시작한다.
   이전 실행은 등록된 이전 release로 대사한다. UID 전체 종료나 실행 중 하위 프로세스 종료를 하지 않는다.
5. 기능 플래그 `COMPANY_AUTONOMOUS_RESEARCH_ENABLED=true`를 승인된 overlay에 적용한다.
   서버와 워커의 정확한 commit·profile identity를 검사한다. 잘못된 pin은 실행 전에 거부되어야 한다.
6. 준비한 첫 mission 명세를 원래 research-center 프로젝트의 승인 출처와 연결해 게시한다.
   사용자의 실제 Slack 승인 전에는 과학 job을 만들지 않는다. 사용자의 새 명세 승인도 배포 승인과 구별한다.
7. 실제 승인 → 두 가지 이상 distinct trial → 반론/이전 결과에 따른 다음 선택 → 독립 감사 →
   S3 바이트 대조 → 총괄 출처 소비 → 실제 Slack 본문·소유자 태그를 확인한다.
   라이브 모델이 정해진 수식 밖을 제안하면 범위 밖으로 실행하지 않고 수정/대기로 남긴다.

## 롤백

1. 새 mission scheduling을 비활성화하고 진행 중 job 상태를 보존한다. 결과나 승인 행을 지우지 않는다.
2. 이전 server env/config로 복원하고 이전 image로 앱 서비스만 되돌린다. 이전 커밋에 새 overlay가
   없으면 플래그와 compose 파일 목록도 함께 되돌린다. DB의 추가 테이블을 drop하지 않는다.
3. `rollback_release(active_config=...)`로 검증된 이전 worker config를 선택하고 poller를 정상 재시작한다.
   새 job이나 uncertain launch가 있으면 그 release/입력/상태는 회수하지 않고 대사한다.
4. 기존 대화·Reporter·maintenance 건강 상태, 이전 P11 job source, 실제 outbox와 DB 재개를 확인한다.
   필요하면 검증된 백업을 별도 대상에서 복원해 비교한다. 운영 DB에 덮어쓰는 복구는 별도 결정이다.

상태 복구 시 research-audit와 연구 source bundle/profile도 DB와 함께 복구해야 한다. 워커 release
경로·runtime 경로·입력 SHA가 바뀌면 기존 job을 임의의 최신 버전으로 실행하지 않는다.
전역 `docker system prune`, 전체 process/UID kill, 운영 DB 컨테이너 재생성은 이 절차에 없다.
