"""Text-to-SQL 에이전트 LangGraph 워크플로우 정의."""

from langgraph.graph import StateGraph, END

from .state import TextToSQLState
from .nodes import (
    parse_request,
    validate_request,
    retrieve_tables,
    select_tables,
    generate_sql,
    guard_sql,
    execute_sql,
    normalize_result,
    validate_llm,
    generate_report,
)
from .common.constants import (
    MAX_SQL_RETRY,
    MAX_TABLE_EXPAND,
    MAX_TOTAL_LOOPS,
)


def check_request_valid(state: TextToSQLState) -> str:
    """요청 유효성 검사 결과 분기 (valid / invalid)."""
    return "valid" if state.get("is_request_valid", False) else "invalid"


def has_table_context(state: TextToSQLState) -> str:
    """테이블 컨텍스트 존재 여부 분기 (valid / invalid)."""
    return "valid" if state.get("table_context") else "invalid"


def guard_sql_route(state: TextToSQLState) -> str:
    """SQL 가드 결과에 따른 분기 (ok / retry / fail)."""
    if not state.get("sql_guard_error"):
        return "ok"
    if state.get("sql_guard_error") == "CLARIFICATION_NEEDED":
        return "fail"
    if state.get("sql_retry_count", 0) <= MAX_SQL_RETRY and state.get("total_loops", 0) < MAX_TOTAL_LOOPS:
        return "retry"
    return "fail"


def normalize_route(state: TextToSQLState) -> str:
    """실행 결과 정규화 성공 여부 분기 (ok / error)."""
    if state.get("sql_error"):
        return "error"
    return "ok"


def verdict_route(state: TextToSQLState) -> str:
    """최종 검증 결과에 따른 라우팅 로직."""
    verdict = state.get("verdict", "OK")
    total_loops = state.get("total_loops", 0)

    if verdict == "OK":
        return "ok"

    if total_loops >= MAX_TOTAL_LOOPS:
        return "fail"

    if verdict in ("SQL_BAD", "COLUMN_MISSING", "TYPE_ERROR"):
        if state.get("sql_retry_count", 0) < MAX_SQL_RETRY:
            return "retry_sql"
        return "fail"

    if verdict == "RETRY_SQL":
        if state.get("table_expand_count", 0) <= MAX_TABLE_EXPAND:
             return "retry_sql"
        return "fail"

    if verdict in ("DATA_MISSING", "AMBIGUOUS", "PERMISSION", "TIMEOUT", "DB_CONN_ERROR"):
        return "fail"

    return "fail"


def build_text_to_sql_graph() -> StateGraph:
    """LangGraph 상태 머신 및 워크플로우 구성."""
    workflow = StateGraph(TextToSQLState)

    # 노드 등록
    workflow.add_node("parse_request", parse_request)
    workflow.add_node("validate_request", validate_request)
    workflow.add_node("retrieve_tables", retrieve_tables)
    workflow.add_node("select_tables", select_tables)
    workflow.add_node("generate_sql", generate_sql)
    workflow.add_node("guard_sql", guard_sql)
    workflow.add_node("execute_sql", execute_sql)
    workflow.add_node("normalize_result", normalize_result)
    workflow.add_node("validate_llm", validate_llm)
    workflow.add_node("generate_report", generate_report)

    # 시작점 설정
    workflow.set_entry_point("parse_request")

    # 기본 흐름 연결
    workflow.add_edge("parse_request", "validate_request")

    # 요청 검증 분기
    workflow.add_conditional_edges(
        "validate_request",
        check_request_valid,
        {"valid": "retrieve_tables", "invalid": "generate_report"},
    )

    workflow.add_edge("retrieve_tables", "select_tables")

    # 테이블 선택 결과 분기
    workflow.add_conditional_edges(
        "select_tables",
        has_table_context,
        {"valid": "generate_sql", "invalid": "generate_report"},
    )

    workflow.add_edge("generate_sql", "guard_sql")

    # SQL 가드 통과 여부 분기
    workflow.add_conditional_edges(
        "guard_sql",
        guard_sql_route,
        {"ok": "execute_sql", "retry": "generate_sql", "fail": "generate_report"},
    )

    workflow.add_edge("execute_sql", "normalize_result")

    # 실행 결과 정규화 분기
    workflow.add_conditional_edges(
        "normalize_result",
        normalize_route,
        {"ok": "validate_llm", "error": "validate_llm"},
    )

    # 최종 검증 및 재시도 분기
    workflow.add_conditional_edges(
        "validate_llm",
        verdict_route,
        {
            "ok": "generate_report",
            "retry_sql": "generate_sql",
            "fail": "generate_report",
        },
    )
    workflow.add_edge("generate_report", END)

    return workflow


graph = build_text_to_sql_graph()
app = graph.compile()


async def run_text_to_sql(question: str) -> dict:
    """Text-to-SQL 워크플로우 실행 및 초기 상태 설정."""
    initial_state = {
        "user_question": question,
        "sql_retry_count": 0,
        "table_expand_count": 0,
        "validation_retry_count": 0,
        "total_loops": 0,
        "verdict": "OK",
        "result_status": "unknown",
        "failed_queries": [],
        "table_expand_attempted": False,
        "table_expand_failed": False,
        "table_expand_reason": None,
    }
    return await app.ainvoke(initial_state)
