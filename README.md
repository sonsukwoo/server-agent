# 🤖 Text-to-SQL Agent (Server Agent)

**서버 모니터링 및 자연어 데이터 분석 에이전트**

이 프로젝트는 자연어 질문을 SQL 쿼리로 변환하여 실시간으로 쌓이는 데이터베이스를 조회하고, 서버 리소스 상태를 모니터링하는 지능형 에이전트 시스템입니다.  
**RAG (Retrieval-Augmented Generation)** 기법과 **LangGraph** 기반의 워크플로우를 사용하여 복잡한 질의를 처리하며, **실시간 스키마 감지** 및 **사용자 규칙 기반 알림** 기능을 통해 DB 구조 변경과 서버 리소스 상태 변화에 즉시 대응합니다.

---

## 🔥 핵심 기능 (Key Features)

### 1. 🛡️ 보안 미들웨어 (Security Middleware)
사용자의 입력이 시스템에 도달하기 전, **`InputGuard` 미들웨어**가 위험한 요청을 사전에 차단합니다.
- **프롬프트 인젝션 방지**: "Ignore previous instructions", "System prompt" 등 LLM의 동작을 조작하려는 시도를 차단합니다.
- **입력 길이 제한**: 과도한 토큰 사용 유발을 방지합니다 (최대 1000자).
- **SQL 안전성 검사**: 생성된 SQL 쿼리에 `DROP`, `DELETE`, `TRUNCATE` 등 파괴적인 명령어가 포함되었는지 2차 검증합니다.

### 2. ⚡ 실시간 스키마 동기화 (Real-time Schema Sync)
데이터베이스의 테이블이 생성되거나 변경되는 즉시 에이전트가 이를 인지합니다.
- **PostgreSQL LISTEN/NOTIFY**: `SchemaListener`가 DB의 DDL 이벤트를 실시간으로 수신 대기합니다.
- **자동 임베딩 업데이트**: 스키마 변경 시 Qdrant 벡터 저장소의 관련 정보를 자동으로 갱신하여, 에이전트가 항상 최신 테이블 구조를 기반으로 답변할 수 있습니다.

### 3. 🧠 지능형 질의 구조화 및 미들웨어 검증 (LLM & Middleware)
사용자의 투박한 질문을 에이전트가 분석하기 최적화된 정교한 구조로 변환하고, 미들웨어를 통해 데이터의 신뢰성을 보장합니다.
- **LLM JSON Mode**: 사용자 질문을 즉시 분석하여 의도(Intent)와 파라미터가 분리된 정밀한 JSON 구조로 변환합니다. 이는 에이전트가 모호함 없이 쿼리를 생성할 수 있는 토대가 됩니다.
- **`ParsedRequestGuard` 미들웨어 검증 및 교정**:
    - **시간 범위 기본값 미적용**: 사용자가 시간을 명시하지 않으면 시간 조건을 추가하지 않습니다(전체 기간 기준).
    - **미래 시점 Auto-Clipping**: "오늘 데이터 보여줘"와 같은 요청 시 LLM이 시간 범위를 00:00~24:00로 설정하더라도, 미들웨어가 현재 시각을 확인하여 **24:00인 종료 시간(End Time)을 현재 시각으로 즉시 교정**합니다.
    - **후속 질문 상속 플래그**: 참조 표현을 감지하면 `inherit` 모드로 표시하고, 이전 쿼리의 시간 조건/필터를 유지하도록 유도합니다.
    - **에이전트 친화적 구조 확립**: 구조화된 질문이 물리적/논리적으로 유효한지 미들웨어 단계에서 한 번 더 검증하고 보정함으로써, 에이전트가 환각(Hallucination) 없이 정확한 SQL을 생성하도록 유도합니다.

