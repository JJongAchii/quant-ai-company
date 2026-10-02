# 조건부 연구 운영 인수 준비

2026-10-02 11:23 KST 운영 조회 기준. **입력·코드·서버 이미지 준비는 완료했고,
지정된 3070 워커의 연결 복구를 기다리고 있다. 운영 전환과 새 프로그램 승인은 아직 완료되지 않았다.**

## 준비와 검증

| 항목 | 확인 결과 |
|---|---|
| 통합 회사 소스 | `19807db8d1817a3a495ac10eee34b1bac2d287fe` |
| CI | 1,520 통과, 47 건너뜀, 실제 모델 검사 1개 제외, lint 통과 |
| 로컬 통합 검사 | 실제 임시 PostgreSQL·Temporal, 320 통과·1 건너뜀; Slack·모델 응답은 fixture |
| 새 입력 | 10개 ETF × 835거래일, 8,350행; 고정 qdata API와 가격·키 불일치 0 |
| 준비 / 개발 구간 | 104거래일·1,040행 / 731거래일·7,310행 |
| 새 프로필 | `kr-etf-retrospective-v1`, 실제 입력용 준비 완료; 실제 워커 자격검증·등록 대기 |
| 서버 이미지 | API 계열과 연구 worker 계열 두 이미지의 새 parser 및 전체 회사 파일 해시 확인; 비활성 보관 |
| 이동 패키지 | 회사·연구 Git bundle, 입력, 수치 검사, 프로필, 프로그램 및 검증 절차 8개 파일의 해시·계약 확인 |
| 연구 실행·성과 | 이번 작업에서 과학 실행·모델 학습·성과 계산 0 |

