# 🆕 v2.1.4 업데이트: 시간 결정 분리(`resolve_time_scope`) 도입 (2026-03-03)

시간 처리 오류를 줄이기 위해, 시간 범위 결정을 파싱 단계와 분리한 구조화 노드를 추가했습니다.

## ✅ 반영 내용

- `resolve_time_scope` 노드 추가
  - 파싱 결과 + 이전 확정 시간 범위를 입력으로 받아 최종 시간 스코프를 결정
  - 출력은 `effective_time_scope`로 상태에 저장
  - 파일: `backend/src/agents/text_to_sql/nodes.py`
- 그래프 흐름 변경
  - `parse_request -> validate_request -> resolve_time_scope -> check_clarification`
  - 파일: `backend/src/agents/text_to_sql/graph.py`
- 신규 구조화 스키마 추가
  - `TimeScopeMode`, `TimeScopeDecision`
  - 파일: `backend/src/agents/text_to_sql/schemas.py`
- 시간 결정용 프롬프트 추가
  - `TIME_SCOPE_RESOLVE_SYSTEM`, `TIME_SCOPE_RESOLVE_USER`
  - 파일: `backend/src/agents/text_to_sql/prompts.py`
- SQL/검증 프롬프트 입력에서 `effective_time_scope` 우선 사용
  - SQL 문자열 파싱 fallback 의존도 축소
  - 파일: `backend/src/agents/text_to_sql/common/helpers.py`
- 상태 확장
  - `effective_time_scope` 필드 추가
  - 파일: `backend/src/agents/text_to_sql/state.py`

## 🧪 테스트

- 신규 테스트:
  - inherit 모드에서 이전 확정 시간 범위 적용
  - all_time 모드가 follow-up 상속보다 우선
- 누적 결과:
  - `27 passed`

---

# 🆕 v2.1.3 업데이트: 후속질문 기본 경로 벡터 보강 검색 활성화 (2026-03-03)

후속질문에서 이전 테이블 재사용만 하고 벡터 검색을 건너뛰던 경로를 수정했습니다.
이제 강제 재검색 플래그가 없어도, 후속질문은 기본적으로 **이전 테이블 + 벡터 후보 보강**을 함께 수행합니다.

## ✅ 반영 내용

- `retrieve_tables` 개선:
  - 후속질문에서 이전 SQL 테이블을 기반으로 유지하되, Qdrant 검색을 항상 병행
  - 벡터 검색 결과가 비어도 이전 테이블 후보를 fallback으로 유지
  - 후속질문 컨텍스트 로그를 `이전 테이블 기반 + 보강 검색 완료` 형태로 명확화
  - 파일: `backend/src/agents/text_to_sql/nodes.py`

## 🧪 테스트

- 신규 테스트:
  - 후속질문 기본 경로에서도 벡터 검색이 호출되고 신규 후보(예: `metrics_disk`)가 병합되는지 검증
- 누적 결과:
  - `25 passed`

---

# 🆕 v2.1.2 업데이트: 후속질문 `COLUMN_MISSING` 자동 승격 보강 (2026-03-03)

후속질문에서 필요한 지표가 바뀌었을 때(예: RAM 결과 기준으로 CPU 지표 요청) 검증이 `COLUMN_MISSING`으로 떨어지면,
기존에는 SQL 재생성만 반복되어 테이블 보강 재검색으로 넘어가지 못하는 케이스가 있었습니다.

## ✅ 반영 내용

- `validate_llm` 보강:
  - `is_followup=true` + `COLUMN_MISSING`이면 `TABLE_MISSING`으로 승격
  - `force_table_search=true`를 설정해 `retrieve_tables` 재검색 경로로 유도
  - 파일: `backend/src/agents/text_to_sql/nodes.py`
- `retrieve_tables` 보강:
  - `force_table_search=true`일 때 벡터 검색 `top_k`를 확대
  - 파싱된 `metric/condition` 힌트를 검색 쿼리에 추가해 관련 테이블 탐색 정확도 개선
  - 파일: `backend/src/agents/text_to_sql/nodes.py`

