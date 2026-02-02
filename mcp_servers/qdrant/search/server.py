"""Qdrant MCP 서버 - 테이블 검색 전용"""
import os
import json
from urllib import request
from typing import Any

from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import Tool, TextContent
from qdrant_client import QdrantClient
from langchain_openai import OpenAIEmbeddings

app = Server("qdrant-tools")

# 설정 (환경변수)
QDRANT_URL = os.getenv("QDRANT_URL", "http://localhost:6333")
QDRANT_API_KEY = os.getenv("QDRANT_API_KEY", "")
QDRANT_COLLECTION = os.getenv("QDRANT_COLLECTION", "table_index")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")

# 임베딩 모델
embeddings = OpenAIEmbeddings(model="text-embedding-3-small", api_key=OPENAI_API_KEY)

# 클라이언트
_client = None

def get_client() -> QdrantClient:
    global _client
    if _client is None:
        _client = QdrantClient(
            url=QDRANT_URL,
            api_key=QDRANT_API_KEY if QDRANT_API_KEY else None,
        )
    return _client

def embed_text(text: str) -> list[float]:
    """텍스트 임베딩"""
    return embeddings.embed_query(text)

def _search_qdrant(query: str, top_k: int) -> list[dict]:
    """Qdrant 검색 수행"""
    client = get_client()
    query_vector = embed_text(query)
    
    # 1. client.search 사용 시도
    if hasattr(client, "search"):
        results = client.search(
            collection_name=QDRANT_COLLECTION,
            query_vector=query_vector,
            limit=top_k,
        )
    else:
        # 2. REST API 직접 호출 (Fallback)
        url = f"{QDRANT_URL}/collections/{QDRANT_COLLECTION}/points/search"
        payload = json.dumps({
            "vector": query_vector,
            "limit": top_k,
            "with_payload": True,
        }).encode("utf-8")
        headers = {"Content-Type": "application/json"}
        if QDRANT_API_KEY:
            headers["api-key"] = QDRANT_API_KEY
        req = request.Request(url, data=payload, headers=headers, method="POST")
        with request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        results = data.get("result", [])

    # 결과 포맷팅
    candidates = []
    for hit in results:
        # hit 객체 속성 또는 dict 접근 처리
        payload = getattr(hit, "payload", None) or (hit.get("payload", {}) if isinstance(hit, dict) else {}) or {}
        score = getattr(hit, "score", None)
        if score is None and isinstance(hit, dict):
            score = hit.get("score")
            
        schema = payload.get("schema", "")
        table_name = payload.get("table_name", "")
        full_name = f"{schema}.{table_name}" if schema and table_name else table_name

        raw_columns = payload.get("columns", [])
        columns = [
            {
                "name": col.get("name", ""),
                "type": col.get("type", ""),
                "description": col.get("description", ""),
                "role": col.get("role", ""),
                "category": col.get("category", ""),
            }
            for col in raw_columns
            if col.get("visible_to_llm") is True
        ]

        candidates.append({
            "table_name": full_name,
            "description": payload.get("description", ""),
            "primary_time_col": payload.get("primary_time_col", ""),
            "join_keys": payload.get("join_keys", []),
            "columns": columns,
            "score": round(score or 0.0, 4),
        })
    
    return candidates

def _error(message: str) -> list[TextContent]:
    return [TextContent(type="text", text=message)]

@app.list_tools()
async def list_tools() -> list[Tool]:
    return [
        Tool(
            name="search_tables",
            description="사용자 질문과 관련된 테이블을 검색합니다",
            inputSchema={
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "사용자 질문"},
                    "top_k": {"type": "integer", "description": "검색할 후보 수", "default": 5}
                },
                "required": ["query"]
            }
        )
    ]

@app.call_tool()
async def call_tool(name: str, arguments: dict) -> list[TextContent]:
    if name == "search_tables":
        query = arguments.get("query", "")
        top_k = arguments.get("top_k", 5)
        
        if not query:
            return _error("오류: query가 비어있습니다")
            
        try:
            candidates = _search_qdrant(query, top_k)
            return [TextContent(type="text", text=json.dumps(candidates, ensure_ascii=False))]
        except Exception as e:
            return _error(f"검색 실패: {str(e)}")
    
    return _error(f"알 수 없는 도구: {name}")

# HTTP Adapter
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
import uvicorn

http_app = FastAPI()

@http_app.get("/tools")
async def handle_list_tools():
    tools = await list_tools()
    return [{"name": t.name, "description": t.description, "inputSchema": t.inputSchema} for t in tools]

class CallToolRequest(BaseModel):
    name: str
    arguments: dict = {}

@http_app.post("/call")
async def handle_call_tool(req: CallToolRequest):
    try:
        result = await call_tool(req.name, req.arguments)
        return [{"type": c.type, "text": c.text} for c in result]
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

async def main():
    async with stdio_server() as (read, write):
        await app.run(read, write, app.create_initialization_options())

if __name__ == "__main__":
    import asyncio
    import sys
    
    # "http" 인자가 있으면 uvicorn 실행 (개발/테스트용)
    if len(sys.argv) > 1 and sys.argv[1] == "http":
        port = int(os.getenv("PORT", 8000))
        uvicorn.run(http_app, host="0.0.0.0", port=port)
    else:
        asyncio.run(main())