### 4. 🔗 스키마 자동 인식 및 고급 RAG (Table Discovery)
에이전트가 여러 테이블 중 정답을 찾기 위해 벡터 DB와 실시간 스키마 정보를 결합합니다.
- **프로젝트 시작 시 지능적 동기화 (Hash-based Smart Sync)**: 
    - **최초 구동 시**: 데이터베이스의 전체 스키마를 자동으로 스캔하여 테이블 명세(DDL, 주석)를 추출하고 벡터화를 수행합니다.
    - **재시작 시 (중복 작업 방지)**: 이전 구동 시 저장된 **스키마 해시(Hash)** 값과 현재 DB 상태를 비교합니다. 데이터베이스 구조에 변경이 없으면 무거운 스캔 및 임베딩 과정을 스킵하여 서버 시작 속도를 획기적으로 향상시킵니다.
    - **실시간 데이터 동기화**: 시작 시 해시값이 다르거나 구동 중 DDL 이벤트가 발생하면 즉시 변경 사항을 감지하여 최신 상태로 동기화합니다.
- **벡터화 및 검색**: 추출된 테이블 정보를 Qdrant 벡터 DB에 저장하고, 사용자의 구조화된 질문에서 추출된 질의를 기반으로 가장 적합한 테이블을 RAG(Retrieval) 방식으로 검색해옵니다.
- **정밀한 컨텍스트 주입 (Targeted Information Injection)**: 
    - 벡터 검색을 통해 질문과 가장 관련 있는 테이블-청크를 선별한 후, 해당 테이블의 **정확한 명칭(Table Name)**과 **전체 컬럼 목록(Column List)**을 추출합니다.
    - 단순히 스키마 정보만 나열하는 것이 아니라, 각 컬럼이 **어떤 데이터를 담고 있는지에 대한 상세 설명(Comment/Description)**을 함께 제공합니다. 이를 통해 LLM은 테이블 간의 관계와 각 컬럼의 용도를 명확히 이해하고, 실제 실행 가능한 최적의 SQL을 생성하게 됩니다.
- **지능형 테이블 확장 및 캐싱**: 
    - LLM 리랭크(Rerank)를 거쳐 가장 관련성 높은 **Top-5** 테이블을 우선적으로 컨텍스트에 포함합니다.
    - Top-5에 들지 못한 나머지 후보 테이블들은 내부 캐시에 안전하게 보관합니다.
    - 쿼리 생성이나 검증 단계에서 "테이블 정보가 부족하다"고 판단될 경우, 에이전트가 스스로 **툴 콜(Tool Call)**을 수행하여 캐시된 후보들 중 필요한 테이블을 추가로 탐색하고 컨텍스트를 확장합니다.

### 5. 🧠 맥락 인식 채팅 메모리 및 최적화 (Context-aware Memory)
대화가 길어져도 시스템은 이전 맥락을 기억하며, 효율적인 토큰 관리를 수행합니다.
- **장단기 기억 하이브리드 모드**:
    - **단기 기억 (Recent Context)**: 가장 최근의 대화는 원문 그대로 유지하여 에이전트가 "그거 실행해줘"와 같은 지시어의 대상을 명확히 파악하게 합니다.
    - **장기 기억 (Summarized History)**: 오래된 대화는 백그라운드에서 AI가 자동으로 요약하여 핵심 정보만 보존합니다. 이를 통해 과거 맥락을 참조하면서도 토큰 비용을 최소화합니다.
- **백그라운드 비동기 요약**: 사용자의 응답 대기 시간에 영향을 주지 않도록, 답변 완료 후 비동기(FastAPI BackgroundTasks)로 요약 엔진이 작동합니다.
- **커서 기반 요약 갱신**: 요약 시점 이후의 새로운 메시지만 선별하여 요약을 업데이트하는 지능적인 커서 시스템을 통해 불필요한 연산을 방지합니다.

### 6. 📊 리소스 모니터링 및 알림 이력 (Dashboard & Alert History)
- **실시간 지표 시각화**: CPU, 메모리, 디스크 사용량을 실시간 차트로 시각화하여 현재 서버 상태를 직관적으로 파악합니다.
- **고급 알림 규칙 (Lego Blocks)**: 사용자가 직접 "CPU > 80% 일 때 알림" 같은 임계치 규칙을 웹 UI에서 블럭 조립하듯 설정할 수 있습니다.
- **지능형 알림 이력 및 추적**: 설정된 트리거가 발생하면 모든 내역이 **알림 히스토리**에 저장됩니다. 이를 통해 어떤 이슈가 **어느 시점에 발생했는지** 빠르게 추적할 수 있으며, 과거 데이터와 비교하여 시스템의 안정성 패턴을 한눈에 파악할 수 있습니다.
- **상승 이벤트 기반 알림**: 임계값을 **넘는 순간**에만 1회 기록하며, 값이 내려갔다가 다시 넘으면 다시 기록됩니다.

