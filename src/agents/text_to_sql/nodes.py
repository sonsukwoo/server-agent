"""Text-to-SQL 에이전트 노드 함수"""
import json
import os
import re
from datetime import datetime
from zoneinfo import ZoneInfo

from dotenv import load_dotenv
load_dotenv(override=True)  # .env 파일 로드 (환경변수 덮어쓰기)

from langchain_openai import ChatOpenAI
from langchain_core.messages import SystemMessage, HumanMessage

from .state import TextToSQLState
from .prompts import (
    PARSE_REQUEST_SYSTEM, PARSE_REQUEST_USER,
    SELECT_TABLE_SYSTEM, SELECT_TABLE_USER,
    GENERATE_SQL_SYSTEM, GENERATE_SQL_USER,
    VALIDATE_RESULT_SYSTEM, VALIDATE_RESULT_USER,
    GENERATE_REPORT_SYSTEM, GENERATE_REPORT_USER,
)
from src.mcp_client.connector import postgres_client

# 타임존 설정 (한국 기준)
TIMEZONE = os.getenv("TZ", "Asia/Seoul")

# LLM 인스턴스 (나중에 변경 가능)
llm_fast = ChatOpenAI(model="gpt-4o-mini", temperature=0)  # 파싱/보고서
llm_smart = ChatOpenAI(model="gpt-4o", temperature=0)       # SQL 생성/검증


# ═══════════════════════════════════════════════════════════════
# 유틸리티 함수
# ═══════════════════════════════════════════════════════════════

def get_current_time() -> str:
    """현재 시간 ISO 8601 형식 반환"""
    return datetime.now(ZoneInfo(TIMEZONE)).isoformat()


def get_now() -> datetime:
    """현재 시간 datetime 객체 반환"""
    return datetime.now(ZoneInfo(TIMEZONE))


def normalize_sql(sql: str) -> str:
    """SQL 안전 규칙 강제 적용"""
    sql = sql.strip()
    
    # 마크다운 코드 블록 제거 (```sql ... ``` 형식)
    match = re.search(r'```(?:sql)?\s*([\s\S]*?)```', sql)
    if match:
        sql = match.group(1).strip()
    
    # 1. SELECT-only 검증
    if not sql.upper().startswith("SELECT"):
        raise ValueError(f"SELECT 쿼리만 허용됩니다. 받은 쿼리: {sql[:50]}...")
    
    # 2. 위험한 키워드 차단
    dangerous = ["DROP", "DELETE", "UPDATE", "INSERT", "TRUNCATE", "ALTER"]
    for keyword in dangerous:
        if keyword in sql.upper():
            raise ValueError(f"위험한 키워드 포함: {keyword}")
    
    # 3. LIMIT 강제 추가 (없으면 LIMIT 100)
    if "LIMIT" not in sql.upper():
        sql = sql.rstrip(";") + " LIMIT 100"
    
    return sql


def parse_json_from_llm(text: str) -> tuple[dict | None, str | None]:
    """
    LLM 응답에서 JSON 안전하게 추출
    
    Returns:
        (parsed_dict, error_message) - 성공 시 (dict, None), 실패 시 (None, error)
    """
    try:
        # ```json ... ``` 블록 추출
        match = re.search(r'```(?:json)?\s*([\s\S]*?)```', text)
        if match:
            text = match.group(1)
        
        # JSON 파싱
        return json.loads(text.strip()), None
    except json.JSONDecodeError as e:
        return None, f"JSON 파싱 실패: {e}. 원본: {text[:100]}..."


# ═══════════════════════════════════════════════════════════════
# Node 1: parse_request
# ═══════════════════════════════════════════════════════════════

async def parse_request(state: TextToSQLState) -> dict:
    """자연어 질문을 구조화된 JSON으로 변환"""
    messages = [
        SystemMessage(content=PARSE_REQUEST_SYSTEM),
        HumanMessage(content=PARSE_REQUEST_USER.format(
            current_time=get_current_time(),
            user_question=state["user_question"]
        ))
    ]
    
    response = await llm_fast.ainvoke(messages)
    parsed, error = parse_json_from_llm(response.content)
    
    # JSON 파싱 실패 시 에러 상태 반환
    if error:
        return {
            "parsed_request": {},
            "is_request_valid": False,
            "request_error": error
        }
    
    return {"parsed_request": parsed}


