# 실제 개선 요청 인수 — 2026-10-13

## 진행 상태

실제 로그인 소유자가 보낸 Slack 요청 세 건을 접수해 추적했다. 첫 요청의 실제 구독 모델 응답 8개는
조사 한도 소진으로 종료됐다. 이를 재현하면서 입력 보존·DB 조회·실행 순서를 수리해 설치했다.
두 번째 요청은 실제 개선 모델 응답 5개를 만들었으나 읽은 코드 누락과 시험 파일 표본 누락으로
조사 4회 한도를 소진했다. 후속 수리는 소스 시험을 통과했으며 실제 이미지 검증과 설치를 진행한다.
아직 봇의 코드 후보·실제 회귀 CI·수리 PR 완료 실적으로 집계하지 않는다.

운영자 서비스 수정은 [PR #134](https://github.com/JJongAchii/quant-ai-company/pull/134)다.
정확한 소스 커밋의 service·codex-protocol CI를 확인했고 서버의 독립 변경들을 유지한 작은
이미지 수정으로 자격검증했다. 일반 워커와 개선봇 실행 순서 수정은 10/13 01:38:25 UTC에 설치했다.
회사 호출 예약에만 19초의 일시 정지 임대를 사용하고 원상 복구했다. 다른 video Claude 작업은 계속 진행했다.

## 실제 요청과 중단 근거

| 요청 | 실제 접수 | 관찰한 결과 |
|---|---|---|
| 1 | [10/08 Slack 원문](https://achiisquantresearch.slack.com/archives/C0C3Q7BFQCE/p1791433176996799), job 672a94ca-b8e9-51b7-8fb0-354b96c7f355 | 실제 개선 모델 8회; 같은 코드 구간을 재요청하다 investigation_budget_exhausted; 원본 보존 |
| 2 | [10/13 Slack 원문](https://achiisquantresearch.slack.com/archives/C0C3Q7BFQCE/p1791850713261119), job c7d07401-ef8a-5d16-96f5-b6048b16dd1b | 실제 회사 접수 응답 3회; 조회·실행 순서 수리 후 실제 개선 응답 5회; 코드 구간 재요청과 시험 파일 누락으로 investigation_budget_exhausted |

두 요청은 실제 로그인 Slack GUI에서 각각 한 번 보냈다. 서명 이벤트·봇 echo·DB inbound를
사람의 요청으로 만들어 넣지 않았다. 과거 요청이나 불확실 호출을 초기화·재호출하지 않았다.

## 수리와 검증

### 읽었던 코드의 입력 보존

진단 이력과 주변 코드가 정작 최근 요청한 정확한 코드 구간을 밀어냈다.
진단 이력을 먼저 줄이고, 최신 exact range를 이전 주변 코드보다 먼저 보존하도록 바꿨다.
처음 후보는 실제 저장 요청을 설치 이미지에서 재구성한 검증에 실패해 설치하지 않았다.
두 번째 후보에서는 실제 필요한 네 구간 75·34·75·143줄을 모두 보존했다.
88,000자 제한·원본 요청·관찰 증거·조사 한도는 유지한다.

[실패한 첫 자격검사](evidence/maintainer-live-acceptance-20261008/qualification-v1-failed.json),
[통과한 자격검사](evidence/maintainer-live-acceptance-20261008/qualification-v2.json),
[설치 영수증](evidence/maintainer-live-acceptance-20261008/cutover-v2.json).

### DB 진단 조회 제한

5초 제한에 걸린 실제 SQL은 연구 단계 요청을 제외하기 전에 큰 JSONB를 문자열로 풀어 읽고 있었다.
owner·channel·기간·task kind를 먼저 걸러낸 뒤 같은 600,000-byte 조건을 적용한다.
실제 DB의 동일 repeatable-read 읽기 전용 snapshot에서 전체 review·재생 요청 6개·표본 제한·
evidence·roster·digest가 완전히 같은 것을 확인했다.

설치 이미지의 전체 처리 시간은 기존 26.93초에서 0.67초로 줄었다.
모델 호출·운영 DB 쓰기는 이 비교에서 0회다. 수정 이미지를 maintenance에만 적용했고
기존 두 job, 첫 요청의 응답 8개, 다른 컨테이너, 환경·권한·자원 설정을 보존했다.
실제 다음 주기에서 두 번째 요청이 review에서 triage로 진행했다.

[실제 조회 비교](evidence/maintainer-live-acceptance-20261008/history-selector-benchmark.json),
[전체 이미지 검증](evidence/maintainer-live-acceptance-20261008/qualification-v3.json),
[소스 CI](evidence/maintainer-live-acceptance-20261008/source-ci-v3.json),
[설치 영수증](evidence/maintainer-live-acceptance-20261008/cutover-v3.json).

### 직접 요청한 개선 작업의 실행 순서

현재의 연구 priority 100 실행도 모든 개선 호출을 막고 있었다.
직접 요청한 개선은 기존 배경 실행 뒤에 호출을 하나 예약할 수 있도록 바꿨다.
새 배경 호출은 그 예약이 due·허용 소유자/채널·현재 revision·활성/최근 heartbeat를 충족하면
양보한다. 원본 요청을 이미 만든 회사 호출과 priority 0 일반 사용자 요청은 보존한다.
일반 자동 진단은 기존의 회사 작업 우선 규칙을 유지한다.
재시도 due 이전·중지·권한 철회·revision 변경·낡은 heartbeat는 배경 작업을 잡아두지 않는다.

수리 전 PostgreSQL 회귀 시험은 두 assertion에서 실패했다.
수리 후 관련 시험은 50개와 기존 개선봇 18개가 통과했고 lint도 통과했다.
로컬 Temporal dev server가 5초 내 시작되지 않은 시도는 환경 오류로 별도 기록했다.
정확한 소스 98796b93f2bfce5f27f803dd36bf0a307d23bcf4의 전체 GitHub CI는 통과했다.

정확한 실제 worker·maintenance 코드에 작은 수정만 적용한 이미지에서 별도 real PostgreSQL을
실행해 기준 실패·후보 성공·동결 요청 보존·사용자 우선·예약 해제와 다섯 guard를 검증했다.
이 시험의 Slack/모델 기록은 모의이며 실제 구독 추론·Slack 전송·운영 DB 쓰기는 0회다.
시험용 DB 컨테이너와 네트워크는 제거했다.

[원본 중단과 소스 검증](evidence/maintainer-live-acceptance-20261008/admission-source-validation.json),
[실제 이미지의 격리 검증](evidence/maintainer-live-acceptance-20261008/admission-qualification.json),
[CI](evidence/maintainer-live-acceptance-20261008/admission-source-ci.json).

[설치 영수증](evidence/maintainer-live-acceptance-20261008/admission-cutover-v2.json).

### 실제 두 번째 조사에서 발견한 후속 결함

같은 요청의 구독 모델 입력은 34,498~38,666 tokens였다. 마지막 응답에도 코드 후보가 없었으며
새 PR은 만들지 못했다. 최신 요청 범위만 우선 보존하여 이전 producer·consumer가 번갈아 빠졌다.
필요한 기존 시험은 allowlist 안에 있고 커밋에도 존재하지만 2MB 전체 읽기 표본에서 제외됐다.
파일 이름 검색은 본문만 검색하여 이 파일을 찾지 못했다.

후속 후보는 모든 명시적 저장 범위를 우선 보존하고, 무관한 과거 기록과 반복 소스 미리보기를 먼저 줄인다.
요청한 안전한 파일은 같은 동결 커밋에서 기존 읽기 한도 안에 먼저 가져온다. 파일명 조회도 같은 본문 소비 경로로 이어진다.
이전 diagnostic revision의 미완료 호출이 배경 예약을 붙들지 않도록 현재 revision만 확인한다.
수리 전 실제 PostgreSQL 회귀는 관련 assertion에서 실패했고 수리 후 관련 시험 56개와 lint가 통과했다.
이 소스 시험의 GitHub·모델은 모의이다. 첫 후속 후보는 전체 CI가 통과했지만 실제 이미지의
고정 입력에서 필요한 범위를 유지하지 못해 설치하지 않았다. 이를 보완한 후보는 같은 실제 입력과
검증한 시험 blob을 로컬에서 재현하여 665줄을 모두 87,527자 안에 유지했다. 최종 소스 94b46be의 service·codex-protocol CI와 실제 이미지 자격검증이 통과했다.
04:36:59 UTC에 작은 수정 이미지를 설치했고 임시 회사 예약 정지를 복구했다. 다른 컨테이너·인증·모델·한도와 이전 두 요청의 응답 13개는 보존했다.
서버 이미지에서는 producer 213줄·case consumer 162줄·company consumer 90줄·요청한 시험 200줄, 합계 665줄을 87,640자 안에서 유지했다.
실제 GitHub의 같은 커밋 시험 blob SHA를 확인했고 읽기 배치는 1,999,830bytes였다. 운영 DB는 읽기 전용이다.
별도 PostgreSQL에서는 실제 worker Company와 maintenance Store 조합으로 stale 진단 예약 해제와 현재 진단 예약 보존을 검증했다. 모델·Slack은 모의이며 운영 DB 쓰기는 없다.

[실제 실패 응답](evidence/maintainer-live-acceptance-20261008/case2-exhausted.json),
[첫 후속 소스 검증](evidence/maintainer-live-acceptance-20261008/continuity-source-validation.json),
[미설치 후보의 실제 실패](evidence/maintainer-live-acceptance-20261008/continuity-qualification-v1-failed.json),
[보완 소스 검증](evidence/maintainer-live-acceptance-20261008/continuity-source-validation-v2.json).

## 세션 수

공통 회사 워커와 maintenance 워커는 각각 activity 하나를 실행한다.
일반 회사 호출과 개선 호출은 같은 Codex company lane의 단일 runtime lock을 공유한다.
news·quant 등 별도 lane은 따로 있다. 서버 전체를 한 세션으로 제한한 구조는 아니다.
이번 수리는 모델·effort·예산·공식 인증·동시 세션 수를 바꾸지 않는다.

## 세 번째 실제 요청

[실제 Slack 요청](https://achiisquantresearch.slack.com/archives/C0C3Q7BFQCE/p1791866371596809)은 한 번 전송했고
실제 owner·channel·서명 수신 기록·전체 요청 일치를 확인했다. 접수 응답 4회 후 job 297ebe7d-3189-5ecf-92cb-b6b01d4f4d5b가 조사 중이다.
필요한 기존 시험은 실제 live 소비 경로에서 자동으로 캐시에 읽혔다. 실제 입력은 첫 호출 38,339tokens, 다음 28,686tokens였다.
함수 이름을 포함한 명시적 파일 조회가 우선 범위로 연결되지 않아 producer와 current/card를 다시 요청했다. 이 경로는 별도 소스 회귀를 보완했고 57개 관련 시험이 통과했으며 아직 설치 전이다.
조사 중 main의 별도 #136 병합으로 기준이 77975ac으로 바뀌어 diagnostic revision1에서 다시 진단했다. 원본 revision과 응답은 보존했다.
현재 실제 코드 후보·회귀 CI·봇 PR은 아직 없다.

[후속 설치](evidence/maintainer-live-acceptance-20261008/continuity-cutover.json),
[실제 이미지 검증](evidence/maintainer-live-acceptance-20261008/continuity-qualification-v2.json),
[최종 소스 CI](evidence/maintainer-live-acceptance-20261008/continuity-source-ci-v2.json),
[세 번째 접수](evidence/maintainer-live-acceptance-20261008/case3-ingress.json),
[함수 조회 회귀](evidence/maintainer-live-acceptance-20261008/function-query-source-validation.json).

## 남은 인수

함수 조회 범위 보존의 후속 CI와 실제 이미지 검증을 마친 뒤 기존 호출 완료 경계에서 설치한다.
첫 두 요청과 실제 응답 13개는 보존한다. 조사 한도를 올리거나 종료 요청을 초기화하지 않는다.
수리 후 새 실제 Slack 요청에서 구독 모델 진단 → 실행 코드 수정 → 기준 실패/후보 성공 → 실제 CI → PR을 검토한다. 정확한 봇 후보를 검증한 뒤 기존 사람 승인 경계에 따라 병합·운영 반영을 진행한다.
무후보나 운영자의 서비스 수리를 봇의 자동 코드 수리 완료로 표시하지 않는다.