### 7. 🔌 제로 구성 이식성 (Zero-Config Portability)
이 시스템은 특정 데이터베이스에 종속되지 않는 유연한 구조를 가지고 있습니다.
- **자동 스키마 구축**: `.env`에서 DB 연결 정보만 변경하면, 서버 시작 시 채팅 저장 및 모니터링에 필요한 모든 테이블, 스키마, 그리고 **실시간 스키마 변경 감지 및 사용자 규칙기반 모니터링에 필요한 이벤트 함수 및 트리거**까지 자동으로 생성합니다. (없을경우에만)
- **즉시 재사용 가능**: 기존에 사용하던 모든 에이전트 워크플로우와 알림 설정 체계가 새로운 DB 환경에서도 즉시 적용되어, 환경 이관이나 복구가 매우 빠릅니다.

---

## 🧭 시간 모드 (Time Mode)
시간 범위가 명시되지 않은 질문과 후속 질문의 동작을 명확히 하기 위해 `time_mode`를 사용합니다.

- **all_time**: 시간 범위가 명시되지 않은 경우. SQL에 시간 조건을 추가하지 않습니다.
- **inherit**: 이전 결과를 참조하는 후속 질문에서 시간 범위를 명시하지 않은 경우. 이전 SQL의 시간 조건을 유지합니다.
- **explicit**: 사용자가 시간 범위를 명시한 경우. 해당 범위를 그대로 반영합니다.

검증 단계에서 `time_mode`와 SQL의 시간 조건이 일치하지 않으면 재생성을 요청합니다.

---

## 🔗 스키마 드롭다운 API
고급 알림 설정 화면에서 테이블/컬럼을 드롭다운으로 선택하기 위해 스키마 목록 API를 제공합니다.

- **GET** `/schema/tables`
  - 반환 형식:
    ```json
    [
      {"table": "schema.table", "columns": ["col1", "col2"]}
    ]
    ```

## 🛠️ 기술 스택 (Tech Stack)

### Backend
- **Framework**: `FastAPI` (High-performance API)
- **Agent Orchestration**: `LangGraph`, `LangChain`
- **Database**: `PostgreSQL` (Asyncpg for async I/O)
- **Vector Store**: `Qdrant` (Schema embedding & storage)
- **Tooling**: `MCP (Model Context Protocol)` (Standardized tool interface)

### Frontend
- **Framework**: `React`, `Vite` (TypeScript)
- **Styling**: `Vanilla CSS` (Dark Theme Optimized)
- **Components**: `Lucide React` (Icons)

---

## 🚀 에이전트 워크플로우 (Architecture Flow: LangGraph)

에이전트는 **LangGraph**를 기반으로 설계되었으며, 각 단계(Node)는 명확한 책임과 도구(Tool)를 가집니다. 특히 실패 시 스스로 쿼리를 수정하거나 테이블 정보를 추가로 확장하는 **순환 구조(Cyclic)**를 가집니다.