[CI](https://github.com/JJongAchii/quant-ai-company/actions/runs/36948691292),
[통합 검사](evidence/conditional-activation-20261001/integration-validation.json),
[수치 검사](evidence/conditional-activation-20261001/numeric-quality.json),
[서버 이미지](evidence/conditional-activation-20261001/server-image-stage.json),
[패키지 검사](evidence/conditional-activation-20261001/worker-package-validation.json).
로컬에서 건너뛴 PDF 메모리 검사는 Linux `RLIMIT_AS`가 필요하다.
CI나 로컬 검사를 새 입력의 실제 3070 실행·실제 Slack 승인·실제 모델 판단으로 표시하지 않는다.

## 고정한 데이터 정책과 이력

새 입력의 `warmup.json` SHA는 `d7d240e4…`, `development.json` SHA는 `7ec74803…`이다.
기존 입력과 준비 초안의 바이트를 유지하고, 새 수치 검사 영수증을 별도로 만들었다.
보호된 평가기 SHA `1984bf6b…`도 그대로다. 실제 워커 검증은 준비 구간만 마운트하고
보호된 평가기의 `qualify`를 호출한다. 개발 구간 평가나 모델 학습을 호출하지 않는다.

정책은 **현재 고정 빈티지에 조건부인 후향적 개발 연구**다. 거래일 당일 23:59 KST 공개는
생성 가정이고 실제 역사적 공개·수정 시각, 원래 준비 당시 clean 바이트는 확인되지 않았다.
조정 시가의 이론적 평가 계약을 사용하며, 과거 시점성·실제 체결·현금 분배금 재투자 수익을
입증하지 않는다. 수치 검사 `ready`는 이 제한된 범위의 공학적 검사 결과다.
실제 직원의 데이터 승인이나 전략 유효성 판정은 아직 없다.

| 새 승인 항목 | 고정 식별자 / 한도 |
|---|---|
| 프로그램 digest | `be0d940bf5904c417d213898617b3f6c0fed19d2bfddd78460ec315e92b216af` |
| 정책 digest | `3b06de9e70370266bfd800c675dc0beb5bee3a3519889a9ac1b19824370768a0` |
| 프로필 digest | `71ee4ce63770fb20205675d42cae4db2719394c0d4e31218484a5d4348f06dd1` |
| 이력 digest | `50fc1f64e78541f0a867e070df20d5b179546b246377a8c30f302cdb28d22a8e` |
| 범위 | `etf_strategy`, 새 가설 과제만 허용 |
| 누적 예산 | 과학 실행 최대 4회, 계산 7,200초, 미션 1개·동시 1개 |
| 첫 회차 | 결과 최대 2개, 자동 다음 회차 없음 |

[프로그램](evidence/conditional-activation-20261001/candidate-program.json),
[정책](evidence/conditional-activation-20261001/data-policy.json),
[검토 식별자](evidence/conditional-activation-20261001/review-package.json).
실제 DB의 기존 대기 과제 10개를 모두 새 계보의 기원으로 포함했다.
기존 프로그램에서 해당 과제들의 미션·과학 시행·예약·계산 소모는 모두 0이다.
[계보 사전값](evidence/conditional-activation-20261001/lineage-preimage-preview.json)은 실제 읽기 기록에서
작성한 검토 자료다. 운영 migration 뒤 실제 `scientific_lineages.history()`와 다시 대조해야 한다.

## 실제 운영 상태와 준비 중 실패

최종 [운영 조회](evidence/conditional-activation-20261001/server-baseline-final-20261002.json)에서
앱·dispatch·Quant Feed는 `ea092f4…`, 연구 worker와 Slack socket은 `3c848af…`다.
서버 이미지 준비 전후 모든 운영 컨테이너 ID, 설정·registry 해시, 프로그램과 과제 기록이 같다.
새 테이블 migration, 프로필·packet 등록, 서비스 재시작, 새 소유자 Slack 요청을 하지 않았다.

첫 이미지 준비에서는 부모 이미지의 `PYTHONPATH=/opt/quant-code/src`가 기존 코드를 선택해
새 parser 검사가 실패했다. [첫 기록](evidence/conditional-activation-20261001/initial-image-stage.json)과
[실제 import 진단](evidence/conditional-activation-20261001/image-import-diagnosis.json), 실패 이미지를 보존했다.
검증된 코드 갱신 Dockerfile로 실제 import 경로를 채운 별도 `-intake2` 이미지 두 개가 검사를 통과했다.

첫 로컬 패키지 준비는 Git bundle에 raw commit만 지정해 실패했다.
[수리 기록](evidence/conditional-activation-20261001/package-preparation-repair.json)을 남기고,
소스가 `HEAD`의 조상인지 확인한 뒤 새 디렉터리에서 named reference로 bundle을 준비했다.
두 기술 실패에서 원격 연구 실행이나 과학 시행은 발생하지 않았다.

## 연결 복구와 다음 진행

[네트워크 확인](evidence/conditional-activation-20261001/worker-connectivity-20261002.json)에서 Tailscale
워커가 오프라인이었고, [최종 SSH 확인](evidence/conditional-activation-20261001/worker-connectivity-final-20261002.json)도
시간 초과였다. PC 전원 상태는 원격으로 알 수 없다. 3070 PC에서 다음을 실행한다.

1. PC를 켜고 Windows에 로그인한다. 작업 중 절전 모드를 피한다.
2. PowerShell에서 `wsl`을 실행한다.
3. WSL 터미널에서 서비스를 시작한다.

```sh
sudo systemctl start tailscaled ssh
systemctl --user start research-worker-tunnel.service research-worker.service
```

연결이 돌아오면 실제 호스트·GPU·기존 설정을 확인하고, 준비 패키지를 새로운 디렉터리에 전달해
준비 구간 자격검증을 실행한다. 성공 영수증을 확보한 뒤
[운영 전환 명세](evidence/conditional-activation-20261001/release-plan.json)에 따라 상태 재확인·백업·호환 코드 적용·
실제 계보 대조를 진행한다. 서버 준비 이미지만으로 연구 권한이 생기지 않는다.

이후 기존 프로그램 취소와 **새 전체 명세에 대한 서명된 Slack 소유자 승인**, 실제 데이터 직원의
독립 평가, 감독자의 과제 선정을 확인한다. 실제 3070 검증과 이 승인·판정이 완료되기 전에는
새 실험을 시작할 수 없다. 검토 초안의 예전 소스 `c113244…` 표기는 준비 시점의 기록으로 보존했고,
이번 운영 후보는 CI·이미지·패키지 기록에 고정한 `19807db8…`다.

전체 [준비 결과](evidence/conditional-activation-20261001/intake-preparation-acceptance.json)와
[qws 진행 상태](../work/lamprey/STATUS.json)에 완료한 준비와 남은 운영 단계를 구분해 기록했다.
