"""Text-to-SQL 에이전트 LangGraph 워크플로우"""
from langgraph.graph import StateGraph, END

from .state import TextToSQLState
from .nodes import (
    parse_request,
    validate_request,
    select_table,
    generate_sql,
    execute_sql,
    validate_result,
    generate_report,
)


# ═══════════════════════════════════════════════════════════════
# 조건부 분기 함수
# ═══════════════════════════════════════════════════════════════

def check_request_valid(state: TextToSQLState) -> str:
    """validate_request 후 분기"""
    if state.get("is_request_valid", False):
        return "valid"
    return "invalid"


def check_table_valid(state: TextToSQLState) -> str:
    """select_table 후 분기"""
    if state.get("is_table_valid", False):
        return "valid"
    return "invalid"


def should_retry(state: TextToSQLState) -> str:
    """validate_result 후 분기"""
    if state.get("is_valid", False):
        return "valid"
    
    # retry_count는 validate_result 노드에서 이미 증가됨
    if state.get("retry_count", 0) < 3:
        return "retry"
    return "fail"


# ═══════════════════════════════════════════════════════════════
# 그래프 빌드
# ═══════════════════════════════════════════════════════════════

def build_text_to_sql_graph() -> StateGraph:
    """Text-to-SQL 에이전트 그래프 생성"""
    workflow = StateGraph(TextToSQLState)
    
    # ─────────────────────────────────────────
    # 노드 추가
    # ─────────────────────────────────────────
    workflow.add_node("parse_request", parse_request)
    workflow.add_node("validate_request", validate_request)
    workflow.add_node("select_table", select_table)
    workflow.add_node("generate_sql", generate_sql)
    workflow.add_node("execute_sql", execute_sql)
    workflow.add_node("validate_result", validate_result)
    workflow.add_node("generate_report", generate_report)
    
    # ─────────────────────────────────────────
    # 엔트리 포인트
    # ─────────────────────────────────────────
    workflow.set_entry_point("parse_request")
    
    # ─────────────────────────────────────────
    # 순차 엣지
    # ─────────────────────────────────────────
    workflow.add_edge("parse_request", "validate_request")
    workflow.add_edge("generate_sql", "execute_sql")
    workflow.add_edge("execute_sql", "validate_result")
    workflow.add_edge("generate_report", END)
    
    # ─────────────────────────────────────────
    # 조건부 엣지: validate_request
    # ─────────────────────────────────────────
    workflow.add_conditional_edges(
        "validate_request",
        check_request_valid,
        {
            "valid": "select_table",
            "invalid": END  # 요청 검증 실패 시 즉시 종료
        }
    )
    
    # ─────────────────────────────────────────
    # 조건부 엣지: select_table (NONE 처리)
    # ─────────────────────────────────────────
    workflow.add_conditional_edges(
        "select_table",
        check_table_valid,
        {
            "valid": "generate_sql",
            "invalid": END  # 테이블 선택 실패 시 즉시 종료
        }
    )
    
    # ─────────────────────────────────────────
    # 조건부 엣지: validate_result (재시도 루프)
    # ─────────────────────────────────────────
    workflow.add_conditional_edges(
        "validate_result",
        should_retry,
        {
            "valid": "generate_report",
            "retry": "generate_sql",  # SQL 재생성
            "fail": END               # 최대 재시도 초과
        }
    )
    
    return workflow


# ═══════════════════════════════════════════════════════════════
# 컴파일된 앱
# ═══════════════════════════════════════════════════════════════

# 그래프 빌드 및 컴파일
graph = build_text_to_sql_graph()
app = graph.compile()


# ═══════════════════════════════════════════════════════════════
# 편의 함수
# ═══════════════════════════════════════════════════════════════

async def run_text_to_sql(question: str) -> dict:
    """
    Text-to-SQL 에이전트 실행
    
    Args:
        question: 사용자 자연어 질문
        
    Returns:
        최종 상태 (report, suggested_actions 포함)
    """
    initial_state = {
        "user_question": question,
        "retry_count": 0
    }
    
    result = await app.ainvoke(initial_state)
    return result

