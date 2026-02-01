"""MCP 클라이언트 - subprocess로 MCP 서버와 통신"""
import os
from pathlib import Path
from dotenv import load_dotenv

# .env 로드 (모듈 최상단에서 실행)
load_dotenv()

import asyncio
import json
from contextlib import asynccontextmanager
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from config.settings import settings

# MCP 서버 경로 (settings 이용)
MCP_SERVERS_DIR = Path(settings.mcp_servers_dir)


@asynccontextmanager
async def create_mcp_client(server_name: str):
    """
    MCP 서버에 연결하는 컨텍스트 매니저
    
    Args:
        server_name: "postgres" 또는 "ubuntu"
    
    Usage:
        async with create_mcp_client("postgres") as client:
            tools = await client.list_tools()
            result = await client.call_tool("execute_sql", {"query": "SELECT 1"})
    """
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


class MCPClientWrapper:
    """MCP 세션 래퍼"""
    
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


# 테스트용
async def test_postgres():
    """PostgreSQL MCP 클라이언트 테스트"""
    print("=" * 60)
    print("PostgreSQL MCP 클라이언트 테스트")
    print("=" * 60)
    print(f"MCP_SERVERS_DIR: {MCP_SERVERS_DIR}")
    
    async with postgres_client() as client:
        # Tool 목록 조회
        tools = await client.list_tools()
        print(f"\n✅ Tool 개수: {len(tools)}개")
        for tool in tools:
            print(f"  - {tool.name}: {tool.description}")
        
        # execute_sql 테스트
        print("\n📋 execute_sql 테스트 (SELECT 1)...")
        result = await client.call_tool("execute_sql", {"query": "SELECT 1 AS test"})
        print(f"✅ 결과: {result}")
    
    print("\n" + "=" * 60)
    print("✅ 테스트 완료!")
    print("=" * 60)


if __name__ == "__main__":
    asyncio.run(test_postgres())

