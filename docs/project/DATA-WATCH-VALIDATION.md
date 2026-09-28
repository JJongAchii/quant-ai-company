# data-watch 구현·검증

2026-09-22. 승인된 두 번째 구현 범위. [전체 채널 설계](SLACK-COMPANY-DESIGN.md)와
[사용·설치 절차](../data-watch.md)를 따른다. 회사 저장소의 기존 PR에 함께 통합하며
전용 Maintainer의 검증 기록은 [별도 문서](IMPROVEMENTS-VALIDATION.md)에 보존한다.

## 구현 범위

- 30분 catalog 관측과 신규·변경·실패 객체에 한정된 footer 검사. 객체 identity가 바뀐
  지연 응답을 현재 결과로 수락하지 않고, 채택·폐기 영수증을 각각 보관한다.
- 업로드/데이터/검사 날짜 구분, 명시적 공표 달력, 기준 미등록·미검사·지연 표시.
  달력 만료만으로 기존 지연 장애를 복구로 바꾸지 않는다.
- 매일 09:00 KST 이후 첫 tick의 당일 요약, 같은 장애의 상태 전환과 증거 기반 복구.
  불명확한 Slack 루트·후속 쓰기를 재전송하지 않는다. 모델 호출 없이 동작한다.
- 등록된 승인 ETF 연구의 정확한 입력 6개를 기존 워커에서 읽는 검사와 typed receipt API.
  512 MiB 입력, 파일별 400만 행, 64 MiB row group, 180/190초 제한. 입력 교체·전략 실행 없음.
- 전달 및 개선봇 진단 직전까지 소유자·채널·연구 revision·정책을 재검사한다.
  승인 변경 뒤의 오래된 장애 알림과 연구 내용이 포함된 요약도 차단한다.
- 새로 관측되고 실제 게시된 장애를 improvements의 기존 케이스/승인 경로에 한 번 연결한다.
  새 직원이나 모델을 추가하지 않고 활성 data 신원을 재사용한다.

## 실행 검증

최종 결과와 검증 파일 SHA-256은 [기계 판독 영수증](evidence/data-watch-validation.json)에 기록한다.

| 검사 | 결과 | 실제/모의 범위 |
|---|---|---|
| `uv run pytest` | 1029 passed, 37 skipped | 실제 PostgreSQL·Temporal / 합성 Slack·데이터·모델 |
| 배포 설정 검사 | 28 passed, 0 skipped | Docker Compose 해석, 운영 배포 아님 |
| 금융 fixture 검사 | 3 passed, 0 skipped | 기존 CI의 결정적 fixture |
| `uv run ruff check .` / `git diff --check` | 통과 | 코드·패치 검사 |

전체 테스트는 이 worktree 전용 PostgreSQL 14의 임시 DB와 실제 로컬 Temporal을 사용했다.
최초 구현 뒤 기본 22개, 확장 경계 조건과 주변 회귀 69개를 단계적으로 확인한 후 최종 전체
회귀 검사를 수행했다. main의 감사 증거 통합 커밋도 병합한 상태로 검사했다.

새 기능 검증은 다음을 포함한다.

- 전체/변경 조회 주기와 회당 상한, catalog 실패 시 이전 목록 보존, 오래된 lease와
  다른 객체 descriptor 거절, 서로 다른 날짜의 표시, 정책 만료·미검사 상태.
- 동일 장애·반복 tick·프로세스 재시작의 중복 억제, Slack 루트 영수증·복구 스레드·불명 전송,
  게시 직전 원래 연구 승인 변경 차단, 09:00 KST 경계와 과거 요약 미발송.
- 실제 Parquet fixture 전체 행 검사 → typed receipt → 인증된 FastAPI → PostgreSQL 저장.
  입력 hash·scope·소유자·revision·시각·측정값 불일치 및 기존 결과 덮어쓰기 거절.
- 실제 checker subprocess의 고정 recipe/누락 입력 경로와 API 연결. 서버 저장 뒤 응답을
  유실시켜 같은 영수증 재제출을 검증했다. 성공한 실제 3070 원본 검사를 주장하지 않는다.
- 검사 subprocess 자격 증명 격리, reader timeout, 구버전 워커의 미등록 scope 거절,
  symlink/입력 변경·중복키·결측·비유한 값·범위 밖 날짜 탐지.
- 실제 Temporal worker 재시작·activity 재시도·workflow history replay 후 요약 한 건 유지.
  모델 실행 경로를 금지한 상태에서도 별도 data-watch worker가 실행됨을 확인했다.
- 전송된 장애에서 개선 케이스 한 건 생성, 기존 maintenance/Slack/연구 워커 회귀,
  실제 Docker Compose 설정 해석과 기존 프로세스 전체의 flag/읽기 전용 달력 mount.

## 운영 활성화와 미검사 범위

**운영 Slack·3070 활성화는 수행하지 않았다.** 새 Slack 채널 생성/초대, 현재 운영 S3 재조회,
실제 승인 snapshot에 대한 새 워커 검사, 새 기능의 실제 구독 대화는 이 인수에 포함하지 않는다.
Slack/모델/GitHub 응답은 합성이며, 데이터 fixture는 실제 시장 데이터가 아니다. 실서비스로
표시하지 않는다. 이미 운영 중인 다른 기능의 인수 기록도 이 문서로 덮어쓰지 않는다.

기능/게시/핵심 검사 flag의 기본값은 모두 false다. 최신성 달력을 등록하지 않은 데이터는
기준 미등록이다. 모든 거래일의 누락, prices/meta 간 전체 키 대응, metadata 속성의 의미,
인과성 및 전략 성과는 이번 필드 검사로 검증되지 않는다. 실제 운영 인수는 사용 절차에 따라
정확한 배포 커밋과 등록 입력으로 별도 기록한다. 다음 구현은 본사 요약과 요청형 시장 분석이다.
