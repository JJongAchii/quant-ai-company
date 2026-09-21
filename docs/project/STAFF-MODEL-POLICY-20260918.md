# 직원 모델·effort 정책 — 2026-09-18

사용자 승인과 정책은 [ADR-0027](../adr/0027-explicit-staff-model-effort.md)에 기록했다.

## 변경

director와 금융 판단 담당은 Astra max, 개발·개선 BOT은 Sol max, 데이터·운영은 Terra high다.
독립 설명 검토는 기존 Opus 5에 max를 지정한다. 국내·글로벌·가상자산 연구의 모델도 Astra로
맞췄다. 비활성 역할·도구 권한·구독 계정은 별도 변경하지 않는다.

이전 운영 릴리스 `f49d7ea1f3c1cdc76107ee59496c171e21e9f0d3`은 director 모델만 Astra로
지정했고 effort 전달 코드는 없었다. 이전 실행을 max였다고 소급 주장하지 않는다.

## 검증 상태

- 실제 PostgreSQL을 사용하는 전체 검사 **385 passed, 1 skipped**. skip은 별도 opt-in 실제
  Codex 호출 검사이며, 아래 운영 Slack 검증과 구별한다. 기존 라이브러리 경고 2개가 있었다.
- `ruff check .` 통과. Codex CLI 0.154.0은 빈 인증·입력으로 Astra max와 Terra high 설정을
  수용했다. 실행 argv와 변경된 effort의 동일 ID 재사용 거부도 검사했다.
- [GitHub CI](https://github.com/JJongAchii/quant-ai-company/actions/runs/35309160256) 통과.
- 운영 릴리스 **`6fc05d7d6807dd87d664ea5f26711531b37ce6d7`**. 배포 전에 기존 상태를 S3에
  백업하고 모든 진행 중 모델 호출이 없음을 확인했다. DB 컨테이너를 재생성하지 않았고
  8개 서비스의 실행 상태·건강 상태·코드 revision을 검사했다.
- 운영 roles 파일에는 model·effort만 갱신해 기존 역할 활성화·권한·지침을 보존했다.
  복구용 runtime.env와 roles.json을 서버에 별도 보존했다.

근거: [로컬 검증](evidence/staff-model-policy-local-20260918.json),
[서버 배포](evidence/staff-model-policy-deployment-20260918.json).

모델/effort 설정을 확인한 것과 직원 전문성·전략 수익성을 입증한 것은 별개다. 과거 요청은
당시 설정 그대로 보존한다. 새 max 결과로 과거 평가를 합산하거나 소급 인증하지 않는다.

## 실제 Slack 검증과 운영 정보 보완

[실제 답변](https://achiisquantresearch.slack.com/archives/C0C2B9EUEGM/p1789707961609579)은
Astra max 요청 2회로 완료했다. 두 CLI 영수증의 요청 설정과 DB 요청, Slack 게시, 최종 사용자
태그, artifact의 출처 연결을 확인했다.
[검증 영수증](evidence/staff-model-policy-live-20260918.json)에 기록했다.

최초 답변은 개선 BOT의 실제 호출 설정을 총괄의 runtime context에서 확인할 수 없다고 정확히
한정했다. 호출 코드는 Sol max를 전달했지만 배경 서비스 정보가 그 context에 빠져 있었다.
후속 보완은 `background_model_requests`에 engineer 설정에서 읽은 개선 BOT의 모델·effort와
독립 검토의 모델·effort를 공개한다. 데몬 건강·활성화 상태나 과거 요청의 설정을 소급 주장하지
않으며, reviewer effort도 실제 요청과 같은 상수를 참조한다.

후속 최종 코드 `e869eb5`의 [CI](https://github.com/JJongAchii/quant-ai-company/actions/runs/35309836027)는
PostgreSQL·Temporal·배포 계약을 포함해 **409 passed, 2 skipped, 1 deselected**였다. 로컬의
배경 설정 관련 29개 검사도 통과했다. 중간 import 형식 실패와 수정은
[후속 검증 기록](evidence/staff-model-context-local-20260918.json)에 보존했다.

최종 운영 릴리스는 **`e869eb5f608254d42da6159ff2932c1af8c2f451`**이며 8개 서비스 정상·DB 보존을
확인했다. [후속 배포 영수증](evidence/staff-model-context-deployment-20260918.json)과
[운영 runtime context](evidence/staff-model-context-runtime-20260918.json)에 실제 배경 BOT 설정을
기록했다. 최초 Astra max의 실제 2회 호출 검증과 이 후속 정보 노출 검증은 서로 다른 기록이다.

[후속 실제 총괄 답변](https://achiisquantresearch.slack.com/archives/C0C2B9EUEGM/p1789708860757539)도
Astra max 1회로 완료했다. 총괄이 새 runtime context에서 개선 BOT Sol max와 독립 설명검토
Opus 5 max를 읽어 이전의 확인 불가 상태가 해소됐다고 답했다. CLI 요청·완료 영수증·실제
Slack 본문·사용자 태그를 [후속 실제 검증](evidence/staff-model-context-live-20260918.json)으로
확인했다. 최초 2회와 합해 이번 정책 검증의 실제 Astra max 호출은 총 3회다.
