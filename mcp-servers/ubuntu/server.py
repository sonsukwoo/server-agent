"""Ubuntu MCP 서버 - stdio 버전"""
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
        command = arguments.get("command", "")
        
        if not command:
            return [TextContent(type="text", text="오류: command를 입력해주세요")]
        
        for keyword in DANGER_KEYWORDS:
            if keyword in command:
                return [TextContent(type="text", text="danger")]
        
        for keyword in CAUTION_KEYWORDS:
            if keyword in command:
                return [TextContent(type="text", text="caution")]
        
        return [TextContent(type="text", text="safe")]
    
    elif name == "execute_command":
        command = arguments.get("command", "")
        
        if not command:
            return [TextContent(type="text", text="오류: command를 입력해주세요")]
        
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
        except subprocess.TimeoutExpired:
            return [TextContent(type="text", text="오류: 명령어 실행 시간 초과 (10초)")]
        except Exception as e:
            return [TextContent(type="text", text=f"오류: {str(e)}")]
    
    elif name == "get_system_status":
        try:
            cpu_result = subprocess.run(
                "top -bn1 | grep 'Cpu(s)' | awk '{print $2}'",
                shell=True, capture_output=True, text=True
            )
            
            mem_result = subprocess.run(
                "free | grep Mem | awk '{print ($3/$2) * 100.0}'",
                shell=True, capture_output=True, text=True
            )
            
            disk_result = subprocess.run(
                "df -h / | tail -1 | awk '{print $5}'",
                shell=True, capture_output=True, text=True
            )
            
            status = {
                "cpu_percent": cpu_result.stdout.strip() or "N/A",
                "memory_percent": mem_result.stdout.strip() or "N/A",
                "disk_percent": disk_result.stdout.strip() or "N/A"
            }
            
            return [TextContent(type="text", text=json.dumps(status, ensure_ascii=False))]
        except Exception as e:
            return [TextContent(type="text", text=f"오류: {str(e)}")]
    
    else:
        return [TextContent(type="text", text="알 수 없는 Tool입니다")]

async def main():
    """MCP 서버 실행 (stdio)"""
    async with stdio_server() as (read, write):
        await app.run(read, write, app.create_initialization_options())

if __name__ == "__main__":
    import asyncio
    asyncio.run(main())