## 🧪 테스트

- 신규 테스트:
  - 후속질문 `COLUMN_MISSING` -> `TABLE_MISSING` 승격 및 재검색 플래그 설정 검증
  - 강제 재검색 시 `top_k` 확대 + metric 힌트 주입 검증
- 누적 결과:
  - `24 passed`

---

# 🆕 v2.1.1 업데이트: 후속질문 테이블 보강/시간 상속 안정화 (2026-03-03)

이번 반영은 후속질문에서 `TABLE_MISSING`이 발생할 때 테이블 보강이 실제로 재시도되도록 그래프 라우팅과 검색 로직을 정리한 수정입니다.

## ✅ 반영 내용

- `TABLE_MISSING` 판정 시 `generate_sql` 재시도가 아니라 `retrieve_tables`로 재진입하도록 라우팅 추가
  - 파일: `backend/src/agents/text_to_sql/graph.py`
- `validate_llm`에서 `TABLE_MISSING` 발생 시 `force_table_search=True`를 설정해 다음 턴 검색을 강제
  - 파일: `backend/src/agents/text_to_sql/nodes.py`
- `retrieve_tables`에서 강제 재검색 시:
  - 이전 SQL 테이블 + Qdrant 후보를 병합(중복 제거)
  - 이후 `select_tables`에서 재선정 가능하도록 후보군 확장
  - 파일: `backend/src/agents/text_to_sql/nodes.py`
- 시간 상속 fallback 보강:
  - `ts BETWEEN` 외 `ts >=`, `ts <=` 형태도 시간 범위 추출 가능
  - 파일: `backend/src/agents/text_to_sql/common/helpers.py`
- follow-up 프롬프트 문구 정리:
  - "무조건 이전 테이블만 사용" 제약 완화, 필요 시 테이블 보강 허용 명시
  - 파일: `backend/src/agents/text_to_sql/prompts.py`

## 🧪 테스트

- 추가 테스트:
  - `TABLE_MISSING` 라우팅 분기 테스트
  - follow-up 강제 재검색 시 이전/신규 테이블 병합 테스트
  - 비교 연산자 기반 시간 범위 추출 테스트
- 실행 결과:
  - `23 passed`

---

# 🆕 v2.1 업데이트: LangChain Structured Outputs 전환 (2026-03-02)

이번 업데이트는 OpenAI JSON 모드 + 수동 파싱(`json.loads`) 기반 흐름을 제거하고,  
**LangChain `with_structured_output(Pydantic)`** 중심으로 전환한 리팩토링입니다.

---

## ✅ 1. 구조화 출력 전환

### 🔹 스키마 중심 아키텍처 도입
- `src/agents/text_to_sql/schemas.py` 신설
- 주요 모델 정의:
  - `IntentClassification`
  - `ClarificationCheck`
  - `ParsedRequestModel`
  - `TableRerankResult` (`items` 래퍼 구조)
  - `GenerateSqlResult`
  - `ValidationResult` / `ValidationVerdict`

### 🔹 LLM 바인딩 방식 변경
- Before: `llm.bind(response_format={"type":"json_object"})` + 문자열 파싱
- After: `llm.with_structured_output(PydanticModel)` + 모델 인스턴스 직접 수신

적용 노드:
- `classify_intent`
- `parse_request`
- `check_clarification`
- `select_tables`
- `generate_sql`
- `validate_llm`

---

## 🛡️ 2. 안정성 개선

### 🔹 OpenAI 400 스키마 오류 해결
다음 오류 대응:
- `Invalid schema ... 'additionalProperties' is required to be supplied and to be false`

조치:
- Structured Output 스키마 공통 베이스(`StructuredModel`) 도입
- 모든 모델에 `extra="forbid"` 적용
- 결과적으로 루트/중첩 모델 모두 `additionalProperties: false` 보장

### 🔹 임시 raw fallback 제거
- `generate_sql`의 구조화 출력 실패 시 raw 재호출/수동 SQL 추출 경로 삭제
- 이제 구조화 출력 실패는 명시적 실패 상태로 반환되어 문제를 즉시 탐지 가능

