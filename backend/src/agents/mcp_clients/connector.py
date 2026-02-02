"""MCP 클라이언트 - subprocess(stdio) 또는 HTTP로 통신"""
import os
from pathlib import Path
from dotenv import load_dotenv

# .env 로드 (모듈 최상단에서 실행)
load_dotenv()

import asyncio
import json
import httpx
from contextlib import asynccontextmanager
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from config.settings import settings

# MCP 서버 경로 (settings 이용)
MCP_SERVERS_DIR = Path(settings.mcp_servers_dir)


class MCPHttpWrapper:
    """HTTP 기반 MCP 클라이언트 래퍼"""
    def __init__(self, base_url: str):
        self.base_url = base_url.rstrip("/")
        self.client = httpx.AsyncClient(timeout=30.0)

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        await self.client.aclose()

    async def list_tools(self) -> list:
        resp = await self.client.get(f"{self.base_url}/tools")
        resp.raise_for_status()
        # MCP Tool 객체 구조(duck typing)로 반환
        tools_data = resp.json()
        # 간단히 속성 접근 가능하도록 SimpleNamespace 또는 딕셔너리 래퍼 사용
        # 기존 코드 호환성을 위해 속성 접근이 가능한 객체 리스트로 변환
        from types import SimpleNamespace
        return [SimpleNamespace(**t) for t in tools_data]

    async def call_tool(self, name: str, arguments: dict = None) -> str:
        if arguments is None:
            arguments = {}
        payload = {"name": name, "arguments": arguments}
        resp = await self.client.post(f"{self.base_url}/call", json=payload)
        resp.raise_for_status()
        content_list = resp.json()
        if content_list:
            return content_list[0].get("text", "")
        return ""


class MCPClientWrapper:
    """Stdio 기반 MCP 세션 래퍼"""
    
    def __init__(self, session: ClientSession):
        self.session = session
    
    async def list_tools(self) -> list:
        """사용 가능한 Tool 목록 조회"""
        result = await self.session.list_tools()
        return result.tools
    
    async def call_tool(self, name: str, arguments: dict = None) -> str:
        """Tool 호출"""
        if arguments is None:
            arguments = {}
        
        result = await self.session.call_tool(name, arguments)
        
        if result.content:
            return result.content[0].text
        return ""


@asynccontextmanager
async def create_mcp_client(server_name: str):
    """
    MCP 서버에 연결하는 컨텍스트 매니저 (Transport 분기 처리)
    """
    
    # 1. HTTP Transport
    if settings.mcp_transport == "http":
        url_map = {
            "postgres": settings.mcp_postgres_url,
            "ubuntu": settings.mcp_ubuntu_url,
            "qdrant": settings.mcp_qdrant_url,
            "qdrant/search": settings.mcp_qdrant_url,
            "qdrant/embeddings": settings.mcp_qdrant_url,
        }
        base_url = url_map.get(server_name)
        if not base_url:
            raise ValueError(f"Unknown MCP server name for HTTP: {server_name}")
            
        async with MCPHttpWrapper(base_url) as client:
            yield client
            
    # 2. Stdio Transport (기존)
    else:
        server_path = MCP_SERVERS_DIR / server_name / "server.py"
        
        # 환경변수를 서버 프로세스에 전달
        env = dict(os.environ)
        
        server_params = StdioServerParameters(
            command="python",
            args=[str(server_path)],
            env=env
        )
        
        async with stdio_client(server_params) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                yield MCPClientWrapper(session)


# 편의 함수
@asynccontextmanager
async def postgres_client():
    """PostgreSQL MCP 클라이언트"""
    async with create_mcp_client("postgres") as client:
        yield client


@asynccontextmanager
async def ubuntu_client():
    """Ubuntu MCP 클라이언트"""
    async with create_mcp_client("ubuntu") as client:
        yield client


@asynccontextmanager
async def qdrant_search_client():
    """Qdrant 검색 (Search) MCP 클라이언트"""
    async with create_mcp_client("qdrant") as client:
        yield client


@asynccontextmanager
async def qdrant_embeddings_client():
    """Qdrant 임베딩 (Embeddings) MCP 클라이언트"""
    async with create_mcp_client("qdrant") as client:
        yield client
