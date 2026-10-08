# 검색어 요청 무응답 복구 — 2026-10-08

소유자의 `실시간 검색어` 요청은 11:07:25 KST 정상 수신됐고 실제 공식 구독 모델의 편집도
11:09:44 끝났다. 11:09:59 생성된 outbox는 발송 시도 없이 대기했다.
공통 발송 대기열 앞의 오래된 기술 피드 115건이 발송 간격 때문에 보류될 때마다
기존 dispatcher가 해당 tick을 끝내 다음 메시지로 진행하지 못했다. 오래된 행들이
다시 발송 가능해지면서 새 답변이 계속 뒤로 밀렸고 요청의 15분 유효 시간이 지났다.
접수 권한·앱 연결·모델 오류나 소유자 pause가 원인은 아니었다.

만료된 요청은 stale로 처리하고 재발송하지 않았다. 원래 스레드에 연결한 별도 안정 ID의
운영자 복구 요청으로 최신 자료를 다시 수집·편집해 11:39:39 실제 Slack에 **10개**를 발송했다.
이 단계는 정상 정책/유효 시간/발송 전 검사를 거친 대상 지정 복구이며 자동 대기열 검증과 구분한다.
이미 전달한 메시지와 uncertain 효과를 자동 재시도하지 않았다.

수정은 `SlackOutbox.claim()`이 보류·거절된 후보를 최대 32개까지 건너뛰게 한다.
각 판단은 독립 PostgreSQL 트랜잭션으로 확정한다. Quant 정정 알림 다음에 소유자의
수시 검색어 답변을 우선하며, 기존 피드 간격·권한·revision·유효 시간 검사는 유지한다.
한 번에 다량의 피드를 보내는 변경은 없다.

원래 실제 운영 소스로 두 새 회귀의 실패를 재현했다. 수정 후 관련 회귀 **391 통과·1 skip**,
실제 parent에 수정한 후보 **135 통과**, lint 통과다. 실제 PostgreSQL을 사용했으며 외부 자료·
모델·Slack HTTP는 fixture다. 이 검사는 실제 운영 발송과 구분한다.

최종 소스 `7039e77`의 [전체 원격 CI](https://github.com/JJongAchii/quant-ai-company/actions/runs/37720210265)는
실제 PostgreSQL·Temporal 서비스 회귀 **1,642 통과·47 skip·1 deselect**, 무인증 Codex
프로토콜 **9 통과**, lint 통과다. 이후 커밋은 운영 증거·문서·qws 상태만 추가한다.
이번 수정은 원격 PR126에 게시하며 main 병합은 아직 수행하지 않았다.

11:54:28 KST dispatcher 한 개만 전환했다. 실제 parent의 226개 소스 파일 중 `slack.py`만
변경하고 나머지 225개와 기존 브리핑 구현을 보존했다. 환경·마운트·비밀 접근자·네트워크·
자원 제한·다른 컨테이너·기존 전달 본문/영수증·소유자 pause를 대조했다.
정기 슬롯은 **08:00·14:00·20:00 KST**, 스포츠 제외와 회당 10개 목표가 유지된다.

전환 후 원래 스레드로 운영자 자격검증 요청 한 건을 정상 `TrendFeedStore.request()`에 넣었다.
새 Slack SDK 이벤트로 가장하지 않았으며 dispatcher의 claim이나 발송을 수동 실행하지 않았다.
첫 완료 응답은 `trend_unknown_member`로 거절됐고, 기존에 허용한 한 차례 재편집은
12:01:36 KST 완료됐다. 12:01:48 원래 스레드에 **10개**가 자동 전달됐다(발송 시도 1회,
편집 완료 후 약 12초). 운영자 요청부터는 약 394초로, 재편집 때문에 수집·편집 지연이 있었다.
확정된 첫 응답을 검증 뒤 거절한 것이며, uncertain 호출을 새 ID로 자동 재실행한 것은 아니다.
발송 receipt와 Slack permalink를 실제 API로 확인했다.

- [진단](evidence/trend-outbox-20261008/diagnosis.json)
- [회귀](evidence/trend-outbox-20261008/pytest.txt), [실제 parent 회귀](evidence/trend-outbox-20261008/native-pytest.txt), [실패 재현](evidence/trend-outbox-20261008/baseline-pytest.txt), [lint](evidence/trend-outbox-20261008/ruff.txt)
- [이미지](evidence/trend-outbox-20261008/image.json), [전환](evidence/trend-outbox-20261008/activation.json), [실제 Slack 발송](evidence/trend-outbox-20261008/live.json)
- [최종 운영 상태](evidence/trend-outbox-20261008/active-probe.json), [전체 CI](evidence/trend-outbox-20261008/ci.json), [CI 요약](evidence/trend-outbox-20261008/ci.txt)
- [수정 PR126](https://github.com/JJongAchii/quant-ai-company/pull/126)

원래 기능과 PR119 병합 기록은 [정기·수시 발송 보고서](TREND-FEED-RECURRING.md)에 보존한다.