```mermaid
graph TD
    %% Entry
    Start((시작)) --> parse_request["1️⃣ parse_request (구조화)"]
    
    %% Input Layer
    parse_request --> validate_request{"2️⃣ validate_request (검증)"}
    
    %% Retrieval Layer
    validate_request -- "통과" --> retrieve_tables["3️⃣ retrieve_tables (벡터 검색)"]
    validate_request -- "차단" --> generate_report
    
    retrieve_tables --> select_tables{"4️⃣ select_tables (리랭킹)"}
    
    %% Generation & Guard Layer
    select_tables -- "성공" --> generate_sql["5️⃣ generate_sql (SQL 생성)"]
    select_tables -- "실패" --> generate_report
    
    generate_sql --> guard_sql{"6️⃣ guard_sql (보안 검사)"}
    guard_sql -- "Retry" --> generate_sql
    guard_sql -- "OK" --> execute_sql["7️⃣ execute_sql (DB 실행)"]
    
    %% Execution & Validation Layer
    execute_sql --> normalize_result["8️⃣ normalize_result (정규화)"]
    normalize_result --> validate_llm{"9️⃣ validate_llm (결과 검증)"}
    
    %% Cyclic Correction
    validate_llm -- "Retry SQL" --> generate_sql
    validate_llm -- "OK/Fail" --> generate_report["� generate_report (최종 보고서)"]
    
    %% End
    generate_report --> End((종료))

    %% (Tools & Metadata section removed to show only node flow)
```

### 📋 노드별 상세 설명 및 도구 호출

| 단계 | 노드명 (Node) | 역할 및 상세 설명 | 사용 도구 / 기술 |
| :--- | :--- | :--- | :--- |
| **1** | **`parse_request`** | 사용자 자연어를 분석하여 **의도(Intent), 지표(Metric), 시간 범위** 등을 JSON으로 구조화합니다. | `ChatOpenAI` (JSON Mode) |
| **2** | **`validate_request`** | 구조화된 요청의 보안성과 논리적 타당성을 검증합니다. (예: 미래 시점 조회 방지, 시각 보정, 시간 모드 처리) | `ParsedRequestGuard` (Middleware) |
| **3** | **`retrieve_tables`** | 질문과 관련 있는 테이블을 벡터 공간에서 검색하여 후보군을 확보합니다. | **Tool**: `search_tables` (Qdrant) |
| **4** | **`select_tables`** | 확보된 후보 중 **Top-K(Elbow Cut)**를 적용하여 실제 쿼리에 사용할 테이블을 확정합니다. | `LLM Rerank` |
| **5** | **`generate_sql`** | 정밀한 테이블 메타데이터를 참조하여 SQL을 생성합니다. 정보 부족 시 스스로 캐시된 테이블을 확장합니다. | **Tool**: `expand_tables` (Internal Cache) |
| **6** | **`guard_sql`** | 생성된 SQL이 `DROP` 등 파괴적인 명령을 포함하는지, 문법이 맞는지 사전에 검사합니다. | `SqlOutputGuard` (Middleware) |
| **7** | **`execute_sql`** | 최종 검증된 SQL을 PostgreSQL 데이터베이스에서 실행하여 결과를 가져옵니다. | **Tool**: `execute_sql` (Postgres) |
| **8** | **`normalize_result`** | 실행 결과 데이터의 가독성을 높이고, 에러 메시지를 기술적으로 정규화합니다. | `Result Normalizer` |
| **9** | **`validate_llm`** | 실행 결과가 사용자의 질문에 부합하는지 최종 검증합니다. 부족할 경우 **고쳐쓰기(Retry)**를 요청합니다. | **Cyclic**: `Reflection` & `Self-Healing` |
| **10** | **`generate_report`** | 최종 데이터와 분석 내용을 바탕으로 사용자가 이해하기 쉬운 자연어 리포트를 작성합니다. | `Markdown Report Gen` |

### 🧠 핵심 기술: 지능형 테이블 캐싱 및 확장
- **Top-5 Rerank**: 벡터 검색 결과 중 가장 연관성이 높은 5개 테이블을 우선 컨텍스트로 사용합니다.
- **후보군 캐싱**: TOP-5에 들지 못한 나머지 테이블은 내부 상태에 캐싱해 둡니다.
- **Dynamic Expansion**: `generate_sql` 노드에서 LLM이 테이블 정보가 더 필요하다고 판단하면, `expand_tables` 툴을 호출하여 캐시에서 관련 테이블을 즉시 추가하고 쿼리를 재생성합니다.

---

## 📂 프로젝트 구조 (Directory Structure)

본 프로젝트는 도메인 중심의 모듈화된 구조를 가지고 있으며, 각 디렉토리는 명확한 책임 범위를 가집니다.

