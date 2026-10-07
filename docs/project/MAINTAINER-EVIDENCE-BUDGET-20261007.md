# 개선봇 진단 입력·재진단 최적화 — 2026-10-07

사용자 승인: “이어서 진행해”. PR #110 병합, 원 검토 영수증 대사, 개선봇 입력·재진단 최적화와 실제 사례 검증의 순서다.

## 완료한 범위

- [PR #110](https://github.com/JJongAchii/quant-ai-company/pull/110)은 00:03:32 UTC에 `64d449ed29cee9197032b101502bdc8094520c66`으로 main에 병합됐다.
- 네 모듈의 최적화 코드는 **10:42:46 KST에 실제 개선봇에 적용**됐다. 패치 커밋은 `db94a2d026aeb10d26e68570089d495de50c3dcc`, 이미지 ID는 `sha256:888c5bddf3b7ab537d176f259cb6607368d6cfd367514c36344c38dd42f57ed3`이다.
- 11:17 KST 운영 readback에서 코드 해시 일치, 정상 heartbeat, 읽기 순서 v2, 필요한 검토/provider 코드의 실제 조회 가능 상태를 확인했다. 재시작 0회, OOM 없음, 메모리 한도 256MiB다.
- 원 검토 11건과 기존 maintenance 호출 80건의 해시가 교체 전후에 유지됐다. 원 요청·응답·차단 상태와 직원 등급을 수정하거나 불확실 호출을 재실행하지 않았다.

## 변경한 내용

1. **저장소 읽기 순서**: 기존 2MB 한도가 운영 증거 파일에 먼저 쓰이면서 필요한 직원 검토와 provider 코드가 누락됐다. 실제 source·관련 테스트를 먼저 읽으며, 같은 Git 커밋의 기존 저장 기록을 보존한 채 필요한 코드 커버리지를 확장한다. blob SHA·모드·크기·secret 검사와 허용 저장소 경계는 유지된다.
2. **진단 입력**: 자동 기술 장애에는 해당 사례와 연결된 대화·직원 업무 지시문을 보낸다. 일반 직원 평가와 포괄적 사람 요청은 전체 이력을 유지한다. 경로 목록은 관련 경로를 포함한 32개까지 먼저 줄이고, 정확한 저장소 검색·추가 읽기를 계속 제공한다. 처음 보내는 source 발췌는 최대 24,000자다. 반복 시스템 설명을 줄이며, 전체 근거와 판정은 DB에 남는다. 생략한 자료는 부재의 증거로 쓰지 않는다.
3. **재진단 조건**: triage 중 코드·테스트·배포 코드·규약·실행 설정·역할·판정 및 실제 읽은 파일이 그대로인 운영 문서 변경은 원 커밋의 조사를 이어간다. 관련 변경은 기존의 재진단·원본 보존 절차를 따른다. patch·평가·PR 게시의 정확한 base 검사와 소유자 권한·후보 승인은 유지된다.

## 실제 입력 준비 비교

실제 운영 설정·PostgreSQL·GitHub 커밋 `b981ad7e282fb1ead9d6663172f83883478aa225`에서 같은 네 사례를 준비했다. **모델 호출 0회**, 준비용 SQL은 모두 롤백됐다. 기존 케이스를 재개하거나 새 호출로 만들지 않았다. 이전·후보 모두 현재 지정 `gpt-6.1-sol / xhigh`를 유지했다.

| 사례 | 이전 문자 수 | 최적화 문자 수 | 감소 |
|---|---:|---:|---:|
| `19aa1178` | 86,023 | 77,555 | 9.84% |
| `454ef5b7` | 86,844 | 83,761 | 3.55% |
| `6f3c54a0` | 87,149 | 85,592 | 1.79% |
| `a4aabd4e` | 87,987 | 87,717 | 0.31% |
| 합계 | 348,003 | 334,625 | **3.84%** |

이는 **문자 수 감소이며 실제 토큰 절감률이 아니다**. 기술 장애 세 건에는 기존에 없던 관련 검토·provider 코드 24,000자를 제공하면서 입력이 줄었다. 실패한 초기 준비 비교에서는 일부 입력이 늘었고, 경로 목록·시스템 설명·직원 문맥을 추가로 수리한 최종 후보만 적용했다. 문서 변경에 따른 반복 호출 감소는 회귀 검증을 통과했지만, 운영에서 절감한 호출 수는 아직 측정하지 않았다.

## 원본 불확실 검토 대사

11건은 과거 **Claude Opus 5 독립 설명 검토**다. Astra의 별도 연구 검증과 구분한다. DB 요청의 입력 fingerprint와 런타임이 기본값을 생략해 만든 digest는 형식이 다르다. 설치된 계약에 없는 `session=null`, `output_contract=agent_decision` 기본값만 정규화한 뒤 모두 원 요청과 런타임 영수증이 일치했다.

모든 원 영수증은 `failed / uncertain`이고 결과·raw CLI 출력이 없다. **실제 모델 완료와 사용량은 확정할 수 없다**. 재호출·성공 처리·직원 감점 없이 원본을 유지한다. 새로운 운영 검증 `operator-review-reconciliation-e648e8cea1e9706959dd37da14ffa1c7`에 이 제한과 원 영수증 해시를 등록해 후속 진단이 읽을 수 있게 했다.

## 검증과 운영 적용

- 초기 전체 서비스: 1,528 passed / 49 skipped.
- 최신 main 통합: 1,578 passed / 49 skipped.
- 최종 문맥 변경 후 실제 PostgreSQL 회귀: 73 passed.
- 최종 source `db94a2d`의 GitHub `service`와 `codex-protocol` CI는 모두 SUCCESS. lint·diff 검사 통과. 이후 운영 기록 추가에는 source 모듈 변경이 없다.
- 자동 테스트의 모델·Slack·GitHub 응답은 fixture다. 실제 로컬 PostgreSQL/Temporal, 실제 GitHub source 조회, 원 영수증 readback과 실제 컨테이너 교체는 각각 별도 증거로 기록한다.
- 설치된 유지보수 이미지의 네 모듈이 base와 정확히 일치하는 것을 확인하고 해당 모듈만 overlay했다. 전체 main 이미지 재배포라고 표현하지 않는다. 공식 인증·현재 모델·호출 한도·환경·UID·mount·network·보안 옵션·256MiB/0.25CPU와 다른 14개 서비스는 교체 전후에 유지됐다.
- 영속 Compose overlay와 rollback 지침이 [cutover 영수증](evidence/maintainer-evidence-budget-20261007/cutover.json)에 있다. 이후 host release는 이 패치를 보존하거나 대체 후보를 검증해야 한다.

## 실제 새 사례의 경계와 다음 행동

11:17 KST readback에서 실행 가능한 개선 건은 **0건**, 적용 후 새 모델 호출은 **0회**다. 기존 네 건은 done 1건, blocked 3건으로 유지됐다. 이전 no-finding은 당시 자료 한계에 따른 종료이며, 이번 변경의 실제 모델 인수 증거로 사용하지 않는다.

최적화 전 관찰창의 실제 24개 호출은 input **882,903**, cached input **60,800**(input에 포함), output **76,847**이다. [원본 사용량 기록](evidence/maintainer-live-cycle-20261006/usage-final.json)과 공식 [Codex JSON usage 문서](https://learn.chatgpt.com/docs/non-interactive-mode)를 따른다. 적용 후 새 호출이 없으므로 토큰 절감률·cache 개선·구독 사용 비율을 계산하지 않는다.

새 사례는 설정된 Slack의 사람 요청에서 시작해야 한다. `maintenance/requests.py`와 `maintenance/identity.py`가 실제 inbound task를 요구하고, AGENTS.md가 서명·워크스페이스·사용자·채널 경계를 요구한다. 이 대화 승인을 서명된 Slack event로 만들거나 기존 blocked 건을 초기화할 수 없다.

소유자는 [기존 개선봇 스레드](https://app.slack.com/archives/C0C3Q7BFQCE/p1791269678293859)에 다음 새 요청을 보낼 수 있다:

> 최적화된 현재 운영 코드와 등록된 원 영수증 대사 결과를 기준으로 새 진단을 진행해. 과거 불확실 호출은 재실행하지 말고, 추가로 재현 가능한 문제만 수정·검증·PR로 제안해. 근거가 부족하면 그 한계를 기록하고 종료해.

그 새 case의 공식 usage와 처리 결과로 실제 절감을 측정한다. 이번 attachment의 **새 모델 사례 인수는 이 외부 경계 때문에 partial**이다. 자동 repair/test/PR가 완료됐다고 보고하지 않는다. [PR #118](https://github.com/JJongAchii/quant-ai-company/pull/118)은 이 대화가 승인한 운영자가 작성한 최적화 PR이다.

## 증거

- [main 병합](evidence/maintainer-evidence-budget-20261007/main-integration.json)
- [원 요청·영수증 대사](evidence/maintainer-evidence-budget-20261007/reconciliation.json)
- [실제 입력 준비 비교](evidence/maintainer-evidence-budget-20261007/qualification.json)
- [모듈 manifest](evidence/maintainer-evidence-budget-20261007/overlay-manifest.json)
- [검증 기록](evidence/maintainer-evidence-budget-20261007/validation.json)
- [운영 대사 검증 등록](evidence/maintainer-evidence-budget-20261007/verification.json)
- [배포 후 실제 상태·호출](evidence/maintainer-evidence-budget-20261007/live-after.json)
