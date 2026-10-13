# 서버 요금제 변경 안내 (Lightsail 4GB → 8GB)

매일 영상 제작(아침·마감)을 서버에 올리기 전에 서버를 키우는 절차다. Lightsail은 사용 중인 인스턴스의 요금제를
제자리에서 바꿀 수 없으므로 **스냅숏으로 새 인스턴스를 만들고 고정 IP를 옮긴다.** 기본안은 8GB(월 $44,
2 vCPU·8GB·160GB·CPU 기준 30%)이고, 4GB(월 $24, 2 vCPU·4GB·80GB·기준 20%)를 고르면 아래에서 요금제와
설정 파일만 바꾼다(지금 서버가 이미 4GB이므로 4GB 선택은 이전이 필요 없다). 근거 숫자는 [영상 운영 문서](daily-video.md)와 2026-10-07 조사 보고를 따른다.

> 두 서버가 동시에 켜져 있으면 Slack·Temporal 작업을 두 번 처리할 수 있다. 새 서버를 켜기 전에 옛 서버의
> 서비스를 멈추고, 옛 서버는 마지막까지 **멈춘 상태로만** 보관한다.

## 0. 준비 (10분)

- 시간대: 브리핑·영상 시간(06:40~09:00, 16:40~22:00 KST)과 백업(03:10)을 피한다. 평일 10:00~16:00을 권한다.
- 최근 백업이 성공했는지 확인한다([운영 문서](deployment.md)의 백업·복구 절차).
- 비용: 8GB는 월 $44(지금 4GB 월 $24보다 $20 추가), 스냅숏 보관은 GB당 월 약 $0.05. 이전 기간에는 옛 서버와 새 서버 비용이 잠시 함께 든다.
- 맥에 SSH 키와 AWS 콘솔 로그인이 되어 있어야 한다.

## 1. 옛 서버 서비스 멈추기 (2분)

```sh
ssh ubuntu@<고정 IP>
cd /opt/quant-company/current
sudo docker compose --env-file deploy/.env -f deploy/compose.yaml stop
```

진행 중인 영상 작업이 있으면 끝날 때까지 기다린다(`quant-company video status`).

## 2. 스냅숏 만들기 (10~20분)

1. AWS 콘솔 → **Lightsail** → 리전 **서울(ap-northeast-2)** → 인스턴스 `quant-company-host`.
2. **스냅숏(Snapshots)** 탭 → **스냅숏 생성** → 이름 `quant-company-host-pre-8gb-YYYYMMDD`.
3. 상태가 "사용 가능"이 될 때까지 기다린다.
4. 인스턴스 상단의 **중지(Stop)** 를 눌러 옛 서버를 멈춘다. 삭제하지 않는다.

## 3. 새 인스턴스 만들기 (5~10분)

1. 방금 만든 스냅숏 오른쪽 메뉴 → **새 인스턴스 생성**.
2. 가용 영역: `ap-northeast-2a`(옛 서버와 같게).
3. 인스턴스 요금제: **Linux $44 (8GB)**. 4GB를 골랐다면 **$24 (4GB)**.
4. 이름: `quant-company-host-8gb`. 키 페어는 옛 서버와 같은 키.
5. 생성 후 **네트워킹** 탭의 방화벽을 옛 서버와 같게 맞춘다. 기본은 SSH(22)만 연다.
   HTTPS 프로필을 쓰지 않는 한 80·443은 열지 않는다([lightsail.json](../deploy/lightsail.json)).

## 4. 고정 IP 옮기기 (2분)

1. Lightsail → **네트워킹** → 고정 IP → 옛 서버에서 **분리(Detach)**.
2. 같은 고정 IP를 새 인스턴스 `quant-company-host-8gb`에 **연결(Attach)**.
3. 맥에서 `ssh ubuntu@<고정 IP>`가 새 서버로 들어가는지 확인한다.
   호스트 키 경고가 나오면 `ssh-keygen -R <고정 IP>` 뒤 다시 접속한다.

## 5. 디스크와 스왑 (5분)

```sh
df -h /          # 160GB(4GB 요금제면 80GB)로 늘었는지 확인. 보통 첫 부팅 때 자동으로 늘어난다.
lsblk            # 늘지 않았다면 디스크·파티션 이름을 확인한 뒤 growpart/resize2fs로 늘린다(이름을 확인하고 실행)
free -m
sudo fallocate -l 2G /swapfile && sudo chmod 600 /swapfile
sudo mkswap /swapfile && sudo swapon /swapfile
echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab
echo 'vm.swappiness=10' | sudo tee /etc/sysctl.d/99-quant-swap.conf && sudo sysctl --system
free -m          # Swap 2047 확인
```

스왑은 메모리 부족 순간을 넘기는 완충일 뿐 용량이 아니다. 메모리 한도는 다음 단계 설정을 따른다.

## 6. 배포 설정 (10분)

