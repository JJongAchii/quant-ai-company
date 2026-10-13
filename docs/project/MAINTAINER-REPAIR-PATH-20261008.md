# 개선봇 진단 → 수리 경로 — 2026-10-08

## 현재 결과

2026-10-08 **11:23 KST**, 검증한 소스 `68ffc8d1c16aaace04b98c259e49055beeb857b9`의
수정 파일을 실제 maintenance, 공통 worker, Claude runtime 이미지에 적용했다.
각 설치 파일의 SHA256과 정상 실행, Reporter heartbeat를 확인했다.
회사 소스는 [PR #123](https://github.com/JJongAchii/quant-ai-company/pull/123)에서 검토한다.

이는 운영자가 적용한 개선봇 서비스 수리다. 개선봇이 실제 새 코드 수리 PR을 자동 완성한 실적은
아직 없다. 모델·GitHub·Slack 응답을 모의한 검증과 실제 설치 결과를 아래에서 구별한다.

## 실제 중단 원인

최근 improvements 사례 6건 중 2건은 수정 후보 없이 종료됐고 4건은 보류됐다.
이 6건에는 수리 PR이나 운영 반영 기록이 없다. 역사상 봇의 반영 완료 1건은 문서 변경이었다.
기존 토큰 최적화는 운영자 작업이므로 봇의 자동 수리 실적에 포함하지 않는다.

마지막 사례 `c44f74be-7d4d-4aa1-a789-a4edd1ee5374`는 독립 설명 검토의 불확실 실행을 조사했다.
직원의 객관 평가 실패를 의미하지 않는다. 실제 모델 호출 5회에서:

- 요청한 코드가 입력 예산에 여유가 있어도 2,500자로 잘렸다.
- 다음 stateless 호출에는 최근 발췌 4개만 본문으로 전달됐다.
- 앞서 읽었던 호출자·검증 코드가 사라져 같은 구간을 다시 요청하다 조사 한도를 소진했다.

원본 독립 검토 12건은 요청 digest가 runtime 영수증과 일치하지만,
실행은 failed/uncertain이고 원 CLI 출력이나 검증된 응답이 없다.
이 메타데이터만으로 원인·완료·복구 성공을 확정할 수 없다.
[실제 채널 감사](evidence/maintainer-repair-path-20261008/channel-audit.json),
[마지막 입력](evidence/maintainer-repair-path-20261008/observed-inputs.json),
[검토 영수증](evidence/maintainer-repair-path-20261008/review-receipts.json)을 보존했다.

## 바꾼 동작

- 이전에 읽은 코드 본문을 다음 호출에도 전달하고, 겹치거나 인접한 줄만 합친다.
  읽지 않은 간격은 분리한다. 총 88,000자 한도와 원본 조회 영수증을 유지한다.
- 시스템 설명을 먼저 압축하고 요청한 코드를 우선 보존한다.
- 기존 호출이 없는 자동 진단에서 원본과 정확히 연결된 불확실 독립 검토만 관찰됐다면,
  새 모델 호출을 시작하지 않고 `review_reconciliation_required`로 인계한다.
  명시적 사용자 요청·다른 오류·원본 불일치에는 이 생략 경로를 적용하지 않는다.
- “수정 후보 없음”을 수리·PR·운영 반영 완료와 구별해서 안내한다.
- 앞으로 CLI 프로세스가 반환한 종료 코드, stdout/stderr 길이·해시와 서비스의 거부 사유를
  비공개 영수증에 보존한다. 과거에 없어진 CLI 출력은 복구하지 않았다.

## 검증 근거

| 검사 | 실제 실행과 한계 |
|---|---|
| 기준 코드 재현 | 기존 코드에서 문맥 보존·불필요 절단·수리 전이·CLI 영수증의 assertion 4개 실패 |
| 관련 검사 | 실제 PostgreSQL·pytest subprocess에서 57개 통과; 마지막 수리·제공자 검사 39개 통과 |
| 수리 → PR 전이 | 기존 회귀시험 실패 → 수정 후보의 추가 실패 → 피드백을 받은 재수정 → 실제 pytest 통과 → 모의 GitHub PR |
| 전체 CI | 정확한 소스 head에서 실제 PostgreSQL·Temporal 검사 1,655 통과, 47 skip, 1 deselect; lint 통과 |
| Codex protocol | 공식 Linux CLI의 인증 없는 설정·catalog 검사 9개 통과; 모델 추론 없음 |
| 실제 입력 비교 | 저장된 진단 5개를 원래 이미지에서 동일하게 재구성하고 수정 이미지와 비교; DB 쓰기·모델 호출 0회 |
| 실제 설치 | 각 기존 이미지를 기준으로 필요한 파일만 교체; 설치 SHA256·모듈 import·서비스 경계 확인 |

실제 마지막 입력에서 유지된 원본 코드 줄은 **111 → 497줄**이었다.
마지막 모델이 재요청한 네 구간은 모두 수정 입력에 포함됐다.
각 입력은 88,000자 이내다. 이 재구성에서는 더 많은 본문을 보존하면서 입력 문자 수가 증가했다.
새 실제 모델 호출 수나 토큰 절감률은 측정하지 않았다. 불확실 검토만 있는 새 자동 사례의
0회 호출 동작은 PostgreSQL 검증에서 확인했다.

[소스 CI](evidence/maintainer-repair-path-20261008/source-ci.json),
[CI 요약](evidence/maintainer-repair-path-20261008/ci-summary.json),
[이미지 자격검사](evidence/maintainer-repair-path-20261008/qualification.json)를 참조한다.

## 운영 적용과 보존

연속 대화의 다음 호출이 drain 도중 시작돼 첫 교체가 중단됐다. 수정 이미지가 적용되기 전에
같은 원래 컨테이너를 복구했다. 이후 새 호출만 120초 임대 단위로 대기시킨 상태에서
기존 실행이 끝난 뒤 교체했다. 이 대기 설정은 17초 뒤 원래 값으로 복원됐다.
접수 대기 설정의 실제 DB 쓰기는 2회이며, 입력 비교·최종 읽기 검증의 DB 쓰기는 0회다.

- maintenance·worker는 실행 중이며 Claude runtime은 healthy이고 각 restart count는 0이다.
- 기존 maintenance 호출 **90건**, 불확실 독립 검토 **12건**과 연결된 직원 평가 원본은 동일하다.
- 기존 모델·effort·한도·인증·환경·권한·mount·네트워크·자원 제한을 보존했다.
- 다른 서비스 컨테이너와 전역 release 링크는 바꾸지 않았다.
- 기존 완료/차단 사례, 요청·응답·승인과 불확실 외부 효과는 초기화하거나 재실행하지 않았다.
- 실제 Slack에 새 인수용 메시지를 보내지 않았다. 안내 문구의 readback은 전송 없는 검증이다.

[첫 보류 영수증](evidence/maintainer-repair-path-20261008/cutover.json),
[원 컨테이너 복구](evidence/maintainer-repair-path-20261008/drain-recovery.json),
[최종 적용 영수증](evidence/maintainer-repair-path-20261008/cutover-v2.json),
[접수 설정 복원](evidence/maintainer-repair-path-20261008/admission-during-cutover.json),
[실제 적용 후 검증](evidence/maintainer-repair-path-20261008/live-after.json)을 남겼다.

## 남은 인수

다음 실제 재현 가능한 사례에서 개선봇이 진단 → 코드 수정 → 회귀검증 → PR을 완성하는지 확인한다.
그 전까지 자동 코드 수리의 운영 인수가 끝났다고 주장하지 않는다. 후보 PR의 병합·운영 반영은
기존 사람 승인 경계를 따른다.

기존 불확실 검토 12건의 원 출력·결과가 없는 문제는 남아 있다.
원 식별자와 입력 digest에 연결된 근거를 확보해야 대사할 수 있으며 재호출로 추정하지 않는다.
