# AWS 배포 현황과 비용

확인일: 2026-09-16. 사용자의 최신 **2GB 생성·검증 승인**에 따라 자원을 생성하고 배포했다.
[승인 원문](AWS-CREATION-APPROVAL.json), [검증 영수증](AWS-DEPLOYMENT-VALIDATION.json).

## 실제 구성

| 항목 | 배포 결과 |
|---|---|
| AWS profile / 계정 | `default` / 끝자리 `2347` |
| 리전 / 가용 영역 | 서울 `ap-northeast-2` / `ap-northeast-2a` |
| Lightsail | `quant-company-host`, `small_3_0`, 2 vCPU·2GB, 월 **$12** |
| OS | Ubuntu 24.04, x86_64 |
| CloudFormation | `quant-company`, `CREATE_COMPLETE` |
| 고정 IPv4 | `quant-company-ip`, 서버에 부착 |
| 백업 | 전용 S3, 비공개·암호화·버전 관리·30일 lifecycle |
| Slack | 네 직원 앱, Socket Mode, 공개 HTTP/도메인 불필요 |
| 운영 접근 | 운영자 공인 IPv4 `/32`의 SSH, API는 `127.0.0.1:8000` |
| SSH 키 | 전용 RSA 4096 공개키 `quant-company-operator` 등록 |
| 백업 계정 | `quant-company-backup`, 회사 버킷의 `company/` 읽기·쓰기만 허용 |
| 기존 운영 자원 | EC2 콜렉터·Insight-Invest·기존 Lightsail 변경 없음 |

서비스 이미지는 커밋 `c7fbd62a4ee27e6f8fd8ab166804620a400f874b`로 빌드했다.
역할 설정은 별도 영속 파일이며 [현재 설정 영수증](evidence/aws-role-config.json)에 출처·digest를 기록한다.
Docker 기반 이미지와 PostgreSQL digest를 고정했고 컨테이너 image ID도 보존했다.

## 2GB에서 확인한 범위

- 서버의 공식 ChatGPT device login과 실제 Codex 호출, 같은 요청 ID의 저장 결과 재사용.
- 실제 역할별 모델로 네 직원·8회 모델 호출의 합성 협업, 직접 동료 위임·계산·출처 읽기.
- 약 4분/49개 표본에서 호스트 가용 메모리 **최소 1072.5 MiB**, Codex 컨테이너
  peak **177.6 MiB**, OOM kill **0**, swap **0**.
- 서버 전체 재부팅 후 기존 협업 기록 보존, 재부팅 전 예약한 업무가 같은 ID로 완료.
- 실제 사용자 Slack 요청, 네 직원별 앱의 응답 8건을 Slack API에서 대조.
- S3에 올린 백업을 별도 경로로 다시 내려받아 새 DB에 복원. 두 프로젝트의 전체 상태 일치.
- 일일 백업 timer 활성화: **03:10 KST + 최대 5분 지연**.

이 증거는 **첫 소규모 운영**에 관한 것이다. 장시간 부하·큰 자료·다중 추론·금융 전문성 시험,
전체 호스트 유실 후 재구축과 자동 장애 인계는 포함하지 않는다. 맥북 완전 종료 후 휴대폰 인수는
별도로 확인한다. 현재 증설 근거는 없으며 4GB로 올리지 않았다.

## 비용

- Lightsail 고정분 **월 $12**. 생성 시 서울 리전의 활성 bundle과 가격을 확인했다.
- S3 백업 **계획 월 $3~6**. 실제 저장량·요청량으로 청구하며 고정 이용료가 아니다.
- AWS 계획 합계 **월 $15~18**, 예산 환율 1,500원 기준 약 **2.25~2.7만원**.
- Temporal Developer는 월 기본료 $0, actions·저장량 종량제. 기존 작은 업무량 가정의
  예시 $3.73~9.66를 더하면 월 **$18.73~27.66**이고 청구 상한은 아니다.
  $150 체험 크레딧과 최초 API key는 2026-12-15 만료 전에 검토한다.
- Slack 유료 업그레이드, 새 모델 API, 추가 Codex 크레딧은 구매하지 않았다.
  기존 Codex 구독료·세금·카드 수수료·시장 자료·거래 인프라는 별도다.

[AWS 가격](https://aws.amazon.com/lightsail/pricing/),
[Temporal 과금 항목](https://docs.temporal.io/cloud/pricing).

회사의 동시 모델 작업은 1개, 일일 기본 한도는 100회다. 이는 자체 실행 제한이며 구독이
제공하는 사용량 보장은 아니다. 증설이 필요하면 실측 근거와 추가 비용을 먼저 제시한다.

## 운영·복구

호스트에서 `/opt/quant-company/current`는 exact commit 릴리스를 가리킨다.
영속 설정·DB·Codex 로그인·영수증은 `/var/lib/quant-company`에 있다. 비밀은 Git·이미지·
백업 묶음에 포함하지 않는다. 외부 백업 복원은 새 `restore_*` DB만 만들며 운영 DB를 덮어쓰지 않는다.
상세 절차: [운영 안내](../../../services/quant-company/docs/deployment.md).

스택 삭제 때 서버·고정 IP·S3 버킷은 보존된다. 사용 종료 시 이 자원까지 확인해 정리해야
청구가 끝난다. 단일 서버 장애·일일 백업의 잠깐 중단은 존재하며 이중화된 서비스는 아니다.
