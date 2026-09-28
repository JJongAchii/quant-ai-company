# data-watch 소스별 갱신 현황

2026-09-28 14:00 KST 운영 레이크 목록 검사 기준. 이전에 게시한 “전체 최신 여부를
판단할 수 없음” 요약은 사용자가 각 소스의 마지막 데이터 날짜와 빠진 갱신분을
알 수 없다는 점에서 완료 기준을 충족하지 못했다. 파일 정보 37/38개를 읽었다는
수치는 데이터의 최신 날짜도, 모든 종목의 누락 여부도 말하지 않는다.

| 소스 | 레이크에서 확인한 마지막 날짜 | 해석과 다음 확인 |
|---|---|---|
| KRX 주식·ETF·수급·지수 | 거래일 2026-09-23 | [KRX 추석 휴장 안내](https://kind.krx.co.kr/external/dst/notice/11637/%5B%ED%95%9C%EA%B5%AD%EA%B1%B0%EB%9E%98%EC%86%8C%5D%202026%EB%85%84%20%EC%98%AC%EB%B9%BC%EB%AF%B8%EA%B3%B5%EC%8B%9C%20%EC%95%88%EB%82%B4.pdf)에 따르면 09/24~27은 휴장 기간이다. 09/28 장중에는 09/23이 마지막 거래일이다. 저녁 수집 확인 시각 뒤에도 09/28이 없으면 점검 대상으로 올린다. |
| 미국 ETF·주가 `prices` | 거래일 2026-09-24 | [NYSE 2026 거래 달력](https://www.nyse.com/trade/hours-calendars)에 09/25 휴장은 없다. 09/25 거래일분이 레이크 날짜 최댓값에 반영되지 않았다. 원본 공급·수집·S3 발행 중 어느 단계가 멈췄는지는 이 영수증만으로 특정할 수 없다. |
| FINRA 공매도 거래량 `us_shortvol` | 거래일 2026-09-24 | [FINRA는 일별 파일을 거래일 당일 18:00 ET까지 게시](https://www.finra.org/finra-data/browse-catalog/short-sale-volume-data/daily-short-sale-volume-files)한다고 명시한다. 09/25분이 레이크 최대 거래일에 반영되지 않았다. 수집·발행 경로 점검이 필요하다. |
| 미국 전종목 가격 `us_prices` | 내부 거래일 미확인 | 객체 자체는 09/28 교체됐으나 qdata의 2 MiB Parquet footer 검사 한도를 넘었다. 업로드 날짜로 데이터 날짜를 대신하지 않는다. 검사 한도와 대상 파일을 검토해야 한다. |
| SEC `sec_filings`·`sec_fundamental` | `filed` 2026-03-31 | 이 자료는 전체 EDGAR 실시간 공시가 아니라 분기별 FSDS다. [SEC가 2026 Q2 FSDS를 공개](https://www.sec.gov/data-research/sec-markets-data/financial-statement-data-sets)했지만 레이크 최대 제출일은 Q1 말일이다. Q2 수집·clean 빌드·발행 경로 확인이 필요하다. |
| SEC `sec_13f` | `filed` 2026-05-29 | [SEC의 2026년 6~8월 13F 묶음](https://www.sec.gov/data-research/sec-markets-data/form-13f-data-sets)이 공개됐으나 레이크 최대 제출일은 5월 29일이다. 이 자료의 수집·clean 빌드·발행 경로 확인이 필요하다. |
| SEC `sec_insider` | `filed` 2026-03-31 | [SEC의 2026 Q2 Form 3·4·5 데이터셋](https://www.sec.gov/data-research/sec-markets-data/insider-transactions-data-sets)이 공개됐으나 레이크 최대 제출일은 3월 31일이다. 수집·clean 빌드·발행 중 원인 단계는 아직 확인하지 않았다. |
| FRED·ECOS·OECD CLI | 관측일 09/24·09/23, 기준월 08월 | 소스마다 시리즈·공표 주기가 다르다. 전체 데이터셋의 최대 날짜만으로 모든 시리즈가 최신이라고 판정하지 않는다. |

날짜는 qdata의 읽기 전용 `dataset_catalog()`와 `inspect_dataset(sample_rows=0)`
결과를 PostgreSQL에 저장한 영수증에서 가져왔다. `statistics_complete=true`인 날짜축만
사용한다. SEC 재무의 `ddate` 최대값은 2215년이고 미국 기업행사의 예정일은 2030년까지
있으므로, 그런 미래 날짜를 “최신화됨”의 근거로 사용하지 않는다. `목록`은 38개 데이터셋의
정확한 날짜축·마지막 날짜·객체 교체일을 모두 보여 준다. 정적 매핑과 고정 이력은 오래된
날짜만으로 장애로 취급하지 않는다.
[38개 소스별 운영 영수증 투영](evidence/data-watch-source-coverage-20260928.json)에는
객체 경로·etag·자격 증명을 제외한 날짜와 점검 상태만 남겼다.

메시지의 ⚠에는 두 층위가 있다. 공식 공개 자료나 저녁 수집 시각과 비교해 특정 날짜가
레이크 최댓값에 아직 도달하지 못한 항목을 먼저 표시한다. 공표 달력이 없는 나머지는
보수적인 경과일 문턱으로 **갱신 경로를 살펴볼 항목**만 고른다. 이를 일괄적으로
“원본 지연” 또는 “모든 종목 누락”으로 부르지 않는다. 실제 수집 실패 단계는 생산자
작업 영수증·로그로 별도 확인해야 하며, 이 회사 서비스는 원본을 자동 수정하지 않는다.

## 운영 적용 확인

14:33 KST에 새 일일 요약이 실제 `#data-watch`에 발송됐다. PostgreSQL outbox의
`delivered` 영수증과 Slack 읽기 결과에서 메시지 ID
`7e64b7a6-8e27-5c5c-91f8-f9a6bccfa688`, 발송 시각
`1790573592.470179`가 일치한다. 목록 검사는 14:31 KST에 성공했고 38개 중 37개
파일의 날짜 정보를 확인했다. 메시지는 KRX·미국 일별·거시·공시의 데이터 날짜와
위 표의 갱신 공백을 직접 보여 준다. [운영 읽기 증거](evidence/data-watch-source-status-live-20260928.json)에
발송 영수증과 관측값을 기록했다.

이는 데이터셋 전체의 최대 날짜를 표시한 것이다. 종목별 누락, 원천 수집 실패 단계,
아직 내부 날짜를 읽지 못한 `us_prices`의 최신 거래일은 별도 확인이 필요하다.
