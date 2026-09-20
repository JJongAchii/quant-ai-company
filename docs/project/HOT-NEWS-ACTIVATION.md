# Reporter 운영 활성화

2026-09-19 KST. Reporter를 기존 서버와 실제 `hot-news`에 연결해 상시 수집·검토·게시를 켰다.
사용자의 “이어서 진행해” 요청에 따른 운영 활성화이며, 이전 [구현 검증](HOT-NEWS-VALIDATION.md)과 구분한다.

앱은 `A0C2V1Q5CCE`, bot user는 `U0C2FLSUUMV`, workspace는 `T0C1YRDRPNF`, 채널은
`C0C2J1SSX09`다. 기존 직원들의 모델·추론 설정과 자격 증명, PostgreSQL 컨테이너를 보존했다.
기존 Lightsail 서버를 사용하며 유료 뉴스 계약이나 서버 증설은 하지 않았다.

## 실제 결과

- [ECB 소비자 기대인플레이션 뉴스](https://achiisquantresearch.slack.com/archives/C0C2J1SSX09/p1789749361591839)와
  [영국의 시리아 지원 발표](https://achiisquantresearch.slack.com/archives/C0C2J1SSX09/p1789749363261359)가
  실제 Reporter 계정으로 게시됐다. 각 게시물의 Slack ID와 성공 영수증을 대조했다.
- 첫 자동 검토는 6건 중 2건 게시, 2건 제외, 2건 보류였다. 개별 금융 제재는 중요도 때문에 제외하고,
  상대국에 관한 영국 정부의 주장만 있는 사안은 추가 근거가 없어 보류했다.
- ECB 스레드에서 저장된 원문을 `read_source`로 읽고 수치·원문 링크로 답했다. 질문은
  **Codex 운영자가 실제 소유자 Slack UI를 통해 작성한 연결 점검 입력**이다. 실제 Socket Mode
  수신과 구독 모델 호출이며, 사람이 직접 작성한 평가나 만족도 검증으로 해석하지 않는다.
- 모델 작업과 발송이 끝난 뒤 worker·dispatcher를 재시작했다. 두 Temporal workflow의 ID와 run ID,
  뉴스·답변 4개 메시지의 ID·Slack 시각·발송 시도 1회가 그대로 유지됐다.
  이는 관찰한 재시작 검사이며, 전송 도중 장애를 포함한 exactly-once 보장은 아니다.

근거: [실제 Slack](evidence/hot-news-live-slack.json), [원문 읽기와 답변](evidence/hot-news-followup.json),
[재시작](evidence/hot-news-restart.json), [운영 상태](evidence/hot-news-operating-health.json).

## 운영 설정과 확인 범위

배포 커밋은 `7528d05d4f3d9dca655ced4622f9c367d0900a5c`다. PR #36으로 main에 병합됐으며
병합 커밋 `ac3bcaf3d3f0e223264e77fcae421ccd86c8e50c`와 파일 트리가 동일하다.
[정확한 배포 커밋 CI](https://github.com/JJongAchii/quant-ai-company/actions/runs/35368634252)는
461 passed, 2 skipped, 1 live deselected다. CI는 실제 PostgreSQL·Temporal, 모의 모델·Slack 검사다.
실제 Codex·Slack의 증거는 별도 영수증에 기록했다.

뉴스 활성화·발송 설정은 모두 true, 피드별 수집 간격은 10분, 편집 확인 간격은 5분이다.
첫 시작의 lookback은 1,440분으로 지정했고 게시 신선도는 24시간이다. 모델 지연·사용자 업무·
원문 장애에 따라 게시가 늦어질 수 있다. 정해진 시각의 브리핑이나 뉴스 개수 채우기는 하지 않는다.

운영자가 먼저 실제 collector로 초기 자료를 수집했고, 이후 실제 Temporal workflow가 편집·발송을
수행했다. 초기 120개 기사 버전 중 오래됐거나 발행 시각을 확인할 수 없는 113개는 게시 대상에서
제외됐다. [초기 수집](evidence/hot-news-initial-collection.json),
[첫 자동 검토](evidence/hot-news-live-status-before-restart.json)를 함께 읽는다.
재시작 이후 01:44 KST에 자동 수집기가 6개 피드를 다시 조회했다. 모두 성공했고 5개는 HTTP 304였다.
두 번째 자동 편집은 남은 지역 금융시설 소식을 제외했으며 기존 뉴스 2건을 재게시하지 않았다.
[다음 수집·편집 주기](evidence/hot-news-continuity.json).

별도의 일회용 PostgreSQL DB에서는 실제 ECB 원문을 Codex 구독 모델로 미리보기 검토했다.
운영과 같은 24시간 신선도를 적용했고 outbox는 0개였다. 영수증 보존 후 임시 DB를 삭제했다.
[서버 미리보기](evidence/hot-news-server-codex-preview.json).

출처는 연준·ECB·BEA·영국 FCDO/재무부/통상부의 **공식 피드 6개**다.
영국 부처들은 하나의 정부 원천으로 취급하며 원문 최초 발행 시각과 OGL 출처 표시를 적용한다.
최종 이미지의 정상 entrypoint에서 6개 피드와 원문을 모두 확인했다.
[원문 검사](evidence/hot-news-final-image-probe.json), [소스 조건](../news.md).

일반 언론의 전 세계 사건 보도는 아직 포괄하지 않는다. 뉴시스 자동 수집 제한을 반영했고,
BBC·KBS는 이용 범위 확인 전까지 비활성이다. BLS는 실제 HTTP 403으로 제외했다.
장시간 가용성·누락률·편집 정확도, 뉴스 전용 운영 경보, 보관·삭제 자동화는 이번 인수로 입증하지 않았다.

## 배포 수리와 보안 정리

배포 전 파일 권한 오류와 빌드 디스크 부족을 수리했다. 첫 전환은 기존 운영 커밋의
`reasoning_effort` 설정을 main 코드가 읽지 못해 마이그레이션 전에 실패했고, 기존 8개 서비스로
복구했다. 정확한 운영 커밋 `e869eb5`를 통합하고 입력 크기 회귀도 수리한 뒤 다시 검증·배포했다.
신규 뉴스 발송은 성공한 전환과 실제 미리보기 검사 이후에만 켰다.
[복구 기록](evidence/hot-news-initial-cutover-rollback.json),
[통합 수리](evidence/hot-news-integration-repair.json), [최종 배포·백업](evidence/hot-news-final-release.json).
최종 상태에서 기존 backup·release timer는 모두 active이며 디스크 여유는 약 13GiB다.

Slack 관리 화면 검사 중 **구형 Verification Token**이 한 차례 도구 출력에 포함됐다.
운영 봇·Socket 토큰은 출력되지 않았고 해당 구형 값은 서비스에 설정돼 있지 않다.
화면 출력 마스킹을 보강했고, 사용자의 직접 재인증 후 **2026-09-21 08:56 KST에 교체를 확인했다.**
Slack의 새 토큰 활성화 확인을 거쳤으며, 페이지를 다시 연 뒤 기존 값과 다르고 활성화 대기가
없음을 확인했다. 비교는 브라우저 내부에서 수행했으며 값은 출력·기록하지 않았다.
[보안 정리 기록](evidence/hot-news-slack-credential-cleanup.json).

## 2026-09-21 미발송 문의 점검

사용자가 최초 게시 뒤 새 뉴스가 오지 않는다고 알려 실제 운영 DB·Temporal 이력·Slack을
08:51~08:56 KST에 확인했다. 배포 커밋은 그대로이며 수집·편집 workflow는 실행 중이다.
6개 출처는 08:45~08:46 KST에 조회에 성공했고, 원격 피드를 조건부 캐시 없이 다시 읽은 검사도
모두 성공했다. **피드 조회 성공은 신규 기사 유입이나 게시 성공을 뜻하지 않는다.**

- 실제 뉴스는 9월 19일 01:36 KST에 보낸 2건 그대로다. Slack의 메시지 ID와 DB의 발송 성공
  영수증이 일치하며, 추가 발송 대기·불명·실패 건은 없다. 새 테스트 메시지는 보내지 않았다.
- 최초 120개 기사 버전 뒤 새로 들어온 것은 영국의 유엔 평화유지 관련 연설 1건이다.
  9월 19일 06:13 KST에 수집돼 06:16 KST에 검토됐고, 새로운 자금·파병·임무 변경이 없는
  입장 표명으로 제외됐다. 그 뒤 새 검토 대상이 없어서 편집기는 `idle`을 반환하고 있다.
- 누적 121개 버전 중 113개는 오래됐거나 발행 시각을 확인할 수 없어 제외됐다.
  최신성 검사를 통과한 8개 중 2개 게시, 4개 중요도 제외, 2개 추가 근거 보류 후 만료다.
  article의 `queued` 2개는 이미 outbox에서 `delivered`된 기사다. 기사 상태만으로 발송 대기를
  판단하지 않는다.
- 현재 수집·편집 실행 이력의 activity 실패는 0건, 처리 중인 검토 0건, 일시 중지·사용자 업무에
  의한 대기·일일 한도 차단은 없다. 현재 Slack 인증·채널 읽기도 성공했다. 이번 점검은 읽기이며
  현재 시점의 새 메시지 발송 시험을 추가로 했다는 뜻은 아니다.

직접 원인은 좁은 공식 발표 목록에서 새 게시 대상이 들어오지 않은 것이다. 이는 전 세계에
중요한 뉴스가 없었다는 결론이 아니다. **현재 동작하는 공식 발표 수집기를 사용자가 요청한
폭넓은 주요 글로벌 뉴스 서비스의 완성으로 표현해서는 안 된다.** 일반 매체의 경제·국제 보도
수집과 확인 경로가 다음에 해결할 범위이며, 이번 상태 점검에서 매체 추가를 배포하지 않았다.
[현재 점검 영수증](evidence/hot-news-quiet-channel-check.json).
