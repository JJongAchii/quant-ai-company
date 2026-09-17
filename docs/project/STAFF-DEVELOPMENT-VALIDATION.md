# 직원 전문 절차·지속 평가 구축 인수

날짜: 2026-09-17. 구현 PR: [#27](https://github.com/JJongAchii/quant-ai-company/pull/27).
실제 직원 구성·모델은 유지하면서 전용 절차와 도구, 영속 평가·피드백 경로를 추가했다.

## 구현

- 전용 절차 11개: 총괄·금융전략·국내/글로벌/가상자산연구·데이터·개발·검증·리스크·운영·개선 담당.
- 실제 요청에 해당 직원의 절차와 digest를 전달. 데이터 담당은 구조화 ID와 설명의 마지막 대조도 수행하도록 보완.
- finance_compute: 채권 할인/듀레이션/볼록성/충격, 유럽형 옵션/Greeks, FX, 선형 노출/시나리오.
- data_quality: 제공 범위 내 결측·중복·미래 가용시각·기대 ID 누락. 전체 레이크 검사와 구분.
- PostgreSQL의 고정 문제/비공개 정답/모델 요청/응답/도구/점수와 Temporal 예약·복구.
- KST 03시 이후 하루 2개 과제, 최대 3개 호출/과제. 일반 업무 우선. 이미 실행 중 호출은 선점하지 않는다.
- 실패를 직원 피드백과 개선 관찰에 연결. 기존 PR 검증/사람 검토 유지. 채점기/문제/예산은 개선 BOT 수정 대상에서 제외.
- staff_status에서 일정·버전·실제 판정·연결된 개선 작업/PR 확인. 합성 시험은 시장 사실 기억으로 승격하지 않음.

## 검증 결과

### 시스템 검사

GitHub CI에서 **344 passed, 1 skipped, 1 deselected**. 미실행 real-model 표시의 의미를 유지했다.
[정확한 CI 실행](https://github.com/JJongAchii/quant-ai-company/actions/runs/35196110201).
수치 참조/유한차분/put-call parity, 입력 경계, 실제 PostgreSQL 동시 예약·재시작·권한·예산·이의 처리,
Temporal activity 재시도와 history replay, 개선 BOT의 중복 없는 실패 접수를 포함한다.
wheel에 11개 절차와 migration이 포함됨을 별도 확인했다.

### 실제 구독 모델

[최초 11개 직무 과제와 12개 실제 호출](evidence/staff-development-baseline-20260917.json):
**10개 객관 조건 통과, 데이터 1개 실패**. 역할별 원래 모델을 사용했으며 무료 fixture 응답은 아니다.
데이터 직원은 설명에 올바른 중복 행을 적었지만 반환 JSON에 다른 ID를 넣었다. 실패 기록을 보존했다.

데이터 절차에 원본 ID/설명/구조화 목록의 최종 대조를 추가했다.
[사전 고정한 새 6개 사례의 동일 조건 비교](evidence/staff-development-data-comparison-20260917.json)는
**기존 6/6, 수정 6/6**이었다. 수정안의 이 범위 통과는 확인됐지만 우월성/통계적 개선은 입증되지 않았다.
이 비교의 모든 입력·응답과 계획 digest를 보존한다. 공개된 사례는 이후 연습 자료이며 미지 시험으로 재사용하지 않는다.

이 문제은행은 알려진 22개 유형에서 새 수치·순서·사례를 생성한다. 서술 품질과 종합 금융 능력은 미인증이다.
전문가 대비 우월성, 새로운 영역으로의 일반화, 실제 전략 성과나 고빈도 실행 능력을 주장하지 않는다.
평가 문제 풀이가 모델 가중치를 자동 학습시키는 것은 아니다.

## 운영 인수

- 배포 커밋: `9fe9786c46c16d7aed2cf0b5fc1bed08d7e3b3a6`. 기존 2GB Lightsail·Codex 구독·Temporal Cloud를 사용했다.
- [배포/백업 기록](evidence/staff-development-deploy-9fe9786c46c1.json): 7개 컨테이너 정상, PostgreSQL 재생성 없음.
- [실제 운영 상태와 총괄 응답](evidence/staff-development-production-status.json): 정기 평가 2건(총괄·금융전략),
  실제 구독 호출 3회, 두 과제의 객관 조건 통과. 총괄의 일반 업무가 staff_status를 1회 호출하고 완료했다.
  총괄이 읽은 시점에는 금융전략 과제가 진행 중이었고, 뒤의 최종 조회에서 완료됐다.
- [worker/dispatcher 재시작](evidence/staff-development-restart.json): 같은 Temporal run ID,
  완료 평가 2개와 호출 3개 유지. 추가 평가/호출 중복 없음.
- Slack 게시 자체는 이번에 새로 시험하지 않았다. 일반 업무 경로의 실제 모델·도구·DB 왕복을 검사했다.
  사용자에게 Slack 메시지를 추가로 보내달라고 요구하지 않았다.
- [호스트 상태](evidence/staff-development-host.json): OOM 없음, release/backup timer 활성.
  배포 이미지 누적으로 디스크가 98%가 되어 [1시간 넘은 미사용 빌드 캐시만 정리](evidence/staff-development-cache-cleanup.json)했다.
  가용 공간은 약 1.46GB→7.62GB. 현재/직전 배포 이미지는 보존했고 서버를 증설하지 않았다.
- 운영 DB의 system_verifications에도 현재 코드/설정에 묶인 한정된 인수 기록을 남겼다.
  정기 일정이 활성 상태이므로 맥북이나 이 대화 세션이 종료되어도 서버가 다음 평가를 계속한다.


## 다음 전문성 확대

실제 업무 실패/반례를 근거로 문제 유형과 검토된 지식을 확장한다. 전문 지식·도구·절차 변경은
새 사례와 실제 업무에서 확인한다. 비활성 여섯 직무의 운영 활성화, 전체 레이크 검사, 3070 실제 연구 연결은
별도 후속 구현 범위다. 이번 교육이 그 기능을 구현하거나 대신했다고 주장하지 않는다.

운영 사용법: [직원 교육·평가](../staff-development.md).
