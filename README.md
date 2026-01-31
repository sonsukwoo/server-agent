# Server Agent

LangGraph + MCP 기반 AI 에이전트로 자연어로 서버 정보 조회 및 시스템 명령 실행
(MCP 통신 방식: `stdio` - 하위 프로세스 직접 실행)

## 프로젝트 구조

```
server-agent/
├── backend/             # Main Application & MCP Servers
│   ├── src/api/         # FastAPI Server
│   ├── src/agents/      # LangGraph Agents
│   ├── mcp/             # MCP Servers (Postgres, Ubuntu)
│   └── schema/          # DB Schema Cache
├── frontend/            # Frontend Application (Planned)
└── docker-compose.yml   # Orchestration
```

## 설치

```bash
pip install -r requirements.txt
```

## 실행

```bash
docker-compose up
```