# ═══════════════════════════════════════════════════════════════
# Node 2: validate_request
# ═══════════════════════════════════════════════════════════════

async def validate_request(state: TextToSQLState) -> dict:
    """요구사항 검증 (미들웨어)"""
    # parse_request에서 이미 실패한 경우 그대로 전달
    if state.get("is_request_valid") is False:
        return {
            "is_request_valid": False,
            "request_error": state.get("request_error", "알 수 없는 오류")
        }
    
    parsed = state.get("parsed_request", {})
    
    # 필수 필드 확인
    if not parsed.get("intent"):
        return {
            "is_request_valid": False,
            "request_error": "intent 필드가 없습니다"
        }
    
    # 시간 범위 검증
    time_range = parsed.get("time_range", {})
    if time_range:
        start = time_range.get("start")
        end = time_range.get("end")
        
        if start and end:
            try:
                start_dt = datetime.fromisoformat(start.replace("Z", "+00:00"))
                end_dt = datetime.fromisoformat(end.replace("Z", "+00:00"))
                now = get_now()
                
                # 시작 > 종료 검증
                if start_dt > end_dt:
                    return {
                        "is_request_valid": False,
                        "request_error": "시작 시간이 종료 시간보다 늦습니다"
                    }
                
                # 미래 시점 검증 (end가 현재보다 1시간 이상 미래면 경고)
                if end_dt > now.replace(tzinfo=end_dt.tzinfo):
                    # 약간의 여유 허용 (1시간)
                    from datetime import timedelta
                    if end_dt > now.replace(tzinfo=end_dt.tzinfo) + timedelta(hours=1):
                        return {
                            "is_request_valid": False,
                            "request_error": "미래 데이터는 조회할 수 없습니다"
                        }
                        
            except ValueError as e:
                return {
                    "is_request_valid": False,
                    "request_error": f"시간 형식 오류: {e}"
                }
    
    return {
        "is_request_valid": True,
        "request_error": ""
    }


# ═══════════════════════════════════════════════════════════════
# Node 3: select_table
# ═══════════════════════════════════════════════════════════════

async def select_table(state: TextToSQLState) -> dict:
    """적합한 테이블 선택"""
    parsed = state["parsed_request"]
    
    # MCP로 테이블 목록 조회
    async with postgres_client() as client:
        result = await client.call_tool("get_table_list")
        table_list = json.loads(result)
    
    # 테이블 목록 포맷팅
    table_str = "\n".join([
        f"- {t['name']}: {t.get('description', 'N/A')}"
        for t in table_list
    ])
    
    messages = [
        SystemMessage(content=SELECT_TABLE_SYSTEM),
        HumanMessage(content=SELECT_TABLE_USER.format(
            intent=parsed.get("intent", ""),
            metric=parsed.get("metric", "N/A"),
            condition=parsed.get("condition", "N/A"),
            table_list=table_str
        ))
    ]
    
    response = await llm_fast.ainvoke(messages)
    selected = response.content.strip()
    
    # NONE 반환 시 테이블 선택 실패
    if selected.upper() == "NONE" or not selected:
        return {
            "table_list": table_list,
            "selected_table": "",
            "is_table_valid": False,
            "table_error": "요청에 적합한 테이블을 찾지 못했습니다. 질문을 더 구체적으로 해주세요."
        }
    
    return {
        "table_list": table_list,
        "selected_table": selected,
        "is_table_valid": True,
        "table_error": ""
    }


# ═══════════════════════════════════════════════════════════════
# Node 4: generate_sql
# ═══════════════════════════════════════════════════════════════

