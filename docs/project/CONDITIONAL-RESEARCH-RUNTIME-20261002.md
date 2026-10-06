# 조건부 연구 런타임 운영 검증 — 2026-10-02

## 완료한 운영 인수

실제 RTX 3070 워커의 연결을 복구하고, 운영 서버와 워커를 검증된 회사 소스
`231d6ba0755f658f167636a8ea2c3ef65b6af562`로 전환했다. 서버의 API·dispatch·Slack socket·회사 worker가
실제로 불러오는 전체 회사 코드의 해시를 검증했다. 독립적으로 운영되는 나머지 서비스의 컨테이너와 이미지 식별자를 보존했다.

실제 워커의 네트워크가 차단된 bubblewrap에서 104거래일·1,040행의 warmup 입력을 검증했다.
원본 producer 결과를 보존하고 서비스의 엄격한 consumer 검증을 다시 통과했다.
이는 동결 입력의 전달·실행 환경 검증이다. GMM 후보의 성과 검증이나 직원의 데이터 적합성 판정은 아직 없다.
개발 가격 행을 sandbox에 마운트하지 않았고, 봉인 가격 읽기·모델 학습·성과 산출·새 과학 시행은 0건이다.

기존 입력·프로필·자료 패킷·서명된 프로그램을 유지하면서 새 조건부 프로필과 프로그램에 결합된 자료 패킷을 추가했다.
실제 PostgreSQL에서 읽은 이전 10개 과제와 시행 이력의 해시는
`50fc1f64e78541f0a867e070df20d5b179546b246377a8c30f302cdb28d22a8e`로 준비 당시 값과 일치했다.
실제 Temporal RPC와 인증된 3070 polling을 확인했고, 이번 전환의 runtime pause를 해제했다.

## 검증과 복구 기록

[최종 소스 CI](https://github.com/JJongAchii/quant-ai-company/actions/runs/36964093804)는
1,737 passed·47 skipped·1 live deselected다. PostgreSQL·Temporal 검증은 실제 서비스이고,
CI의 Slack 이벤트와 모델 응답은 fixture다. 실제 3070·bubblewrap·운영 서버 검증은 별도 증거로 남겼다.

전환 전 일관된 DB·설정·실행 영수증 백업을 만들고 S3 업로드 영수증과 체크섬을 보존했다.
백업 SHA-256은 `02864b904ce0c1c84f73293ecbac40bdce5bce62980e2eb0e648ecfc99d9fab7`이다.
자격 증명 파일과 비밀값은 Git에 저장하지 않았다.

경로 검사, 서비스 프로세스 확인, 새 자료 폴더 권한 및 이전 실행 경로를 물려받는 설정 문제를 실제 검증에서 발견했다.
실패와 이전 설정 복구를 보존하고 수정한 새 작업에서 확인했다.
완료 영수증이 있던 Quant Feed 호출 한 건은 입력·계정·응답 타입을 검증한 뒤 계정 호출 원장을 정리했다.
이 복구는 새 모델 호출이나 Quant Feed 게시를 실행하지 않았다.

## 남은 승인과 직원 판단

새 조건부 프로그램 해시는 `be0d940bf5904c417d213898617b3f6c0fed19d2bfddd78460ec315e92b216af`다.
고정 빈티지와 공개시각 가정에 조건부인 개발 연구이며, 과거 PIT·실제 체결·확증·운영 배치를 인증하지 않는다.
누적 한도는 4개 과학 시행·7,200초·1미션·동시 1개이고 첫 회차는 최대 2결과다.

2026-10-02 06:32 UTC 확인 시 기존 프로그램은 활성 상태였다.
승인된 Slack 연구센터 스레드에 이전 프로그램의 정확한 취소 명령을 전달했고, 실제 outbox의 `delivered` 기록을 확인했다.
INTENT-v8의 서명된 소유자 게이트에 따라 기존 프로그램 취소가 기록된 뒤 새 canonical 초안을 등록하고 별도 승인을 받아야 한다.
채팅 승인이나 이전 프로그램의 서명은 새 정책의 승인으로 사용하지 않는다.

새 프로그램 초안·새 소유자 승인·실제 data 직원의 적합성 판단·director의 과제 선정은 아직 완료하지 않았다.
직원이 `conditional_ready`나 과제 수락을 선택하도록 강제하지 않는다.

## 근거

- [운영 인수 영수증](evidence/conditional-runtime-20261002/runtime-intake-acceptance.json)
- [실제 서버 전환·이력·접속 검증](evidence/conditional-runtime-20261002/server-activation.json)
- [실제 3070 입력 검증](evidence/conditional-runtime-20261002/worker-qualification-receipt.json)
- [Slack 전달과 프로그램 상태](evidence/conditional-runtime-20261002/owner-review-state.json)
- [기술 실패와 복구](evidence/conditional-runtime-20261002/operational-repairs.json)

이 문서는 운영 검증을 기록한다. 연구 전략의 수익률·우수성·채택 또는 paper/live 배치를 판정하지 않는다.
