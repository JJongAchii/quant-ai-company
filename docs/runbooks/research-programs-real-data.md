# 실제 국내 ETF·주식 연구 프로그램 인수

현재 준비 증거: [입력·자격검사 보고서](../project/RESEARCH-PROGRAMS-DATA-PREPARATION-20260928.md),
[전체 프로그램 초안](../project/evidence/research-programs-20260928/first-program-draft.json),
[교차 검증 영수증](../project/evidence/research-programs-20260928/preparation-validation.json).
상태는 준비 완료, **서버 미배포·프로그램 미승인·과학 실험 0회**다.

## 고정된 입력과 프로필

| 식별자 | 값 |
|---|---|
| qdata API commit | `d6d7d0ed066ec49541e9acdd657c9ec5692ffc52` |
| 소스 번들 commit | `422e5da2fbaf3682cfc04a5d8334dcc2f34d8a11` |
| 소스 번들 SHA-256 | `44fb48c8d4b299e66009a01e5b026a0d0b886cfa92bee25db9304607a1f09b49` |
| 3070 | `DESKTOP-5T00NAF`; 준비 경로 `/home/achii/quant-company-qualification/programs-20260928/real-data-b4b9304` |
| 준비된 워커 릴리스 | `/home/achii/quant-company-qualification/programs-20260928/release-candidates/b4b9304129c80aa45fe4f9eb5419dfed4362e6fd` |
| 현재 활성 워커 설정 SHA-256 | `e615f9ffcda1732cb69dd7e1cb2146f50300cb0098d63e3f859168d7e0c2006b` |
| 준비된 워커 설정 SHA-256 | `c0f8ee6186170ca496ee3e084b6b2ebeb8dcc90a8c5b5810baaf4602cb9e08e8` |

입력 JSON은 이 저장소에 없다. 로컬 `.local/science-inputs/{kr_etf,kr_stock}-prepared-v2/`와
3070 격리 경로 `inputs/`에 있고, 각 SHA는 초안과 입력 영수증에 고정됐다. 원본
`data-receipt-*.json`을 확인한 후에만 새 경로에 복사한다. qdata가 바뀌면 같은 파일이라고
가정하지 않고 새 목적지와 새 receipt를 만든다. 준비 명령은
`uv run python -m quant_company.research.domestic_snapshot --help`에 있다. 읽기 전용
`QDATA_LAKE`가 `--lake-uri`와 일치하고, qdata checkout이 표의 clean commit인지 검사한다.

**서버 소스 프로필에는 위 `422e5da…`/`44fb48c8…` 조합만 쓴다.** 3070에서
`domestic_profile`을 재실행해 생긴 `e46025a…`/`70c1f5d…` 조합은 같은 코드라도
프로그램 초안의 `base_commit`과 달라 `profile_for`가 거절한다. 3070에 배치한
`canonical-baseline-422e.bundle`의 SHA를 확인하고, 서버 컨테이너의 읽기 전용
경로에 같은 바이트를 배치한 뒤 `server-profile.json`의 `source_bundle`만 해당 절대
경로로 설정한다. [공개 프로필 사본](../project/evidence/research-programs-20260928/real-profile-public.json)의
코드·런타임·입력 계약 digest를 대조한다.

## 활성화 전 대조

1. 실제 Lightsail 회사 호스트의 서버 commit, DB migration, release registry, 진행 중
   job, 설정 백업을 관측한다. 현재 확인된 것은 인스턴스 이름
   `quant-company-host-4gb-20260928`뿐이다. 배포 시점에 서버와 워커의 company
   commit·프로필을 맞추고 이전 릴리스를 보존한다.
2. 초안의 `research:6a64df0e-9c60-555f-aa3a-a80a70e7f985`가 실제 운영 프로젝트에서
   같은 소유자에게 승인된 출처인지 확인한다. `include_quant_feed=true`로 추가되는
   원문은 독립 검토 상태와 인용 위치를 확인한다. 해당 출처가 없으면 새 유효한 출처
   ID를 고정해 초안 digest를 다시 만들고 자격검사 identity를 다시 대조한다.
3. 네 envelope, 전체 24 trial·36,000초·6 mission, 시장별 비용·위험 제한과 개발/봉인
   기간을 완전한 JSON으로 검토한다. 원문 수치 재현은 실제 논문과 같은 추정량·목표값이
   생긴 뒤 별도 envelope로 고정한다. 주식 거래 불가 행 194개는 유지한다. 이 행을
   매매·보유해야 하는 후보는 과학 평가에서 기술적으로 거절될 수 있다.
4. [기본 연구 배포 절차](research-programs.md)의 실제 DB/Temporal·3070·Slack 인수를
   거친다. 워커 준비 구간 검사 4회와 로컬 회귀 테스트는 완료됐지만 운영 서버나
   실제 Slack 승인/발송을 확인한 것은 아니다.

`docs/work/lamprey/INTENT-v2.json`의 결정 단계는 **정확한 배포본과 첫 과학 프로그램을
활성화 전에 검토**하도록 정한다. 위 관측과 전체 명세를 채운 뒤 그 결정을 받는다.
승인된 배포는 [기본 절차](research-programs.md)의 백업·롤백·대사 순서대로 수행한다.
불확실한 실행이나 발송은 자동 재시도하지 않고 기존 영수증으로 대조한다.
