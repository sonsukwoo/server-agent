"""PostgreSQL MCP 서버 - DB 조회 Tool 제공"""
from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import Tool, TextContent
import json
import psycopg2
import os
from pathlib import Path

app = Server("postgres-tools")

# 스키마 디렉토리 경로
SCHEMA_DIR = Path(os.getenv("SCHEMA_DIR", "/app/schema"))

# DB 연결 설정
DB_CONFIG = {
    "host": os.getenv("DB_HOST", "localhost"),
    "port": int(os.getenv("DB_PORT", "5432")),
    "database": os.getenv("DB_NAME", "server_agent_db"),
    "user": os.getenv("DB_USER"),
    "password": os.getenv("DB_PASSWORD"),
}

@app.list_tools()
async def list_tools() -> list[Tool]:
    """사용 가능한 Tool 목록"""
    return [
        Tool(
            name="get_table_list",
            description="사용 가능한 모든 테이블 목록과 설명을 반환합니다",
            inputSchema={"type": "object", "properties": {}}
        ),
        Tool(
            name="get_table_schema",
            description="특정 테이블의 컬럼 정보를 반환합니다",
            inputSchema={
                "type": "object",
                "properties": {
                    "table_name": {"type": "string", "description": "테이블명 (예: ops_metrics.metrics_system)"}
                },
                "required": ["table_name"]
            }
        ),
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
    
    if name == "get_table_list":
        try:
            all_tables = []
            
            for json_file in SCHEMA_DIR.glob("*.json"):
                with open(json_file, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                
                for table in data.get("tables", []):
                    all_tables.append({
                        "name": table["full_name"],
                        "description": table["description"]
                    })
            
            if not all_tables:
                return [TextContent(type="text", text="테이블을 찾을 수 없습니다")]
            
            result = json.dumps(all_tables, ensure_ascii=False, indent=2)
            return [TextContent(type="text", text=result)]
        
        except Exception as e:
            return [TextContent(type="text", text=f"오류: {str(e)}")]
    
    elif name == "get_table_schema":
        table_name = arguments.get("table_name", "")
        
        if not table_name:
            return [TextContent(type="text", text="오류: table_name을 입력해주세요")]
        
        try:
            # 스키마명과 테이블명 분리
            if "." in table_name:
                schema_name, tbl_name = table_name.split(".", 1)
            else:
                return [TextContent(type="text", text="오류: 테이블명은 'schema.table' 형식이어야 합니다")]
            
            # 해당 스키마의 JSON 파일 찾기
            json_file = SCHEMA_DIR / f"{schema_name}.json"
            
            if not json_file.exists():
                return [TextContent(type="text", text=f"오류: {schema_name} 스키마를 찾을 수 없습니다")]
            
            # JSON 파일 읽기
            with open(json_file, 'r', encoding='utf-8') as f:
                data = json.load(f)
            
            # 테이블 찾기
            for table in data.get("tables", []):
                if table["name"] == tbl_name or table["full_name"] == table_name:
                    result = {
                        "table_name": table["full_name"],
                        "description": table["description"],
                        "columns": table["columns"]
                    }
                    return [TextContent(type="text", text=json.dumps(result, ensure_ascii=False, indent=2))]
            
            return [TextContent(type="text", text=f"오류: {table_name} 테이블을 찾을 수 없습니다")]
        
        except Exception as e:
            return [TextContent(type="text", text=f"오류: {str(e)}")]
    
    elif name == "execute_sql":
        query = arguments["query"]
        
        # SELECT만 허용
        if not query.strip().upper().startswith("SELECT"):
            return [TextContent(type="text", text="오류: SELECT 쿼리만 실행 가능합니다")]
        
        try:
            conn = psycopg2.connect(**DB_CONFIG)
            cursor = conn.cursor()
            cursor.execute(query)
            results = cursor.fetchall()
            columns = [desc[0] for desc in cursor.description]
            
            # 결과를 JSON으로 변환
            result_list = [dict(zip(columns, row)) for row in results]
            
            cursor.close()
            conn.close()
            
            return [TextContent(type="text", text=json.dumps(result_list, default=str, ensure_ascii=False))]
        except Exception as e:
            return [TextContent(type="text", text=f"오류: {str(e)}")]
    
    else:
        return [TextContent(type="text", text="알 수 없는 Tool입니다")]

async def main():
    """MCP 서버 실행"""
    async with stdio_server() as (read, write):
        await app.run(read, write, app.create_initialization_options())

if __name__ == "__main__":
    import asyncio
    asyncio.run(main())