---

## 🧹 3. 코드 정리 (Clean Code)

- 미사용 파싱 유틸 `parse_json_from_llm` 제거
- 프롬프트 내 불필요한 “JSON만 출력” 강제 문구 정리
- 검증 필드명 정합성 통일 (`reason`, `hint`)
- `state.py` 타입 정리 (`is_followup` 반영, 미사용 `feedback_to_sql` 제거)

---

## 🧪 4. 테스트 및 검증

- 테스트 목킹을 문자열 응답 기반에서 **Pydantic 모델 인스턴스 기반**으로 전환
- 신규 테스트 추가:
  - 구조화 출력 실패 시 `generate_sql`이 raw fallback 없이 실패 반환하는지 검증
- 실행 결과:
  - `16 passed`

테스트 실행 예시:
```bash
DB_USER=test DB_PASSWORD=test OPENAI_API_KEY=test \
backend/.venv/bin/python -m pytest backend/src/tests/test_nodes.py backend/src/tests/test_routing.py backend/src/tests/test_query_api.py -q .
```

---

## 📦 5. 운영 메모

- 이번 변경으로 `backend/requirements.txt`, `backend/Dockerfile` 수정은 필수 아님
- Docker 사용 시 호스트 전역 Python 환경과 무관하게 컨테이너 환경 기준으로 동작

---

# 🚀 v2.0 대규모 업데이트: LangGraph 기반 능동형 에이전트 전환

이번 업데이트는 기존의 수동적인 SQL 생성기를 넘어, **LangGraph(랭그래프)** 라이브러리의 강력한 상태 관리 기능을 100% 활용하여 **기억력과 판단력을 갖춘 지능형 에이전트**로 진화시키는 것에 초점을 맞추었습니다.

---

## 💡 1. 핵심 기술 도입 (Key Technologies)

### 🔹 LangGraph Checkpointer (`AsyncPostgresSaver`)
- **Before**: 매번 API 요청 시 이전 대화 내역 전체를 DB에서 긁어와 문자열로 합쳐서 LLM에게 던져주는 방식 (Prompt Injection). 서버가 죽거나 재시작하면 진행 중이던 대화 상태가 유실될 위험이 컸습니다.
- **After**: **LangGraph 내장 체크포인트 시스템** 도입.
  - `thread_id` 하나로 대화 히스토리, 생성된 SQL 쿼리, 실행 결과 데이터, 에러 메시지 등 **에이전트의 모든 상태(State)를 DB에 영구 저장**합니다.
  - 사용자가 브라우저를 껐다 켜거나 서버를 재시작해도, **마지막 대화 지점에서 완벽하게 복원(Resume)**됩니다.

### 🔹 조건부 엣지 (Conditional Edge) & 분기 처리
- **Before**: 사용자 질문 -> SQL 생성 -> 실행이라는 단일 선형 구조로만 동작하여, 질문이 모호해도 무조건 SQL을 만들려다 실패했습니다.
- **After**: **의도 분류(Intent Classification)** 노드 도입.
  - 질문이 들어오면 먼저 **[SQL, General, Clarification]** 중 어떤 의도인지 판단하여 실행 경로를 동적으로 바꿉니다.
  - "안녕", "고마워" 같은 인사에는 SQL을 생성하지 않고 즉시 친절한 답변을 생성합니다.

---

## 🛠️ 2. 기능적 강화 (Functional Enhancements)

### 🔄 멀티턴(Multi-turn) 대화 능력 강화
단발성 질문만 처리하던 기존과 달리, 사람처럼 꼬리에 꼬리를 무는 대화가 가능해졌습니다.

1. **문맥 기반 의도 파악**:
   - "전체 기간", "모든 데이터"라고 말하면, 이전 쿼리의 시간 제약에 얽매이지 않고 **`ALL` 토큰**을 생성하여 상속 로직을 스스로 끊고 전체를 조회합니다.
   - "2.5일" 같은 약어 날짜도 정확히 파싱합니다.

