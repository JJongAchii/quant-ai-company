# Analyst `#daily-brief` 설치와 미리보기 인수 상태 — 2026-09-28

후속 내용 평가는 [Analyst 내용 자격 재검증](ANALYST-QUALITY-FOLLOWUP-20260928.md)을
참조한다. 09/23 저장 원문의 수리 후 개발 판정은 통과했지만 신규 날짜의 원정책 평가와
실제 5거래일·서버 배포·Slack 발송 자격은 아직 통과하지 않았다.

`Analyst` 앱은 실제 채널이 있는 **Achii's Quant Research** 워크스페이스에 설치됐고
`#daily-brief`에 추가됐다. 새 Lightsail 서버에는 전용 자격 증명이 저장됐지만, 현재 실행 중인
릴리스에는 브리핑 역할과 실행 코드가 없으며 예약 미리보기와 게시가 모두 꺼져 있다.
새 아침판의 실제 모델 내용 검토도 미통과다. 따라서 이 단계에서 브리핑 메시지를 보내거나
정시 발간이 검증됐다고 표시하지 않는다.

처음 앱 생성 화면에서 워크스페이스 선택을 잘못해 `test`에 `Demo App`을 만들었다.
그 앱은 **설치되지 않았고 OAuth 권한도 없다**. 이를 브리핑 앱으로 사용하지 않았다.
올바른 워크스페이스에 새 `Analyst` 앱을 만들고 전용 채널에 추가했다. 앱 ID는
`A0C4NUZKQBV`, 팀 ID는 `T0C1YRDRPNF`, 기존 채널 ID는 `C0C2E59B1AA`,
봇 사용자 ID는 `U0C4PJDR8G3`이다. Socket Mode와 필요한 bot events·여섯 bot scope를
설정하고 해당 워크스페이스 설치를 확인했다. Slack의 채널 내 앱 목록과 추가 시스템 메시지로
멤버십을 확인했다. **Analyst가 브리핑 본문을 보낸 적은 없다.** 자격 증명은 서버의 root 소유
비밀 저장소에만 기록했고 Git 또는 이 문서에는 넣지 않았다.

[비밀값 없는 호스트 점검 기록](evidence/briefing-preview-host-20260928.json)에 따르면 현재 릴리스
`ccdcc43c2a0985dcc035262f33d19e0ae979f0a1`에는 `market_brief` 역할이 없다.
`BRIEFING_ENABLED=false`, `BRIEFING_PUBLISH_ENABLED=false`이며 채널·담당자 설정은 비어 있다.
기존 채널 허용 목록에도 `#daily-brief`가 없다. 계정 소유자는 허용 목록에 있다.
전용 자격 증명을 등록한 것만으로 Socket Mode 연결이나 발간이 시작되지는 않는다.

기존 자격 분리 worker에서 `qdata.api`로 KRX 핵심 지수 원천을 제한된 날짜 범위로 읽었다.
`krx_index` 객체는 읽기 전후 식별자가 같았고 KOSPI·KOSDAQ의 최신 행은 모두 9월 23일이다.
9월 28일 장 마감 전의 직전 한국 거래일도 9월 23일로 계산된다. 이 점검은 원천에 접근할 수
있다는 증거이며 9월 28일 마감값 게시, 미국 핵심 지수의 독립 확인, 실제 브리핑 데이터 생성
성공을 입증하지 않는다. 레이크 내부 파일에 직접 접근하거나 원천을 수정하지 않았다.

## 새 아침판 내용 평가

9월 25일 07:10 KST 기준의 이전에 사용하지 않은 미국장 아침판을 원문을 열기 전에 고정했다.
[사전 기준·해시](evidence/briefing-am-20260925/evaluation-policy.json)에 포맷 8, 검증 7,
최대 네 번의 작성·검토 호출과 성공 조건을 보존했다. 당시 수집 후보 151건에서 16건을
선택했고, 이전 사례와 같은 원문 ID는 세 건이었다. 실시간 레이크 스냅샷을 사후에 채워 넣지
않았고 실제 구독 모델로 작성→검토→한 차례 보완→최종 검토를 실행했다.

