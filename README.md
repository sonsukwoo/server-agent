# Server Agent

LangGraph + MCP 기반 AI 에이전트로 자연어로 서버 정보 조회 및 시스템 명령 실행

## 프로젝트 구조

```
server-agent/
├── src/agents/          # LangGraph 에이전트
├── mcp-servers/         # MCP Tool 서버들
├── src/middleware/      # 안전장치 레이어
└── schema/              # DB 스키마 캐시
```

## 설치

```bash
pip install -r requirements.txt
```

## 실행

```bash
docker-compose up
```