1. 메모리 한도: [8GB 설정 예시](../deploy/lightsail-8gb.env.example)(4GB면 [4GB 예시](../deploy/lightsail-4gb.env.example))의
   값을 `/var/lib/quant-company/config/runtime.env`에 반영한다. 기존 비밀 아닌 설정(채널, 소유자, 브리핑 등)은 지우지 않는다.
2. 영상 설정: [영상 설정 예제](../deploy/video.env.example)의 값을 추가한다. 처음에는 `VIDEO_ENABLED=false`로 둔다.
3. 영상 폴더(`prepare-state.py`는 만들지 않는다):

```sh
S=/var/lib/quant-company
sudo install -d -o 10001 -g 10001 -m 0750 $S/video $S/video-models $S/video-assets
sudo install -d -o 10001 -g 10001 -m 0700 $S/media-auth
```
4. 승인된 커밋으로 이미지를 빌드하고 서비스를 올린다([운영 문서](deployment.md) 6~7장, push·배포 승인 후).

```sh
cd /opt/quant-company/current
sudo docker compose --env-file deploy/.env -f deploy/compose.yaml up -d --wait
sudo docker compose --env-file deploy/.env -f deploy/compose.yaml -f deploy/video.compose.yaml \
  --profile video --profile claude build video-worker
```

## 7. 이미지 라이브러리 복사 (5분, 맥에서)

```sh
LIB="$HOME/Library/Mobile Documents/com~apple~CloudDocs/뭐든story/아침브리핑/library"
rsync -av --exclude '*.json' "$LIB/images" "$LIB/photos" ubuntu@<고정 IP>:/tmp/video-assets/
scp "$LIB/manifest.json" ubuntu@<고정 IP>:/tmp/video-assets/
ssh ubuntu@<고정 IP> 'sudo rsync -a /tmp/video-assets/ /var/lib/quant-company/video-assets/ && \
  sudo chown -R 10001:10001 /var/lib/quant-company/video-assets && rm -rf /tmp/video-assets'
```

`manifest.json`의 ID(일러스트 11장, 자료사진 3장)만 영상에 쓰인다. 새 그림을 추가하면 manifest도 함께 바꾼다.

## 8. 서버에서 렌더 시간 재기 (5분)

모델·음성·업로드 없이 프레임 캡처와 인코딩만 잰다. 맥에서 오늘 회차 화면 폴더를 보낸다.

```sh
# 맥에서
tar -C /Users/achii/orca/workspaces/quant-ai-company/video-claude/.local/brief_close -czf /tmp/bench.tgz template
scp /tmp/bench.tgz ubuntu@<고정 IP>:/tmp/
# 서버에서
sudo mkdir -p /var/lib/quant-company/video/bench && sudo tar -C /var/lib/quant-company/video/bench -xzf /tmp/bench.tgz
sudo chown -R 10001:10001 /var/lib/quant-company/video/bench
sudo docker compose --env-file deploy/.env -f deploy/compose.yaml -f deploy/video.compose.yaml --profile video --profile claude \
  run --rm --no-deps video-worker quant-company video bench --page /state/video/bench/template/index.html --seconds 60
# 다른 터미널에서 같은 시간에: sudo docker stats --no-stream (영상 작업자 메모리 최대치 기록)
```

결과의 `fps`와 `estimate_5min_episode_minutes`(5분 영상 렌더 예상 분)를 기록한다. 기준:
- 10 fps 이상(5분 영상 15분 이내): 공개 10분 전 파일 목표(아침 06:50·마감 17:50) 유지 가능.
- 6~10 fps: 공개 시각을 늦추거나(`VIDEO_AM_PUBLISH_DST` 등) 렌더 workers를 늘린다. 아침 새 생성은 09:00에 그대로 멈춘다.
- 6 fps 미만 또는 메모리 부족(OOM): 영상 기능을 켜지 말고 결과를 공유한다.

측정 뒤 `sudo rm -rf /var/lib/quant-company/video/bench`.

## 9. 확인 후 옛 서버 정리

- 새 서버에서 아침·마감 브리핑이 각각 한 번 이상 정상 발송되고, 영상 첫 비공개 샘플까지 확인되면 끝난 것이다.
- 옛 인스턴스는 **멈춘 상태로 7일** 보관한 뒤 삭제한다. 멈춘 동안에도 디스크 비용은 든다.
- 스냅숏은 30일 보관 후 삭제한다. 문제가 생기면 스냅숏에서 다시 만들 수 있다.

## 되돌리기

새 서버에 문제가 있으면 새 서버 서비스를 `stop`하고, 고정 IP를 옛 서버로 다시 연결한 뒤 옛 서버를 시작한다.
옛 서버가 멈춘 뒤에 새 서버에서 처리된 브리핑·영상 기록은 옛 DB에 없으므로, 되돌린 뒤 새 서버 DB 백업을 보관한다.