[고정 최종 판정](evidence/briefing-am-20260925/final-frozen/assessment.json)은 **미통과**다.
검토자는 숫자·출처·시점·인과·반대 근거·가독성 등 10개 항목을 통과로 보았지만
`coverage`와 `depth`를 실패로 판정하고 `reduce`를 요구했다. 연준 관련 정책금리·기조 물가와
직전 인상, 윌리엄스 발언이 빠졌고, 미중 휴전의 기존 만료일도 비교 기준에서 누락됐다.
첫 누락은 정확한 영문 `3.75%-4%`를 음수 `-4%`로 잘못 읽은 숫자 검증기 때문에
연준 항목 전체가 제외된 영향이 크다. 검증기를 수리하고 버전을 8로 올렸으며 실제 음수는
계속 구분하는 회귀 검사를 추가했다. 저장된 응답을 새 검증기로 재현한
[개발 판정](evidence/briefing-am-20260925/final-development-replay/assessment.json)에는
기계 거절이 없고 이슈 다섯 건이 남는다. 다만 기존 검토 응답을 재사용한 결과이므로
**새 독립 검토 통과로 세지 않는다**. 휴전 비교 누락도 남는다.

성공한 네 모델 호출의 경과 시간은 작성 434.933초, 검토 109.998초, 보완 436.717초,
최종 검토 107.272초로 합계 **1,088.920초(약 18분 9초)**다. 최종 검토의 첫 시도는
busy 응답으로 확정되지 않아 영수증을 보존하고 같은 요청 ID로 재시도했다. 이 시간에는
원문 수집·큐 대기·Slack 전송이 빠져 있다. 예정 시각까지 20분인 아침 편집 창 안에
매일 안정적으로 끝난다는 증거가 아니다. 상세 원문·요청·응답·초안·검토 결과는
[사례 폴더](evidence/briefing-am-20260925/)에 보존한다.

## 릴리스와 다음 실행 조건

브리핑 후보는 최신 `origin/main`과 서버가 실행 중인 housing 릴리스 코드를 모두 병합했고,
충돌 세 곳에서 브리핑·housing 경로를 함께 보존했다. 서버 릴리스의
`deploy/maintenance_release.py:validate_tree`를 그대로 로드해 코드 후보
`2647c7e67561aa9b4ed8fde2427e538100ff79a9`와 비교한
[실제 계약 검사](evidence/briefing-preview-release-gate-20260928.json)는
`release_protected_file_changed`로 거부했다. 두 트리 사이에는 467개 파일 차이가 있고
`cli.py`, `config.py`, `db.py`, Compose·환경 템플릿·의존성 파일 등 보호된 변경이 있다.
`market_brief` 역할도 새로 추가되므로 기존 유지보수 릴리스의 역할 병합만으로는
서버 설정에 자동 등록되지 않는다. 이 계약을 우회해 서버를 갱신하지 않았다.

후속 작업자가 같은 검사를 재실행할 수 있는 읽기 전용 도구는
[`scripts/audit_briefing_release_gate.py`](../../scripts/audit_briefing_release_gate.py)다.

```bash
python3 scripts/audit_briefing_release_gate.py \
  --previous ccdcc43c2a0985dcc035262f33d19e0ae979f0a1 \
  --candidate 2647c7e67561aa9b4ed8fde2427e538100ff79a9 \
  --output docs/project/evidence/briefing-preview-release-gate-20260928.json
```

검토된 **전체 코드 릴리스** 절차가 이 보호 파일·스키마·새 역할을 수용한 뒤에만 서버에
후보를 설치한다. 그때 기존 역할과 다른 앱 설정을 보존하면서 채널
`C0C2E59B1AA`, 소유자 `U0C250E23NW`를 허용 목록에 넣고
`BRIEFING_ENABLED=true`, `BRIEFING_PUBLISH_ENABLED=false`로 시작한다.
전용 일반 worker의 데이터 queue와 기존 뉴스 모델 queue가 실제 실행 중인지 확인하고,
`briefing probe`, `briefing data-probe`, `briefing status` 및 첫 예약 판의 PostgreSQL·Temporal
영수증을 남긴다. 이 설정은 현재 서버에 적용하지 않았다.

최근 5한국 거래일의 아침·저녁 10개 판을 현재 정책의 고정 입력과 실제 정시 실행으로
관찰하고, 핵심 지수의 독립 근거·검토 판정·지연·축약 이유를 확인해야 한다.
`briefing qualify`와 사람의 실제 내용 검토를 통과한 뒤 전용 앱의 본문·스레드·에코 무시·
수신 영수증을 실제 Slack에서 확인한다. 그 전에는 게시를 계속 끈다.

통합 후보에서 전체 회귀는 **1,364개 통과, 38개 건너뜀**이고 Ruff·`uv lock --check`가
통과했다. 별도 브리핑·housing Temporal 검사는 실제 로컬 PostgreSQL·Temporal로
**4개 통과**했다. Slack 전달 검사는 아직 실제 전송이 아니며, 구독 모델 판정은 위
고정 아침판 하나의 실패와 개발 재현만을 뜻한다.
