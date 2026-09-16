# 개선 BOT 운영

정책: 수정·테스트·draft PR까지 자동, 운영 반영은 검토 후 진행.
[설계](adr/0013-review-controlled-maintenance.md)에 데이터 범위·예산·실패 처리·권한을 정의한다.

## 현재 상태

구현·검증용 코드와 비활성 Compose profile을 준비했다. GitHub App 설치·실제 구독 기반 수정 PR
인수·운영 서버 profile 활성화·추가 메모리 실측은 아직 하지 않았다. 현재 네 직원이 이 기능을
이미 사용한다고 보고해서는 안 된다. 기존 서버와 Slack 회사는 맥북 전원과 무관하게 운영 중이다.

## 검토 후 연결할 구성

1. 이 저장소에만 GitHub App을 설치한다. Contents/PR write와 Actions read만 부여한다.
   웹훅·도메인은 필요하지 않다. 비밀 key를 채팅·Git·모델에 보내지 않는다.
2. `deploy/maintenance.example.json`의 예제 App/installation ID와 허용 사용자 ID를 실제 값으로
   교체해 서버 `config/maintenance.json`에 둔다. key는 `secrets/maintenance_github_key`에 둔다.
   허용 사용자는 기존 Slack 허용 사용자 목록의 부분집합이어야 한다.
3. 검토한 정확한 서비스 커밋과 `deploy/qdata-source.json`의 qdata archive로 maintenance 이미지를
   빌드한다. 기본 운영 이미지와 별도 target이며 고정된 JWT 서명기용 OpenSSL만 추가한다.
4. `enabled: true`와 `--profile maintenance`는 운영 반영 단계에서 설정한다. 기본 profile은 꺼져
   있다. 운영자만 설정·활성화한다. 맥북에서 daemon을 실행할 필요는 없다.
5. 작은 합성 결함으로 원인 대화 → 제안 → 실제 CI → draft PR → Slack 알림을 확인한다. 실제
   구독 사용량·메모리를 측정하고, 첫 PR은 직접 검토한다. 검토한 뒤에도 배포는 별도 단계다.

명령:

```bash
quant-company maintenance --config /etc/quant-company/maintenance.json
quant-company maintenance-status
```

첫 명령은 명시된 서버 설정과 자격증명이 필요하다. 두 번째 명령은 최근 50개 case/triage의 상태·
안전한 오류 코드·CI/PR 영수증만 조회한다. `blocked`는 운영자가 해당 case와 GitHub 실제 상태를
확인할 대상이며, 자동으로 새 ID를 만들거나 실패한 PR을 재생성하지 않는다.

## 변경 범위와 한계

- 일반 회사 런타임 모듈·문서와 역할의 mission/instructions 수정 제안을 허용한다.
- 데이터 라이브러리·연구 규약·다른 저장소·배포·인증·CI·개선 BOT 자신의 변경은 거절한다.
- 기존 테스트를 고치거나 삭제하지 않는다. Python 수정에는 case별 새 회귀 검사를 붙인다.
- 테스트 성공은 수정의 타당성이나 금융 전문성을 보장하지 않는다. 문맥이 부족하면 blocked가
  적절한 결과다. 전체 서비스 구조의 자율 재작성이나 전략 실행까지 구현한 것이 아니다.
- PR에는 문제 요약·재현·기대 동작·원본 메시지 ID·base/head/digest·CI 링크가 들어간다.
  원문 대화 전체를 PR에 복제하지 않는다. 알려진 비밀 패턴은 차단하지만 임의의 개인정보나 모든
  비밀을 완벽히 탐지하는 필터라고 주장하지 않는다.
- 새로운 컨테이너의 기본 상한 128MiB는 설정값이다. 2GB 호스트에서의 실제 적합성은 profile
  활성화 인수로 측정해야 하며 현재 운영 실측과 섞지 않는다. 후보 코드의 테스트는 GitHub에서 한다.
- GitHub Actions는 계정 사용량에 합산된다. 추가 결제 한도나 요금제를 바꾸지 않는다.
