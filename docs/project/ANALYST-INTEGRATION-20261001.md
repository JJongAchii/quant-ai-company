# Analyst 통합 후보와 새 오전판 검증

2026-10-01. 승인된 INTENT-v16의 Analyst 내용 검증과 게시를 끈 운영 준비를 이어 진행한다.
Quant Scout나 전략 연구를 변경하는 작업이 아니다.

## 기존 운영 기능을 보존한 후보

9월 30일 12:21 UTC의 [실제 호스트 조회](evidence/briefing-analyst-integration-20260930/runtime-observation.json)에서
운영 링크는 `5defb8cb`, 일반 연구 worker는 `0286ba3e`, news-worker는 `837ac72a`였다.
API·dispatch 등의 실행 버전도 별도로 기록했다. 링크를 모든 실행 이미지의 버전으로 취급하지 않는다.

`5defb8cb`를 Analyst 브랜치에 병합한 첫 통합 후보는 `c3f8070fd02eaf3f51619a8418e6cfa9240804d5`다.
원문·숫자 처리 후보는 `bbabee0e3516e0d514b9b3d6d4ba95f1aaa01c6f`이며, 실제 검토에서 발견한
관측 시점 표현까지 보완한 현재 실행 후보는 **`14e603eef5d7a0b71178e173a60747238e97321d`**다.
[소스 보존·미리보기 제안](evidence/briefing-analyst-integration-20260930/source-preservation-and-preview-manifest.json)에서
서버 기준 삭제 파일은 **0개**이고 기존 역할의 모든 필드는 동일하다. 연구·Quant Feed·Housing·
Data Watch·모델 provider·maintenance 소스도 서버 기준과 동일하다. 추가된 역할은 비활성 `market_brief`이며
기존 역할의 도구나 권한을 넓히지 않는다. qdata 코드 커밋과 원본 archive 식별자는 유지된다.
선언된 공개 읽기 함수 목록에는 브리핑에 필요한 네 함수가 추가되며, 이는 새 데이터 코드 설치가 아니다.
[현재 후보 manifest](evidence/briefing-analyst-integration-20260930/source-preservation-and-preview-manifest-v41.json)도
같은 보존 결과이며 파일별 해시와 선택할 프로세스를 기록한다.
이전 manifest의 qdata 보존 boolean은 공개 함수 목록까지 포함한 전체 JSON을 비교해 `false`였다.
[비교 대상 정정](evidence/briefing-analyst-integration-20260930/qdata-manifest-identity-clarification.json)과
현재 manifest는 코드·archive 식별자 필드가 모두 동일하고 공개 함수 선언만 추가됐음을 구분한다.
이전 기록을 덮어쓰지 않는다.

브리핑의 시장 데이터 활동이 공용 연구 worker에 등록되는 구성만으로는 현재 연구 프로세스를
보존하면서 미리보기를 시작할 수 없다. `quant-company briefing-data-worker`와 opt-in Compose
프로필을 추가했다. 별도 `-brief-data` Temporal queue를 사용하며 모델·Slack 자격 증명이나 모델
네트워크를 주지 않는다. 기존 lake 읽기 자격만 사용한다. 새 프로세스는 공용·연구 worker를
시작하지 않는다. 기존 일반 worker의 호환 등록은 유지된다.

## 확인한 검사

통합 후 전체 검사 **1,533 통과, 46 건너뜀, 실패 0**과 lint·lock 검사를 통과했다.
[전체 검사 기록](evidence/briefing-analyst-integration-20260930/regression-committed.json)을 남겼다.
별도 실제 PostgreSQL·Temporal 검사 **5개**는 저장·편집·timer 복구·재생·전용 데이터 프로세스까지
확인했다. 모델과 데이터 입력은 fixture이고 Slack은 연결하지 않았다. cloud/live 건너뜀을
실제 운영 검증으로 세지 않는다.

