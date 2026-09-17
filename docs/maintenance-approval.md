# Slack에서 개선안 반영하기

개선 담당이 PR 링크를 올린 **같은 스레드**에서 `반영해`라고 답한다.
`반영해줘`, `반영해 주세요`, `적용해줘`, `승인`도 사용할 수 있다.
여러 PR을 함께 검토 중이면 `PR #3 반영해`처럼 번호를 적는다.

1. 승인 접수 메시지에 PR 번호와 승인한 커밋이 표시된다.
2. 다음 개선 담당 진행 주기(최대 약 5분 + GitHub 검사 시간)에 병합을 진행한다.
3. 문서 변경은 병합으로 완료한다. 실행 코드·직원 지침 변경은 상시 서버의 별도 실행기가
   이어받아 빌드·백업·배포·상태 확인을 수행한다. 보통 추가 수 분이 필요하며 실제 완료 알림을 확인한다.
4. 완료 또는 실패·복구 결과가 같은 Slack 스레드에 온다.

승인은 해당 PR의 특정 커밋에만 적용된다. 이후 다른 개선안을 자동 승인하지 않는다.
다른 스레드나 승인한 뒤 변경된 PR은 같은 승인으로 적용하지 않는다.
실행 코드의 기준 브랜치가 바뀌면 검사를 다시 준비해야 한다. 질문·부정문은 승인으로 처리하지 않는다.

## 운영자 설치

아래 서비스는 기존 호스트에만 설치한다. 새 서버·요금제·API가 필요하지 않다.
먼저 승인된 구현 release를 배포하고 maintenance가 additive DB migration을 마친 것을 확인한다.

```sh
sudo install -m 0644 /opt/quant-company/current/deploy/quant-company-release.service /etc/systemd/system/
sudo install -m 0644 /opt/quant-company/current/deploy/quant-company-release.timer /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now quant-company-release.timer
```

배포 영수증은 회사 DB와 `/var/lib/quant-company/releases/<application-id>.json`에 남는다.
같은 디렉터리의 이전 env/roles 파일은 root 전용이며 Git·Slack에 올리지 않는다.
실패 시 `journalctl -u quant-company-release.service`와 해당 application 오류 코드를 확인한다.
불확정 병합을 자동으로 다시 시도하지 않는다. 이전 서비스 복구에 실패하면 host journal을
보존하고 운영자가 복구한다. 이미 병합된 GitHub 커밋을 서버 rollback이 되돌리지는 않는다.

[설계 및 제한](adr/0016-slack-approved-maintenance-application.md)
