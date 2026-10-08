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

## 실제 새 Slack 사례 — 2026-10-07 완료

소유자의 [새 메시지](https://achiisquantresearch.slack.com/archives/C0C3Q7BFQCE/p1791340131991029?thread_ts=1791269678.293859&cid=C0C3Q7BFQCE)가 11:28:52 KST에 허용된 Maintainer ingress로 접수됐다. 원 event key·payload digest·task ID는 [접수 영수증](evidence/maintainer-evidence-budget-20261007/fresh-case-intake.json)에 보존했다.

실행 전 설치된 공용 워커가 새 `trend_scout` 역할을 전문 절차로 읽으면서 `Unknown specialist`를 반복했다. 원 turn `e2988906-53c9-532b-acad-4afa31c0ca98`에는 request·response가 없었고 attempts=0이었다. main에 존재하는 역할 제외·pack guard를 정확한 설치 `company.py`에만 적용했다. 실제 PostgreSQL의 동일 요청 준비는 base에서 실패, 후보에서 ready(35,779자, gpt-6.1-sol/xhigh)였고 모든 준비용 SQL은 롤백됐다. 검증 모델 호출은 0회다.

13:09:19 KST에 워커 하나를 후보 이미지 `sha256:cab5db642ea0be53e9a3dc71e42f7498459eae65d7329a753162cdcf3b16e92c`로 교체했다. 코드 SHA는 `7f03288f548f68d8cf8162bcf9fed5ccb5ef181dbc50a61b478bd93704a3d8ca`다. 설정·권한·mount·network·자원 한도와 다른 14개 서비스, 원 불확실 검토 11건과 역사 maintenance 호출을 보존했다. [실제 준비 검증](evidence/maintainer-evidence-budget-20261007/worker-compat-qualification.json)과 [교체 영수증·rollback](evidence/maintainer-evidence-budget-20261007/worker-compat-cutover.json)을 따른다. 새 요청이나 모델 결과를 만들어 넣지 않고 원 Temporal 실행이 이어졌다.

### 결과와 실제 사용량

- 진단 요청 `49144860-7e42-5a72-8311-d47871ccf315`, Slack 케이스 `80b8f971-78db-5d6e-b938-7ded89dc5aca`.
- 13:45:16 KST에 `done`, finding 없음, error 없음으로 종료됐다. 원 영수증 대사와 누락된 원 CLI 출력의 한계를 읽었지만, 추가로 재현할 수 있는 결함·수정 후보를 확정하지 못했다. 전 시스템의 무결함 판정이나 자동 repair/test/PR 성공으로 사용하지 않는다.
- 동일 snapshot `388f44d5897129314232d86ba9230a863bf658a4`에서 추가 코드 조사 4회, 발췌 기록 38개를 받았다. diagnostic_revision=0으로 끝났다. [추가 조사 기록](evidence/maintainer-evidence-budget-20261007/fresh-case-investigation.json)의 반복 경로는 후속 입력 개선의 관찰 근거다.
- 최종 상태 메시지는 13:45:22 KST에 [실제 Maintainer 케이스 스레드](https://achiisquantresearch.slack.com/archives/C0C3Q7BFQCE/p1791346259340899)에 delivered됐다. 원 스레드의 Maintainer 접수 답변도 delivered다. 별도의 Director 링크 안내 한 건은 `not_in_channel`로 blocked이며 delivered로 계산하지 않는다.
- 아래 수치는 모두 실제 공식 Codex usage다. 접수와 진단 모두 `gpt-6.1-sol`이며 과거 Astra 두 턴은 포함하지 않는다.

| 범위 | 호출 | input | cached input | output |
|---|---:|---:|---:|---:|
| Maintainer 대화 접수 | 2 | 41,124 | 0 | 2,754 |
| 실제 진단 | 5 | 183,508 | 0 | 16,739 |
| 합계 | 7 | **224,632** | **0** | **19,493** |

cached input은 input에 포함되고 reasoning output은 output에 포함된다. 새 진단의 평균 input은 36,701.6 tokens/call이다. 전 관찰창 24회(input 882,903, cached input 60,800, output 76,847)는 사례·모델 구성이 달라 대조군이 아니다. 이번에는 cache 재사용이 확인되지 않았다. **실제 토큰 절감률과 구독 한도 소모 비율은 이 자료로 확정할 수 없다.** [완료·전달 원 영수증](evidence/maintainer-evidence-budget-20261007/fresh-case-final.json)과 [사용량 집계](evidence/maintainer-evidence-budget-20261007/fresh-case-usage.json)를 따른다.

진단에 제공된 운영 메타데이터의 commit `46d853cb`, GitHub snapshot, 실제 설치 overlay 모듈 SHA는 서로 다른 provenance다. 모델 응답의 commit 언급을 전체 서비스의 설치 버전 인증으로 사용하지 않는다.

새 실제 사례 인수는 근거 있는 무후보 종료와 usage·Slack delivered 영수증으로 완료했다. [PR #118](https://github.com/JJongAchii/quant-ai-company/pull/118)은 운영자가 작성한 최적화·운영 기록 PR이며 개선봇이 생성한 수정 후보 PR이 아니다. 기존 `29f7e8b` head의 service/codex-protocol은 모두 SUCCESS였다. 이 후속 기록의 CI와 병합은 별도 상태다.

## 증거

- [main 병합](evidence/maintainer-evidence-budget-20261007/main-integration.json)
- [원 요청·영수증 대사](evidence/maintainer-evidence-budget-20261007/reconciliation.json)
- [실제 입력 준비 비교](evidence/maintainer-evidence-budget-20261007/qualification.json)
- [모듈 manifest](evidence/maintainer-evidence-budget-20261007/overlay-manifest.json)
- [검증 기록](evidence/maintainer-evidence-budget-20261007/validation.json)
- [운영 대사 검증 등록](evidence/maintainer-evidence-budget-20261007/verification.json)
- [배포 후 실제 상태·호출](evidence/maintainer-evidence-budget-20261007/live-after.json)
