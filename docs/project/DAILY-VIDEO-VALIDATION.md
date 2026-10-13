# Daily brief 영상 경로 구현 검증 · 2026-10-06

검토된 AM 발송본 → 글 중심 대본과 별도 검토 → Runway 기존 웹 계정 음성 →
1080p 렌더링과 자막 → YouTube 비공개 업로드 → 기존 Slack 스레드에서 공개·수정·보류
경로를 구현했다. **현재 서버 활성화와 실제 외부 게시를 완료한 상태는 아니다.**
코드는 이 회사의 독립 worktree에서 작성했으며 기존 Analyst·storage 작업과 연구 저장소를 변경하지 않았다.

## 확인한 범위

| 항목 | 결과 | 실행 범위 |
| --- | --- | --- |
| 최종 전체 회귀 검사 | 1,885 passed / 49 skipped / 0 failed | 실제 로컬 PostgreSQL·Temporal 포함 |
| 최종 영상 기능 검사 | 61 passed / 0 skipped / 0 failed | 아래 protocol simulation 및 로컬 렌더링 |
| Ruff | 통과 | 저장소 전체 |
| wheel build·schema 포함 | 통과 | optional video 코드와 SQL 패키징 |
| Compose 설정 확장 | 통과 | 읽기 전용 CLI; image build/서버 배포는 수행하지 않음 |
| 한국어 디자인 샘플 | 1920×1080, 약 27.42초 | 로컬 macOS Yuna 음성·실제 Chromium·FFmpeg·Whisper small |
| 실제 Runway 과금·YouTube 업로드·Slack 게시 | 미실행 | 새 비용이나 외부 게시 없음 |

샘플은 합성 문안으로 만든 세 장면이다. 실제 음성의 자막 9개, 장면/챕터 시점,
프레임·오디오 길이, 전체 decode, 검은 화면, 장시간 무음, 음량 검사를 통과했다.
실측 integrated loudness는 -16.01 LUFS, true peak는 -4.49 dBFS다.
영상은 `.local/daily-video-demo/video.mp4`, 썸네일은 같은 디렉터리의 `thumbnail.png`에 있다.
화면과 썸네일은 시각적으로 확인했다. 이 샘플은 Runway 음성의 채널 적합성이나
실제 금융 문안의 숫자·발음·내용 품질 승인을 대신하지 않는다.

## 검증한 동작

- Full AM source·검토 항목·명시적 비축소 품질만 수용하고 preview/PM/축소/대체/미검토/늦은 자료를 거부한다.
  brief DB 커밋과 같은 트랜잭션에 하나의 stable video job을 만든다.
- 원본·주장 ID·검증된 기존 숫자 계산을 동결한다. 모델은 tool-free typed plan/review만 반환한다.
  숫자 근거와 별도 semantic review를 확인하고 회사의 모델 호출 제한을 공유한다.
- 크레딧은 제출 전에 예약하고 동일 회차의 수정본까지 합산한다. 모의 concurrent 요청,
  월/회차 한도, 회차 전체 예산 사전 점검과 응답 유실 후 재요청 차단을 확인했다.
- Temporal worker 재시작과 history replay가 이미 기록된 모델 요청을 반복하지 않는 것을 확인했다.
  여기서 모델은 fixture이며 실제 구독 Codex의 영상 계약 호출은 아직 실행하지 않았다.
- 실제 렌더링의 파일 hash 변경, 무음/검은 영상의 기술 검사 실패를 확인했다.
  렌더링 시도별 디렉터리를 써서 중단된 시도가 승인한 파일을 덮어쓰지 않게 했다.
- YouTube protocol은 MockTransport로 검증했다. private insert, 기존 session offset 조회·resume,
  만료 session의 새 insert 금지, 다른 channel/visibility 거부, 처리 완료 후 검토, 공개 직전 마감을 확인했다.
- Slack signed event 경로와 모의 Outbox로 owner/channel/app/message receipt/version/hash 바인딩,
  위조·만료·이전 버전 거절, 중복 action 수신, 누락된 전송 영수증의 uncertain 처리를 확인했다.
  실제 Slack에 영상 메시지를 게시하지 않았다.
- 08:30 지연 메시지는 회차당 한 번 생성하며 09:00 이후 새 효과·일반 승인을 중단한다.
  알려진 원래 receipt의 수동 reconciliation은 외부 쓰기 없이 감사 기록을 남긴다.

첫 전체 회귀 검사에서는 새로운 플래그가 없는 기존 dispatcher fixture의 호환 오류가 한 건 발생했다.
비활성 기본값으로 처리하도록 수정한 후 해당 검사와 최종 전체 검사를 통과했다.
최신 PyAV 19가 speech decoder의 인자를 지원하지 않아 실제 샘플이 실패한 이력도 있다.
optional video의 PyAV를 `<19`로 고정했고 lock의 18.1.0으로 실제 샘플과 렌더링 검사를 통과했다.

## 남은 운영 연결

기존 Analyst의 발송 없는 관찰과 publication 플래그는 유지했다. Full brief의 실제 발송 자격,
서버 Runway OAuth/명시적 workspace와 잔액, YouTube Desktop OAuth/해당 채널 ID,
API 프로젝트의 공개 허용 여부, 서버 memory/disk 여유를 확인해야 한다.
media worker image를 아직 실제 Linux 서버에서 빌드·설치하지 않았다.
최초 다운로드와 5~8분 영상의 08:30 준비 시간도 실제 서버에서 측정해야 한다.
실제 Runway 첫 비공개 샘플의 전체 청취·시청으로 voice, 숫자·단위·고유명사, 화면 속도를 확정한 뒤 활성화한다.

Runway MCP는 웹 계정 credits를 사용하는 공식 endpoint를 쓴다. 새 paid API key·구매·자동 충전·
이미지/영상 생성 의존성은 추가하지 않았다. 1,500 credits 월 한도는 추가 원화 지출을 자동 승인하지 않는다.
새 server 구매나 기존 company service 배포 변경도 수행하지 않았다.

[실행·로그인·복구 문서](../daily-video.md), [daily video 별도 제작 기준](../DAILY_VIDEO_PRODUCTION_STANDARD.md),
[기계 판독 검증 요약](evidence/daily-video-20261006/validation.json),
[실제 렌더링 요약](evidence/daily-video-20261006/render-demo.json)을 참고한다.
