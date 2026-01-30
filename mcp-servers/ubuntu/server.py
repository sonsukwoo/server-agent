"""Ubuntu MCP 서버 - 시스템 명령 Tool 제공"""
from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import Tool, TextContent
import subprocess
import json

app = Server("ubuntu-tools")

# 위험 명령어 키워드
DANGER_KEYWORDS = ["rm -rf", "shutdown", "reboot", "mkfs", "dd if=", "> /dev/"]
CAUTION_KEYWORDS = ["restart", "stop", "kill", "docker stop"]

@app.list_tools()
async def list_tools() -> list[Tool]:
    """사용 가능한 Tool 목록"""
    return [
        Tool(
            name="classify_risk",
            description="명령어의 위험도를 분류합니다 (safe/caution/danger)",
            inputSchema={
                "type": "object",
                "properties": {
                    "command": {"type": "string", "description": "분류할 명령어"}
                },
                "required": ["command"]
            }
        ),
        Tool(
            name="execute_command",
            description="Ubuntu 시스템에서 쉘 명령어를 실행합니다",
            inputSchema={
                "type": "object",
                "properties": {
                    "command": {"type": "string", "description": "실행할 명령어"}
                },
                "required": ["command"]
            }
        ),
        Tool(
            name="get_system_status",
            description="현재 시스템 상태(CPU, RAM, 디스크)를 요약합니다",
            inputSchema={"type": "object", "properties": {}}
        ),
    ]

@app.call_tool()
async def call_tool(name: str, arguments: dict) -> list[TextContent]:
    """Tool 실행"""
    if name == "classify_risk":
        command = arguments["command"]
        
        # 위험도 분류
        for keyword in DANGER_KEYWORDS:
            if keyword in command:
                return [TextContent(type="text", text="danger")]
        
        for keyword in CAUTION_KEYWORDS:
            if keyword in command:
                return [TextContent(type="text", text="caution")]
        
        return [TextContent(type="text", text="safe")]
    
    elif name == "execute_command":
        command = arguments["command"]
        
        try:
            result = subprocess.run(
                command,
                shell=True,
                capture_output=True,
                text=True,
                timeout=10
            )
            
            output = {
                "stdout": result.stdout,
                "stderr": result.stderr,
                "returncode": result.returncode
            }
            
            return [TextContent(type="text", text=json.dumps(output, ensure_ascii=False))]
        except Exception as e:
            return [TextContent(type="text", text=f"오류: {str(e)}")]
    
    elif name == "get_system_status":
        # 간단한 시스템 상태 조회
        try:
            cpu = subprocess.run("top -bn1 | grep 'Cpu(s)' | awk '{print $2}'", shell=True, capture_output=True, text=True)
            mem = subprocess.run("free | grep Mem | awk '{print ($3/$2) * 100.0}'", shell=True, capture_output=True, text=True)
            
            status = {
                "cpu": cpu.stdout.strip(),
                "memory": mem.stdout.strip()
            }
            
            return [TextContent(type="text", text=json.dumps(status, ensure_ascii=False))]
        except Exception as e:
            return [TextContent(type="text", text=f"오류: {str(e)}")]
    
    return [TextContent(type="text", text="알 수 없는 Tool입니다")]

async def main():
    """MCP 서버 실행"""
    async with stdio_server() as (read, write):
        await app.run(read, write, app.create_initialization_options())

if __name__ == "__main__":
    import asyncio
    asyncio.run(main())