현재 검증 40 실행 소스의 [전체 검사](evidence/briefing-analyst-integration-20260930/regression-v40.json)는
**1,544 통과, 46 건너뜀, 실패 0**이며 관련 내용·숫자 검사 173개도 통과했다. 실제 PostgreSQL·
local Temporal을 사용하지만 데이터·모델은 fixture다. 모델 품질은 별도의 실제 응답으로 평가한다.
검증 40의 [실제 CI](https://github.com/JJongAchii/quant-ai-company/actions/runs/36812275665/job/110209729060)도
통과했다. 관측 시점 지침을 추가한 검증 41은 동일한 본문·검토·원문·입력 한도의
[관련 검사 173개](evidence/briefing-analyst-integration-20260930/observation-time-v41.json)와 lint를 통과했다.
최종 검증 41 후보의 [CI도 통과했다](https://github.com/JJongAchii/quant-ai-company/actions/runs/36816592685/job/110222878434).
검증 40 전체 검사와 검증 41 후속 검사를 합쳐 새로운 전체 검사 수로 표시하지 않는다.

첫 전체 검사는 병합 소스가 미커밋이라 기존 연구 mission 검사의 `committed code` 조건에서
중단됐다. [이 실패](evidence/briefing-analyst-integration-20260930/regression-uncommitted-failure.json)와
XML을 보존하고, 통합을 커밋한 뒤 테스트 조건을 바꾸지 않고 전체 검사를 다시 통과했다.
이 기록은 연구 결과나 연구 자격 심사가 아니다.

## 크기가 제한된 배포 자료와 설치 조건

저장소 전체 약 **172MB** 중 평가 근거가 약 **156MB**라 기존 32/64MiB 설치 archive 한도를
넘는다. 근거는 회사 저장소에 보존한다. 읽기 전용 준비 도구
`scripts/audit_briefing_release_gate.py --runtime-archive`는 확정 커밋의 실행 소스·배포 정의·의존성
lock·Slack 앱 정의만 추출하고, 파일별 SHA-256·전체 archive 해시·크기를 기록한다.
미커밋 코드가 섞이지 않고 symlink는 거절하며 결과를 덮어쓰지 않는다.

[설치 검사·archive 기록](evidence/briefing-analyst-integration-20260930/release-gate-and-artifact.json)의
실행 파일 269개는 **13,499,291 bytes**, 압축 **11,330,852 bytes**다. 실행 소스의 코드·입력
변경은 42개이며 모든 이전 파일을 보존했다. 이 자료는 전체 Git checkout을 대신해
자동 유지보수 정책을 통과시키는 도구가 아니다.

검증 40 후보의 [검사·archive](evidence/briefing-analyst-integration-20260930/release-gate-and-artifact-v40.json)는
실행 파일 **13,503,295 bytes**, 압축 **11,332,025 bytes**이며 SHA-256은
`d21ea00369264e052c1de31d4fec2088a1ab421dd71ee1f15b02a13b1c56c2b1`다.
실제로 설치된 검사기는 이 후보도 보호된 파일 변경으로 거절했다.

현재 검증 41 후보의 [검사·archive](evidence/briefing-analyst-integration-20260930/release-gate-and-artifact-v41.json)는
실행 파일 **269개·13,503,674 bytes**, 압축 **11,332,111 bytes**이며 SHA-256은
`f282d940ec8248198377b004d85d9520f2dc2f55d2896484328185383fe05406`다.
이 확정 커밋과 해시를 보호된 미리보기 검토의 대상으로 사용한다.

실제로 설치된 `deploy/maintenance_release.py:validate_tree`는 최신 통합 후보도
**`release_protected_file_changed`**로 거절했다. 기존의 파일 삭제 문제는 해결됐지만 CLI·설정·
DB 등록·의존성·새 역할·Compose를 추가하는 작업은 자동 유지보수 범위 밖이다.
설치된 정책 자체를 고치지 않았으며 실제 서버 설치나 환경 변경은 수행하지 않았다.

검토된 운영자 설치에서 선택할 대상은 `api`, `news-worker`, `dispatch`와 새 데이터 프로세스다.
공용 연구 worker·기존 모델 runtimes·다른 전용 collectors·PostgreSQL은 보존해야 한다.
선택한 활동이 안전하게 끝난 시점, 최신 버전·설정·이미지·자원 재조회, archive와 각 소스 해시,
복구 journal·backup, 기존 역할·허용 채널 보존이 실제 적용의 선행 조건이다.
이 최소 미리보기 단계는 이전 generic worker/socket을 보존하므로 브리핑 후속 대화나 실제 Slack
전달 자격을 입증하지 않는다. 게시 설정은 계속 `false`로 유지한다.

10월 1일 03:30 UTC의 [새 실제 조회](evidence/briefing-analyst-integration-20260930/runtime-observation-20261001.json)에서도
운영 링크·API는 `5defb8cb`, news-worker는 `837ac72a`였다. 설정·역할 파일의 해시는 이전 조회와
같았고 브리핑·게시가 모두 꺼져 있다. 채널 설정과 역할 등록도 아직 없다. 가용 메모리 약
1,938 MiB와 디스크 여유 약 8.3GB를 기록했다. 이 값으로 장시간 안정성이나 적용 성공을 주장하지 않는다.

## 실제 Slack 접근

[자원·인증 조회](evidence/briefing-analyst-integration-20260930/host-admission.json)에서 Analyst는
예상 워크스페이스 `T0C1YRDRPNF`, 봇 사용자 `U0C4PJDR8G3`로 인증됐다. 소유자는 기존 허용
목록에 있지만 브리핑 채널은 아직 허용 목록에 없다. `conversations.info`는 `missing_scope`였다.
권한을 늘리거나 앱을 재설치하지 않았다.

기존 `channels:history` 범위로 [실제 읽기 접근](evidence/briefing-analyst-integration-20260930/slack-read-access.json)을
확인했다. 채널 `C0C2E59B1AA`의 기록을 읽었으며 본문 내용은 근거에 저장하지 않았다.
읽기 접근과 워크스페이스 인증을 확인한 것이고 채널 정보의 `is_member` 검증이나 브리핑 발송
영수증은 아니다. 메시지·권한·설정 변경은 하지 않았다.

## 사전에 고정한 10월 1일 오전판

[새 평가 정책](evidence/briefing-analyst-integration-20260930/fresh-am-20261001-policy.json)은
자료 마감 전에 고정했다. 미국 9월 30일 장을 다루고, 자료 마감은 10월 1일 07:00 KST,
예정 발간은 07:45 KST다. 이 평가는 실제 예정 시각의 운영 실행이나 전달을 검증하지 않는다.
정책은 형식 15·검증 38, 최대 다섯 완료 모델 호출과 한 차례 수정만 허용한다.

원문 제목·내용 조회는 마감이 지난 뒤 수행했다. 조회 원문 171개 중 계약에 맞는 168개에서
후보 96개를 고정했다. 과거 평가 후보와 겹치는 ID는 43개이며 53개는 새 ID다.
마감 전에 저장된 등록 원문만 사용하며 현재 검색이나 현재 lake 값으로 과거 시점의 데이터를
채우지 않는다. 사전 시장 데이터 snapshot이 없음을 명시한다.

자료 선정의 첫 호출은 추론 전에 `invalid_request`로 거절됐다. 통합으로 생긴 기본
`output_contract=agent_decision`·`session=null`을 별도 평가 helper가 원시 JSON에 포함했기
때문이다. 운영 `RuntimeClient`에는 이미 이 기본값을 제외하는 호환 코드가 있다.
평가 helper의 전송 형식만 운영 클라이언트와 일치시키고 같은 요청 파일·ID·모델·prompt로
재시도했다. [변경 기록](evidence/briefing-analyst-integration-20260930/am-20261001-v38/transport-amendment.json)에
정책·시장 입력·의미 기준을 바꾸지 않았고 최초 추론이 실행되지 않았음을 명시했다.
실제 새 내용 검토가 끝나기 전에는 새 날짜의 품질 통과나 전문가 수준을 주장하지 않는다.

## 새 원문에서 확인한 입력·검증 문제

실제 자료 선정은 원문 16개·본문 30,149자를 선택했다. 초안 작성은 공식 구독 Codex에서
완료됐다. 선정된 본문을 모두 읽고 [첫 검토 전 독립 대조](evidence/briefing-analyst-integration-20260930/am-20261001-v38/independent-main-audit-before-review.json)를
남겼다. 핵심 이슈 다섯 가지와 경제적 전달 경로·반대 근거가 보였지만, 원문 한 개가
6,235자 중 6,000자만 제공됐다. 전체 원문 확인을 통과한 사례로 세지 않는다.
마감 이후 원문 끝부분을 가져와 이 사례에 넣지 않는다.

잘림은 모델의 요약 단계보다 앞선 세 곳에 있었다. `fetch_original`, PostgreSQL 저장,
Analyst `document`가 모두 6,000자로 제한했다. 실제 기사 끝부분의 조건·반대 근거가 사라질
수 있는 경로다. 검증 39의 수정은 기존 문서 계약 한도인 **12,000자**까지 본문을 보존하고,
그보다 긴 기사나 알려진 부분 원문은 Analyst 입력에서 제외하며 진단을 기록한다.
Reporter의 모델 입력은 기존 6,000자 범위와 잘림 표시를 유지한다. 회사 DB에 저장된 원문과
Reporter가 읽는 요약 범위를 구분한다. 기존 과거 원문을 소급 수정하지 않는다.

초안의 기계 검사에는 서로 다른 원인 세 개가 있었다. 미국장 overview의 출처 ID 오자는
계속 거절한다. 반면 영문 방송 문장의 `six-point-11 points, or zero-point-72 percent`와
과거 합의를 가리키는 `from June`은 원문에 지원되는 값인데 기존 검증기가 인정하지 않았다.
검증 39는 정확히 적힌 소수·인접 상승/하락 술어·단위와 명시된 월만 해석한다. 반대 부호,
다른 단위·값·다른 지수 절과 원문에 없는 일자는 계속 거절한다.
[기술 진단](evidence/briefing-analyst-integration-20260930/am-20261001-v38/numeric-false-rejection-diagnostic.json)은
과거 출력 두 항목의 검증 동작을 비교한 것이며 새 모델 실행이나 새 품질 통과가 아니다.

작성·검토 지침에는 투자·재정 발표 금액이 새 자금인지 기존 약속의 배분인지, 수정 전망인지
본문에서 설명하도록 추가했다. 단순히 두 금액을 나열해서는 위험 노출이 얼마나 바뀌었는지
독자가 알기 어렵다. 서로 다른 경제적 경로의 뉴스는 공통 단어만으로 한 이슈에 묶지 않는다.

[관련 검사](evidence/briefing-analyst-integration-20260930/prospective-v39-repair.json)는 실제 PostgreSQL
저장부터 Analyst 소비까지 긴 본문 끝부분을 보존하고, DB와 저장된 브리핑 입력의 부분 원문을 제외하며,
영문 숫자와 월 표현의 양성·음성 사례를 확인한다. 169개 관련 검사와 월 표현 후속 검사 3개가
통과했다. 초안 사례는 사전에 고정한 **검증 38** 코드로 계속 평가한다. 수정된 검증 39로
같은 사례를 다시 평가해 새 날짜의 통과로 표시하지 않는다.

## 원문에 지원되는 내용이 더 빠지던 이유

사전에 고정한 오전판의 첫 검토는 12개 기준 중 8개만 통과했다. 허용된 한 차례 보완은 같은
마감 이전 후보에서 요청된 원문 세 개를 더 읽어 총 19개·39,942자 안에서 진행했다.
숫자·기간 검사에서 여섯 항목이 탈락해 금리·메모리 이슈와 고용 일정 등이 빠졌다.
[오전판 실패 기록](evidence/briefing-analyst-integration-20260930/am-20261001-v38/frozen-case-outcome.json)은
검증 38의 입력·기준과 실제 네 번의 완료 응답을 보존한다. 부분 원문도 남아 있어 전체 원문
기준을 충족하지 못한다. 실패 이후 같은 기준의 추가 작성·수정은 수행하지 않는다.

탈락 원문을 다시 대조한 검증 40 수정은 `4bp`와 `0.04%포인트`의 정확한 등가, hyphen으로
적힌 영문 회계 분기, 명시된 경제지표 발표 대상 월, 관세 법률의 `301조`를 처리한다.
법률 조항은 조원 규모의 금액과 구분하고 조항 번호 자체는 비교한다. 잘못된 값·기간·단위는
통과시키지 않는다. 특히 `8:30`의 숫자 8이 `8월 고용보고서`를 지원하지 못하도록 별도의 월
대조를 추가했다. 이 음성 검사의 [첫 실패](evidence/briefing-analyst-integration-20260930/numeric-semantic-v40.xml)를
보존하고 조건을 바꾸지 않은 채 코드 수정 후 [173개 검사](evidence/briefing-analyst-integration-20260930/numeric-semantic-v40-repaired.xml)를
통과했다.

[같은 저장 출력의 기술 진단](evidence/briefing-analyst-integration-20260930/am-20261001-v38/development-v40/numeric-diagnostic.json)은
본문·출처·모델 응답을 바꾸지 않고 기계 탈락이 6개에서 0개로 줄어, 여섯 이슈와 고용 일정이
모두 표시됨을 확인했다. 숫자 매칭이 의미 정확성을 보장하지는 않는다. 기존 투자 약속과 새
발표의 관계, 다른 경제적 경로의 뉴스 묶음과 빽빽한 문단은 독립 내용 검토에 남긴다.
새 작성 호출을 추가하지 않고 개발용 내용 검토 최대 한 회만 허용해, 원래 사례 네 회와
합쳐 완료 모델 호출은 다섯 회를 넘지 않는다. 이 검토는 새 시장 날짜·새 품질 통과로 세지 않는다.

## 실제 개발 검토 결과와 마지막 보완

공식 구독 Codex의 [실제 개발 검토 결과](evidence/briefing-analyst-integration-20260930/am-20261001-v38/development-v40/outcome.json)는
**9/12·reduce·통과 아님**이다. 수치·출처·인과·반대 근거·전달 경로·대안·반증 조건·깊이·
가독성은 통과했고, 시점·중요한 맥락·포괄성은 실패했다. 수정 모델 응답을 손대지 않은 기술
진단에서 기계 탈락 0을 확인한 뒤, 의미 검토에서 `macro_interpretation` 하나를 다시 제외했다.

원문의 ‘발표 이후’를 ‘발표 직후’로 바꾼 표현은 더 좁은 관측 시점을 단정한다. 검증 41의
작성·검토 계약에 관측 시점의 정확도를 보존하고, 사후 보도를 즉각 반응이나 종가로 바꾸지
말라는 지침을 추가했다. 금액 배분 관계와 서로 다른 경제적 경로의 이슈 구분은 검증 39부터
보완한 작성 지침이다. 이번 검토 대상은 그 지침으로 새로 작성한 결과가 아니라 **기존 검증 38
수정 응답**이다. 새로운 지침의 효과는 다음 새 자료 작성에서 확인해야 한다.

현재 원문에 있는 원전 투자 규모·새 관세 체계의 적용 범위·공급 우회의 비교 기준도 중요한
맥락으로 지적됐다. 제목만 본 미선정 원문 두 개는 추가 확인 요청으로 남겼으며 읽은 사실이나
새 시장 사실로 간주하지 않는다. 원래 사례 네 번과 개발 검토 한 번으로 호출 예산 다섯 번을
사용했고 추가 작성·검토·소급 자료 보완을 수행하지 않는다. 원문 한 개의 알려진 잘림도 남아
있으므로 전문가 품질·완전한 원문 검증·새 날짜의 통과를 주장하지 않는다.

## 다음 새 자료 평가

[10월 1일 오후판 검증 41 정책](evidence/briefing-analyst-integration-20260930/fresh-pm-20261001-v41-policy.json)은
자료·제목을 보기 전, 마감 전인 04:47 UTC에 고정했다. 실행하지 않은 검증 39·40의 정책을
장부에 보존하며 보완한 사유를 명시했다. 자료 마감 **19:30 KST**, 기존 예정 발간 **20:15 KST**,
같은 최대 다섯 호출·한 번의 수정·전체 열두 기준을 사용한다. 지금 예정 시각의 운영 검증이나
새 내용 통과가 이뤄졌다는 뜻은 아니다.

새 자료에는 완전한 제한 범위의 원문만 사용하고, 이미 본 전일 자료의 겹침을 manifest에
기록한다. 과거 잘린 본문이나 시장 데이터가 없으면 현재 자료로 소급 채우지 않는다.
실제 입력·내용 검증, 보호된 미리보기 배포, 최근 다섯 거래일의 시각·전달 확인과 사람의
내용 수용이 아직 남아 있다. 자동 발송은 계속 꺼져 있다.
