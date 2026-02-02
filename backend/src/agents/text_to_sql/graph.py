"""Text-to-SQL 에이전트 LangGraph 워크플로우 (재구축)"""
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
    expand_tables,
    generate_report,
)
from .constants import (
    MAX_SQL_RETRY,
    MAX_TABLE_EXPAND,
    MAX_TOTAL_LOOPS,
)


def check_request_valid(state: TextToSQLState) -> str:
    return "valid" if state.get("is_request_valid", False) else "invalid"


def has_table_context(state: TextToSQLState) -> str:
    return "valid" if state.get("table_context") else "invalid"


def guard_sql_route(state: TextToSQLState) -> str:
    if not state.get("sql_guard_error"):
        return "ok"
    if state.get("sql_retry_count", 0) <= MAX_SQL_RETRY and state.get("total_loops", 0) < MAX_TOTAL_LOOPS:
        return "retry"
    return "fail"


def normalize_route(state: TextToSQLState) -> str:
    if state.get("sql_error"):
        return "error"
    return "ok"


def verdict_route(state: TextToSQLState) -> str:
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

    if verdict == "TABLE_MISSING":
        if state.get("table_expand_count", 0) < MAX_TABLE_EXPAND:
            return "expand_tables"
        return "fail"

    if verdict in ("DATA_MISSING", "AMBIGUOUS", "PERMISSION", "TIMEOUT", "DB_CONN_ERROR"):
        return "fail"

    return "fail"


def build_text_to_sql_graph() -> StateGraph:
    workflow = StateGraph(TextToSQLState)

    # 노드 등록 (각 단계별 역할)
    workflow.add_node("parse_request", parse_request)
    workflow.add_node("validate_request", validate_request)
    workflow.add_node("retrieve_tables", retrieve_tables)
    workflow.add_node("select_tables", select_tables)
    workflow.add_node("generate_sql", generate_sql)
    workflow.add_node("guard_sql", guard_sql)
    workflow.add_node("execute_sql", execute_sql)
    workflow.add_node("normalize_result", normalize_result)
    workflow.add_node("validate_llm", validate_llm)
    workflow.add_node("expand_tables", expand_tables)
    workflow.add_node("generate_report", generate_report)

    # 엔트리 포인트
    workflow.set_entry_point("parse_request")

    # 기본 흐름: 파싱 → 검증 → 검색 → 선택 → 생성 → 가드 → 실행 → 정규화 → 검증 → 보고서
    workflow.add_edge("parse_request", "validate_request")

    # 요청 검증 실패 시 즉시 보고서 노드로 종료
    workflow.add_conditional_edges(
        "validate_request",
        check_request_valid,
        {"valid": "retrieve_tables", "invalid": "generate_report"},
    )

    workflow.add_edge("retrieve_tables", "select_tables")

    # 테이블 컨텍스트가 없으면 보고서로 종료
    workflow.add_conditional_edges(
        "select_tables",
        has_table_context,
        {"valid": "generate_sql", "invalid": "generate_report"},
    )

    workflow.add_edge("generate_sql", "guard_sql")

    # SQL 가드 실패 시 재생성 루프 또는 종료
    workflow.add_conditional_edges(
        "guard_sql",
        guard_sql_route,
        {"ok": "execute_sql", "retry": "generate_sql", "fail": "generate_report"},
    )

    workflow.add_edge("execute_sql", "normalize_result")

    workflow.add_conditional_edges(
        "normalize_result",
        normalize_route,
        {"ok": "validate_llm", "error": "validate_llm"},
    )

    # 실행 결과를 검증하고 결과에 따라 분기
    workflow.add_conditional_edges(
        "validate_llm",
        verdict_route,
        {
            "ok": "generate_report",
            "retry_sql": "generate_sql",
            "expand_tables": "expand_tables",
            "fail": "generate_report",
        },
    )

    # TABLE_MISSING 확장 후 SQL 재생성으로 루프
    workflow.add_edge("expand_tables", "generate_sql")
    workflow.add_edge("generate_report", END)

    return workflow


graph = build_text_to_sql_graph()
app = graph.compile()


async def run_text_to_sql(question: str) -> dict:
    initial_state = {
        "user_question": question,
        "sql_retry_count": 0,
        "table_expand_count": 0,
        "validation_retry_count": 0,
        "total_loops": 0,
        "verdict": "OK",
        "result_status": "unknown",
        "failed_queries": [],
    }
    return await app.ainvoke(initial_state)
