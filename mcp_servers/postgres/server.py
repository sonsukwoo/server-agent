"""PostgreSQL MCP 서버 - execute_sql 전용"""
from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import Tool, TextContent
import json
import psycopg2
import os
from typing import Any, Iterable

app = Server("postgres-tools")

# DB 연결 설정
DB_CONFIG = {
    "host": os.getenv("DB_HOST", "localhost"),
    "port": int(os.getenv("DB_PORT", "5432")),
    "database": os.getenv("DB_NAME", "server_agent_db"),
    "user": os.getenv("DB_USER"),
    "password": os.getenv("DB_PASSWORD"),
}

def _error(message: str) -> list[TextContent]:
    """표준 에러 응답 생성"""
    return [TextContent(type="text", text=message)]


def _execute_select(query: str) -> str:
    """SELECT 쿼리를 실행하고 JSON 문자열 결과를 반환"""
    conn = psycopg2.connect(**DB_CONFIG)
    cursor = conn.cursor()
    try:
        cursor.execute(query)
        results = cursor.fetchall()
        columns = [desc[0] for desc in cursor.description]
        result_list = [dict(zip(columns, row)) for row in results]
        return json.dumps(result_list, default=str, ensure_ascii=False)
    finally:
        cursor.close()
        conn.close()


def _is_select_query(query: str) -> bool:
    """SELECT 쿼리인지 확인 (CTE 허용)"""
    upper_query = query.strip().upper()
    return upper_query.startswith("SELECT") or upper_query.startswith("WITH")


@app.list_tools()
async def list_tools() -> list[Tool]:
    """사용 가능한 Tool 목록"""
    return [
        Tool(
            name="execute_sql",
            description="SELECT 쿼리를 실행하고 결과를 반환합니다",
            inputSchema={
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "실행할 SQL 쿼리"}
                },
                "required": ["query"]
            }
        ),
    ]

@app.call_tool()
async def call_tool(name: str, arguments: dict) -> list[TextContent]:
    """Tool 실행"""
    
    if name == "execute_sql":
        query = arguments.get("query", "")
        
        if not query:
            return _error("오류: query를 입력해주세요")
        
        if not _is_select_query(query):
            return _error("오류: SELECT 쿼리만 실행 가능합니다")
        
        try:
            result_json = _execute_select(query)
            return [TextContent(type="text", text=result_json)]
        except Exception as e:
            return _error(f"오류: {str(e)}")
    
    else:
        return _error("알 수 없는 Tool입니다")

# HTTP Adapter
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
import uvicorn
import os

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
    """MCP 서버 실행 (stdio)"""
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