<details>
<summary><b>상세 디렉토리 구조 보기 (클릭하여 확장)</b></summary>

```text
server-agent/
├── backend/src/             # 🏰 Core Engine (FastAPI & 에이전트 로직)
│   ├── advanced_settings/   # 알림 조건(Lego Blocks) 기반 모니터링 모듈
│   │   ├── schemas.py       # Pydantic 기반 데이터 검증 모델 (Rule/History)
│   │   ├── templates.py     # SQL 기반 트리거 및 함수 생성용 템플릿
│   │   ├── service.py       # 알림 규칙 관리 서비스 Layer
│   │   ├── listener.py      # DB NOTIFY 채널 실시간 수신 리스너
│   │   └── router.py        # 규칙 CRUD 및 알림 이력 조회 API
│   ├── agents/              # 지능형 에이전트 핵심 로직
│   │   ├── text_to_sql/     # Text-to-SQL 워크플로우 (LangGraph)
│   │   │   ├── graph.py     # 에이전트 상태 전이 및 그래프 구조 정의
│   │   │   ├── nodes.py     # 분석, 검색, 생성, 검증 등 핵심 노드 구현
│   │   │   ├── prompts.py   # 단계별 시스템/사용자 프롬프트 관리
│   │   │   ├── state.py     # 에이전트 실행 상태(State) 스키마 정의
│   │   │   ├── chat_context.py # 에이전트용 채팅 맥락(Summary+Recent) 구성 엔진
│   │   │   └── table_expand_too.py # 캐시 기반 테이블 정보 확장 도구
│   │   └── mcp_clients/     # 외부 MCP 서버 통합 클라이언트
│   │       └── connector.py # HTTP 기반 MCP 서버 연동 공통 모듈
│   ├── api/                 # FastAPI 웹 프레임워크 인프라
│   │   ├── main.py          # 앱 진입점 및 라우터 통합 등록
│   │   ├── lifespan.py      # Startup/Shutdown 관리 (DB 초기화 등)
│   │   ├── query.py         # 에이전트 질의 및 SSE 스트리밍 API
│   │   ├── schema.py        # 스키마 테이블/컬럼 드롭다운 API
│   │   ├── chat.py          # 채팅 세션 및 기록 관리 API
│   │   └── resource.py      # 실시간 서버 자원 모니터링 API
│   ├── db/                  # 데이터 저장소 액세스 레이어
│   │   ├── db_manager.py    # 커넥션 풀 및 기본 DB 접근 총괄
│   │   └── chat_context.py  # 채팅 요약 상태 및 커서 기반 데이터 접근 전용
│   └── schema/              # 지능형 DB 스키마 관리 및 벡터화
│       ├── orchestrator.py  # 초기 동기화 및 감지 프로세스 제어
│       ├── listener.py      # PostgreSQL DDL 이벤트 실시간 감지
│       ├── sync.py          # 스키마 정보 추출 및 Qdrant 벡터화 동기화
│       ├── trigger_setup.py # 변경 감지 전용 트리거 자동 설치
│       └── hash_utils.py    # 스키마 변경 여부(Diff) 판별 해시 유틸
├── mcp_servers/             # 🔌 Standardized Tools (MCP 서버군)
│   ├── postgres/            # DB 지능형 제어 서버 (SQL 실행 등)
│   └── qdrant/              # 벡터 검색 서버 (테이블 시맨틱 검색)
└── frontend/                # 📊 Dashboard (React + Vite)
    ├── src/                 # 대시보드 UI 및 상태 관리 (TypeScript)
    └── index.html           # SPA 진입점
```

</details>

---

## 🚀 시작하기 (Getting Started)

### 1. 환경 변수 설정
`.env` 파일을 생성하고 필요한 API 키와 DB 설정을 입력하세요.

### 2. 실행
Docker Compose를 사용하여 모든 서비스를 한 번에 실행합니다.

```bash
docker compose up --build -d
```

### 3. 접속
- **웹 UI**: [http://localhost:5173](http://localhost:5173) (또는 80번 포트 설정에 따름)
- **API 문서**: [http://localhost:8000/docs](http://localhost:8000/docs)