async def generate_sql(state: TextToSQLState) -> dict:
    """SQL 쿼리 생성"""
    parsed = state["parsed_request"]
    selected_table = state["selected_table"]
    validation_reason = state.get("validation_reason", "")
    
    # MCP로 테이블 스키마 조회
    async with postgres_client() as client:
        result = await client.call_tool("get_table_schema", {"table_name": selected_table})
        schema = json.loads(result)
    
    # 컬럼 정보 포맷팅
    columns_str = "\n".join([
        f"- {col['name']} ({col['type']}): {col.get('description', 'N/A')}"
        for col in schema.get("columns", [])
    ])
    
    # 시간 범위
    time_range = parsed.get("time_range", {})
    
    messages = [
        SystemMessage(content=GENERATE_SQL_SYSTEM),
        HumanMessage(content=GENERATE_SQL_USER.format(
            intent=parsed.get("intent", ""),
            time_start=time_range.get("start", "N/A"),
            time_end=time_range.get("end", "N/A"),
            metric=parsed.get("metric", "N/A"),
            condition=parsed.get("condition", "N/A"),
            table_name=selected_table,
            columns=columns_str,
            validation_reason=validation_reason or "없음"
        ))
    ]
    
    response = await llm_smart.ainvoke(messages)
    sql = response.content.strip()
    
    # SQL 정규화 (안전 규칙 적용)
    sql = normalize_sql(sql)
    
    return {
        "table_schema": schema,
        "generated_sql": sql
    }


# ═══════════════════════════════════════════════════════════════
# Node 5: execute_sql
# ═══════════════════════════════════════════════════════════════

async def execute_sql(state: TextToSQLState) -> dict:
    """SQL 실행"""
    sql = state["generated_sql"]
    
    try:
        async with postgres_client() as client:
            result = await client.call_tool("execute_sql", {"query": sql})
            sql_result = json.loads(result)
        
        return {
            "sql_result": sql_result,
            "sql_error": ""
        }
    except Exception as e:
        return {
            "sql_result": [],
            "sql_error": str(e)
        }


# ═══════════════════════════════════════════════════════════════
# Node 6: validate_result
# ═══════════════════════════════════════════════════════════════

async def validate_result(state: TextToSQLState) -> dict:
    """결과 검증 (자기채점)"""
    # SQL 에러가 있으면 바로 INVALID
    if state.get("sql_error"):
        return {
            "is_valid": False,
            "validation_reason": f"SQL 에러: {state['sql_error']}",
            "retry_count": state.get("retry_count", 0) + 1
        }
    
    messages = [
        SystemMessage(content=VALIDATE_RESULT_SYSTEM),
        HumanMessage(content=VALIDATE_RESULT_USER.format(
            parsed_request=json.dumps(state["parsed_request"], ensure_ascii=False, indent=2),
            generated_sql=state["generated_sql"],
            sql_result=json.dumps(state["sql_result"][:10], ensure_ascii=False, indent=2)  # 최대 10개만
        ))
    ]
    
    response = await llm_smart.ainvoke(messages)
    result = response.content.strip()
    
    if result.startswith("VALID"):
        return {
            "is_valid": True,
            "validation_reason": "",
            "retry_count": state.get("retry_count", 0)
        }
    else:
        # INVALID: 이유 형식
        reason = result.replace("INVALID:", "").strip()
        return {
            "is_valid": False,
            "validation_reason": reason,
            "retry_count": state.get("retry_count", 0) + 1
        }


# ═══════════════════════════════════════════════════════════════
# Node 7: generate_report
# ═══════════════════════════════════════════════════════════════

async def generate_report(state: TextToSQLState) -> dict:
    """보고서 생성"""
    messages = [
        SystemMessage(content=GENERATE_REPORT_SYSTEM),
        HumanMessage(content=GENERATE_REPORT_USER.format(
            user_question=state["user_question"],
            parsed_request=json.dumps(state["parsed_request"], ensure_ascii=False, indent=2),
            sql_result=json.dumps(state["sql_result"][:20], ensure_ascii=False, indent=2)  # 최대 20개
        ))
    ]
    
    response = await llm_fast.ainvoke(messages)
    report = response.content.strip()
    
    # 권장 액션 추출 (보고서에서 💡 이후)
    suggested_actions = []
    if "💡" in report:
        action_section = report.split("💡")[1]
        lines = action_section.split("\n")
        for line in lines:
            if re.match(r'^\d+\.', line.strip()):
                suggested_actions.append(line.strip())
    
    return {
        "report": report,
        "suggested_actions": suggested_actions
    }
