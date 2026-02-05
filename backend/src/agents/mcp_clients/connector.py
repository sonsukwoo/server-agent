"""MCP 클라이언트 연결 (HTTP/Stdio) 및 도구 실행 래퍼."""

import os
import httpx
from pathlib import Path
from types import SimpleNamespace
from dotenv import load_dotenv
from contextlib import asynccontextmanager
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from config.settings import settings

load_dotenv()

MCP_SERVERS_DIR = Path(settings.mcp_servers_dir)

class MCPHttpWrapper:
    """HTTP 전송을 사용하는 MCP 클라이언트 래퍼."""
    
    def __init__(self, base_url: str):
        """기본 URL 및 비동기 HTTP 클라이언트 설정."""
        self.base_url = base_url.rstrip("/")
        self.client = httpx.AsyncClient(timeout=300.0)

    async def __aenter__(self):
        """컨텍스트 진입 시 자신 반환."""
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """컨텍스트 종료 시 HTTP 클라이언트 닫기."""
        await self.client.aclose()

    async def list_tools(self) -> list:
        """가용 도구 목록 조회 및 객체 매핑."""
        resp = await self.client.get(f"{self.base_url}/tools")
        resp.raise_for_status()
        tools_data = resp.json()
        return [SimpleNamespace(**t) for t in tools_data]

    async def call_tool(self, name: str, arguments: dict = None) -> str:
        """도구 실행 및 결과 반환."""
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
    """Stdio 전송을 사용하는 MCP 클라이언트 래퍼."""
    
    def __init__(self, session: ClientSession):
        """MCP 세션 객체 저장."""
        self.session = session
    
    async def list_tools(self) -> list:
        """가용 도구 목록 조회."""
        result = await self.session.list_tools()
        return result.tools
    
    async def call_tool(self, name: str, arguments: dict = None) -> str:
        """도구 실행 및 결과 텍스트 반환."""
        if arguments is None:
            arguments = {}
        
        result = await self.session.call_tool(name, arguments)
        
        if result.content:
            return result.content[0].text
        return ""


@asynccontextmanager
async def create_mcp_client(server_name: str):
    """전송 방식(HTTP/Stdio)에 따른 MCP 클라이언트 생성 및 반환."""
    
    # 1. HTTP 전송 방식
    if settings.mcp_transport == "http":
        url_map = {
            "postgres": settings.mcp_postgres_url,
            "qdrant": settings.mcp_qdrant_url,
            "qdrant/search": settings.mcp_qdrant_url,
            "qdrant/embeddings": settings.mcp_qdrant_url,
        }
        base_url = url_map.get(server_name)
        if not base_url:
            raise ValueError(f"HTTP용 알 수 없는 MCP 서버: {server_name}")
            
        async with MCPHttpWrapper(base_url) as client:
            yield client
            
    # 2. Stdio 전송 방식
    else:
        server_path = MCP_SERVERS_DIR / server_name / "server.py"
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


@asynccontextmanager
async def postgres_client():
    """PostgreSQL MCP 연결 컨텍스트."""
    async with create_mcp_client("postgres") as client:
        yield client


@asynccontextmanager
async def qdrant_search_client():
    """Qdrant 검색 MCP 연결 컨텍스트."""
    async with create_mcp_client("qdrant") as client:
        yield client


@asynccontextmanager
async def qdrant_embeddings_client():
    """Qdrant 임베딩 MCP 연결 컨텍스트."""
    async with create_mcp_client("qdrant") as client:
        yield client
