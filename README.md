# Server Agent

LangGraph + MCP + Qdrant 기반 AI 에이전트로 자연어로 서버 정보 조회 및 시스템 명령 실행

## 아키텍처

- **Text-to-SQL**: Qdrant 벡터 검색으로 관련 테이블 탐색 → LLM 리랭크 → SQL 생성 → MCP를 통한 실행
- **MCP 서버**: `execute_sql` 전용 (스키마 조회는 Qdrant로 이관)

## 프로젝트 구조

```
server-agent/
├── backend/
│   ├── src/api/             # FastAPI Server
│   ├── src/agents/          # LangGraph Agents
│   │   └── tools/
│   │       ├── connector.py    # MCP Client
│   │       └── qdrant_client.py # Qdrant 검색 유틸
│   ├── mcp_servers/         # MCP Servers (Postgres, Ubuntu)
│   └── scripts/
│       └── init_qdrant.py   # Qdrant 초기화 스크립트
└── docker-compose.yml
```

## 환경 변수

```bash
# .env
QDRANT_URL=http://192.168.219.100:6333
QDRANT_API_KEY=        # 선택사항
QDRANT_COLLECTION=table_index
```

## 설치 및 실행

```bash
# 1. 의존성 설치
pip install -r requirements.txt

# 2. Qdrant 초기화 (테이블 정보 업로드)
python scripts/init_qdrant.py

# 3. 서버 실행
docker-compose up
```