2. **똑똑한 상속(Smart Inheritance)**:
   - "상위 5개만 보여줘"라고 하면 "무엇의 상위 5개?"라고 되묻지 않고, **직전 실행했던 SQL 쿼리의 조건과 테이블을 그대로 가져와서** `LIMIT` 조건만 추가하여 재실행합니다.

### 🙋 휴먼-인-더-루프 (HITL) & 역질문
에이전트가 모르는 것을 아는 척하지 않고, 사용자에게 되물어보는 능력이 생겼습니다.

- **Before**: 정보가 부족하면 에이전트가 임의로 가정하고 이상한 SQL을 만들거나 "정보 부족" 에러를 뱉었습니다.
- **After**: **Interrupt(중단) 메커니즘** 도입.
  - 필수 정보(측정 지표 등)가 누락되었다고 판단되면, SQL 생성 단계로 넘어가지 않고 **즉시 멈추어(Interrupt)** 구체적인 역질문을 던집니다.
  - 사용자가 대답을 하면 그 정보를 바탕으로 **중단된 지점부터 자연스럽게 작업을 재개**합니다.

### 📝 결과 요약 및 일반 대화 지원
단순히 표만 보여주는 것이 아니라, 데이터가 의미하는 바를 해석해 줍니다.

- **결과 요약(Data Interpretation)**:
  - SQL 실행 결과(표 데이터)를 LLM이 분석하여 "평균 CPU 사용률은 10%이며 안정적입니다"와 같이 **자연어 보고서**를 작성해 줍니다.
  
- **일반 대화(General Chat)**:
  - SQL과 무관한 시스템 사용법 질문이나 가벼운 대화도 에이전트가 맥락에 맞게 자연스럽게 받아줍니다.

---

## 📊 요약: Before vs After

| 구분 | Before (v1.0) | After (v2.0 LangGraph Native) |
| :--- | :--- | :--- |
| **상태 관리** | 수동 DB 조회 및 프롬프트 주입 | **Checkpointer 자동 저장 및 복원** |
| **대화 흐름** | 선형적 (질문 -> SQL) | **동적 분기 (조건에 따라 경로 변경)** |
| **정보 부족 시** | 에러 발생 또는 환각(Hallucination) | **즉시 멈추고 역질문 (HITL)** |
| **일반 대화** | 불가능 (SQL 생성 실패로 처리) | **가능 (별도 경로로 우회 처리)** |
| **결과물** | 단순 데이터 표(Table) | **데이터 표 + 인사이트 요약 보고서** |

---

## 📡 3. API 이벤트 규약 (API Event Contract)

프론트엔드와 백엔드 간의 실시간 통신을 위한 **Server-Sent Events (SSE)** 프로토콜 명세입니다.

### 1. `status` (진행 상태)
- **설명**: 각 단계별 진행 상황을 실시간으로 알립니다.
- **Payload**:
  ```json
  {
    "type": "status",
    "message": "SQL 쿼리 생성 중...",
    "node": "generate_sql"
  }
  ```

### 2. `clarification` (역질문 - HITL)
- **설명**: 정보가 부족할 때 사용자에게 추가 정보를 요청합니다.
- **Payload**:
  ```json
  {
    "type": "clarification",
    "message": "어떤 기간의 매출을 원하시나요?",
    "session_id": "uuid-..."
  }
  ```

### 3. `result` (최종 결과)
- **설명**: 작업 완료 성공 시 최종 데이터와 보고서를 전달합니다.
- **Payload**:
  ```json
  {
    "type": "result",
    "payload": {
      "ok": true,
      "agent": "sql" | "general",
      "session_id": "uuid-...",
      "data": {
        "report": "...",
        "suggested_actions": ["..."],
        "raw": { ... }
      }
    }
  }
  ```

### 4. `error` (오류 발생)
- **설명**: 처리 중 치명적인 오류가 발생했을 때 전송됩니다.
- **Payload**:
  ```json
  {
    "type": "error",
    "message": "서버 내부 오류가 발생했습니다."
  }
  ```
