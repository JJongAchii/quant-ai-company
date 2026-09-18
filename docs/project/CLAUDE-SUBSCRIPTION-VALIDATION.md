# Claude 구독 독립 검토 운영 확인

2026-09-18. [PR #31](https://github.com/JJongAchii/quant-ai-company/pull/31)을 머지하고
`f49d7ea1f3c1cdc76107ee59496c171e21e9f0d3`을 기존 회사 서버에 배포했다.
사용자가 Claude Pro 구독·추가 과금 비활성화·공식 로그인 완료를 확인했다.
직원 개발과 독립 검토가 활성화되어 있으며, **정기 검토는 하루 총 2건**이다.
11명 모두 매일 평가하는 일정으로 바꾼 것은 아니다.

## 확인한 결과

- [최종 CI](evidence/claude-subscription-ci.json): PostgreSQL·Temporal·배포 계약·금융 fixture 포함
  **403 passed, 1 skipped, 1 live deselected**. 모델 응답/CLI/GitHub 효과는 CI에서 명시적 fixture다.
- [실제 답안 검토](evidence/claude-subscription-live-staff.json): 국내연구·데이터 직원의 기존
  답안 2건을 Temporal 직원 개발 루프가 자동으로 검토하고 DB에 저장했다. 실제 모델은 모두
  `claude-opus-5`, CLI는 `2.1.275`, effort는 high다. 인용 검증·원 답안 binding이 통과했고,
  배포 전과 비교해 객관 점수가 바뀌지 않았다. 네 차원 모두 supported였으나 모델의 보조 의견이다.
- [기본 구분 확인](evidence/claude-subscription-sanity.json): 별도로 실행한 정상/오답 산술 2건은
  기대한 방향으로 구분했다. 첫 연결 probe의 불필요한 가정 지적을 보고 루브릭을 보완한 뒤의 결과다.
  기준 조정에 사용한 사례이므로 독립적인 정확도 평가나 종합 전문성 증거로 사용하지 않는다.
- [실행기 교체 후 재사용](evidence/claude-subscription-recovery.json): 두 완료 결과를 새 컨테이너에서
  같은 ID로 재조회했다. 응답과 영수증 바이트가 동일하며 새 요청 ID를 만들지 않았다.
- [배포 영수증](evidence/claude-subscription-deployment.json): PostgreSQL 컨테이너를 재생성하지 않았고
  역할 설정도 보존했다. cutover 전에는 업무와 발신 대기열이 비어 있음을 확인했다.
- [운영 관찰](evidence/claude-subscription-operations.json): 실행 중인 8개 서비스 모두 OOM/재시작이
  없었다. 이번 실제 답안 검토에서 Claude 컨테이너의 memory.peak는
  **206.7 MiB / 512 MiB**였다. 관찰 시 호스트 가용 메모리는
  **888.2 MiB**였으며 최대 동시 부하 보장은 아니다.
  이전의 재생성 가능한 미사용 이미지 31개를 정리했고 현재/복구용 이미지·DB·볼륨·백업은 보존했다.
- 실제 cutover 전 백업에 Claude 영수증 2개가 포함됐고 인증 자료는 없었다. S3 백업과 SHA-256
  manifest를 남겼다. 이 확인은 새로운 서버로 전체 DB를 복원한 시험을 뜻하지 않는다.

## 운영 범위

다른 계열 모델을 연결했지만 **평가자는 아직 보정되지 않았다**. 결과의
`not_yet_calibrated` 표시를 유지하며, 기존 객관 점수나 자동 절차 승격 기준을 덮어쓰지 않는다.
추후 실제 업무의 정상/오답/정보 부족 사례와 독립 기준으로 오판·누락을 비교해야 한다.
직원의 장기 전문성 향상, 광범위한 정확도, 큰 문서·동시 부하의 안정성을 입증한 결과는 아니다.

정기 검토는 기존 업무·문제 풀이 다음 순서다. 구독 한도 오류는 대기하고 API 과금으로 전환하지
않는다. 계정의 추가 과금 설정은 소유자 확인에 근거하며 실행기가 결제 설정을 잠그는 기능은 없다.
Claude Code가 출력하는 API 상당액 추정치를 실제 구독 청구액으로 기록하지 않는다.
운영·재로그인·중단 방법은 [Claude 실행기 문서](../claude-runtime.md)를 따른다.
