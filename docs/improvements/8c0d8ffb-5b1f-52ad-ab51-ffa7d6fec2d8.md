# 직원이 최신 인터넷 자료를 검색·검증·인용할 수 없음

상태: 조직·행동 개선 제안, 효과 미검증, 사람 검토 필요.

분류: `platform_defect`

## 관찰한 문제

사용자는 요청과 관련된 인터넷 자료를 검색해 정보를 전달하도록 전사 기능 개선을 요청했다. 현재 런타임에는 external_web_search가 비활성화되어 있고, 등록·승인된 출처만 조회할 수 있어 직원이 임의의 웹 원문을 검색하거나 확인할 수 없다. 이는 특정 연구 과제의 답변 문제가 아니라 여러 직원의 최신 정보 응답에 영향을 주는 플랫폼 기능 공백이다.

## 원인 가설

웹 검색 제공자, URL 접근 정책, 원문 보존 및 출처 식별자를 런타임 도구 계약에 연결하는 아키텍처가 아직 없기 때문에 직원 지침만 바꿔서는 이 기능을 제공할 수 없다.

## 재현·추가 조사

현재 시스템 스냅샷에서 external_web_search=false이고 read_source 및 knowledge_search는 승인·등록된 출처로 제한된다. 이 상태에서 인터넷 검색이 필요한 요청을 제출하면 직원은 웹 검색 도구를 호출할 수 없으며, 원문 URL·발표 시각·조회 시각을 확인한 결과를 산출할 수 없다.

## 제안하는 동작

인간 검토를 거친 설계가 검색 대상과 접근 정책, 원문 확인, 출처 URL·발표일·조회 시각 보존, 사실과 해석의 구분, 접근 제한 및 검색 실패 표시, 인용 가능한 source_id 생성 방식을 명시해야 한다. 구현·권한 부여·배포는 별도 승인과 검증 없이는 수행하지 않는다.

## 구현 전에 확인할 성공 기준

검토된 설계 PR이 검색 요청·결과의 도구 계약, 허용 및 차단 정책, 원문과 메타데이터 보존, source_id 생성, 실패·접근 제한 표현, 사용자 답변의 인용 형식, 보안 경계와 후속 회귀시험 계획을 모두 명시하면 성공으로 본다. 이는 구현·배포 완료를 의미하지 않는다.

## 근거 식별자

- `message:999be5d2-fd35-4d0f-88a8-bff9a8d2e762`
- `system:b6d096d1f5983842a5f8e2c306fac53bde5a6c01567f75d98765c643af180c35`
- `code:85f8620493103b5d9e188af5a775598dcc31d311:docs/project/NEXT-STEPS.md`

## 관찰 요약

Review digest: `01cd90f00479fd0895042af61370b29d33bfb628aa7162db796878b1a5538731`.

```json
[
  {
    "end": "2026-09-10 04:54:34.844384+00:00",
    "days": 23,
    "start": "2026-08-18 04:54:34.844384+00:00",
    "tasks": [],
    "turns": [],
    "delegation_edges": [],
    "identical_delegation_signal": {
      "repeated_groups": 0,
      "additional_tasks": "0"
    }
  },
  {
    "end": "2026-09-17 04:54:34.844384+00:00",
    "days": 7,
    "start": "2026-09-10 04:54:34.844384+00:00",
    "tasks": [
      {
        "agent": "data",
        "tasks": 1,
        "status": "blocked",
        "max_depth": 1
      },
      {
        "agent": "data",
        "tasks": 3,
        "status": "completed",
        "max_depth": 1
      },
      {
        "agent": "director",
        "tasks": 1,
        "status": "blocked",
        "max_depth": 0
      },
      {
        "agent": "director",
        "tasks": 23,
        "status": "completed",
        "max_depth": 0
      },
      {
        "agent": "financial_strategist",
        "tasks": 4,
        "status": "completed",
        "max_depth": 1
      },
      {
        "agent": "researcher_kr",
        "tasks": 1,
        "status": "completed",
        "max_depth": 1
      }
    ],
    "turns": [
      {
        "agent": "data",
        "turns": 1,
        "status": "blocked",
        "attempts": 1,
        "retried_turns": 0
      },
      {
        "agent": "data",
        "turns": 12,
        "status": "completed",
        "attempts": 12,
        "retried_turns": 0
      },
      {
        "agent": "director",
        "turns": 4,
        "status": "blocked",
        "attempts": 4,
        "retried_turns": 0
      },
      {
        "agent": "director",
        "turns": 35,
        "status": "completed",
        "attempts": 35,
        "retried_turns": 0
      },
      {
        "agent": "financial_strategist",
        "turns": 4,
        "status": "completed",
        "attempts": 4,
        "retried_turns": 0
      },
      {
        "agent": "researcher_kr",
        "turns": 1,
        "status": "completed",
        "attempts": 1,
        "retried_turns": 0
      }
    ],
    "delegation_edges": [
      {
        "sender": "director",
        "recipient": "data",
        "delegations": 4
      },
      {
        "sender": "director",
        "recipient": "financial_strategist",
        "delegations": 1
      },
      {
        "sender": "director",
        "recipient": "researcher_kr",
        "delegations": 1
      }
    ],
    "identical_delegation_signal": {
      "repeated_groups": 0,
      "additional_tasks": "0"
    }
  }
]
```

위 집계는 생성 시기별 현재 상태이며 품질 점수가 아니다. 구간 길이가 다르므로 원시 건수를 직접 비교하지 않는다. 반복 위임은 문제의 증명이 아니며 업무상 필요했을 수 있다.

이 PR은 설계 문서만 추가한다. 실행 코드·권한·직원 구성·모델·운영 설정은 변경하지 않는다. 문서 CI 통과로 행동 개선이 입증되지는 않는다. 구현과 검증은 검토한 범위에서 별도로 진행한다.
