"""Text-to-SQL 에이전트 그래프 (LangGraph)"""
from langgraph.graph import StateGraph, END
from typing import TypedDict

class SQLAgentState(TypedDict):
    """Text-to-SQL 에이전트 상태"""
    user_question: str
    selected_tables: list[str]
    generated_sql: str
    query_result: list[dict]
    error_message: str
    retry_count: int
    final_answer: str

def select_tables(state: SQLAgentState) -> SQLAgentState:
    """1차 LLM: 테이블 선택"""
    # TODO: LLM 호출하여 필요한 테이블 선택
    return {"selected_tables": []}

def generate_sql(state: SQLAgentState) -> SQLAgentState:
    """2차 LLM: SQL 생성"""
    # TODO: 선택된 테이블 스키마를 보고 SQL 생성
    return {"generated_sql": ""}

def execute_query(state: SQLAgentState) -> SQLAgentState:
    """MCP Tool: SQL 실행"""
    # TODO: MCP execute_sql Tool 호출
    return {"query_result": []}

def validate_result(state: SQLAgentState) -> str:
    """검증 LLM: 결과 확인"""
    # TODO: 결과가 요구사항에 맞는지 LLM으로 검증
    if not state["query_result"]:
        return "retry"
    return "valid"

def analyze_error(state: SQLAgentState) -> SQLAgentState:
    """오류 분석 LLM"""
    # TODO: 왜 틀렸는지 분석
    return {
        "error_message": "분석 필요",
        "retry_count": state["retry_count"] + 1
    }

def generate_summary(state: SQLAgentState) -> SQLAgentState:
    """최종 요약 생성"""
    # TODO: 결과를 자연어로 요약
    return {"final_answer": ""}

# 그래프 구성
def create_sql_agent():
    """Text-to-SQL 에이전트 그래프 생성"""
    workflow = StateGraph(SQLAgentState)
    
    # 노드 추가
    workflow.add_node("select_tables", select_tables)
    workflow.add_node("generate_sql", generate_sql)
    workflow.add_node("execute_query", execute_query)
    workflow.add_node("analyze_error", analyze_error)
    workflow.add_node("generate_summary", generate_summary)
    
    # 엣지 연결
    workflow.set_entry_point("select_tables")
    workflow.add_edge("select_tables", "generate_sql")
    workflow.add_edge("generate_sql", "execute_query")
    
    # 조건부 분기
    workflow.add_conditional_edges(
        "execute_query",
        validate_result,
        {
            "valid": "generate_summary",
            "retry": "analyze_error"
        }
    )
    
    workflow.add_edge("analyze_error", "generate_sql")
    workflow.add_edge("generate_summary", END)
    
    return workflow.compile()
