"""Text-to-SQL 에이전트 LangGraph 워크플로우 정의."""

from langgraph.graph import StateGraph, END

from .state import TextToSQLState
from .nodes import (
    classify_intent,
    general_chat,
    parse_request,
    validate_request,
    check_clarification,
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
from src.db.checkpointer import get_checkpointer


# ─────────────────────────────────────────
# 조건부 분기 함수
# ─────────────────────────────────────────

def route_by_intent(state: TextToSQLState) -> str:
    """의도 분류 결과에 따른 분기 (sql / general)."""
    return state.get("classified_intent", "sql")


def check_clarification_needed(state: TextToSQLState) -> str:
    """HITL: 역질문 필요 여부 분기 (proceed / clarify)."""
    if state.get("needs_clarification"):
        return "clarify"
    return "proceed"


def check_request_valid(state: TextToSQLState) -> str:
    """요청 유효성 분기 (valid / invalid)."""
    return "valid" if state.get("is_request_valid", False) else "invalid"


def has_table_context(state: TextToSQLState) -> str:
    """테이블 컨텍스트 존재 여부 분기."""
    return "valid" if state.get("table_context") else "invalid"


def guard_sql_route(state: TextToSQLState) -> str:
    """SQL 가드 결과에 따른 분기."""
    if not state.get("sql_guard_error"):
        return "ok"
    if state.get("sql_guard_error") == "CLARIFICATION_NEEDED":
        return "fail"
    if (
        state.get("sql_retry_count", 0) <= MAX_SQL_RETRY
        and state.get("total_loops", 0) < MAX_TOTAL_LOOPS
    ):
        return "retry"
    return "fail"


def normalize_route(state: TextToSQLState) -> str:
    """실행 결과 정규화 분기."""
    return "error" if state.get("sql_error") else "ok"


def verdict_route(state: TextToSQLState) -> str:
    """최종 검증 결과에 따른 라우팅."""
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
    return "fail"


# ─────────────────────────────────────────
# 그래프 빌더
# ─────────────────────────────────────────

def build_text_to_sql_graph() -> StateGraph:
    """LangGraph 워크플로우 구성.

    흐름:
    classify_intent → (sql) parse_request → validate_request
                       → check_clarification → (proceed) retrieve_tables → ...
                       → check_clarification → (clarify) END (역질문)
    classify_intent → (general) general_chat → END
    """
    workflow = StateGraph(TextToSQLState)

    # ── 노드 등록 ──
    workflow.add_node("classify_intent", classify_intent)
    workflow.add_node("general_chat", general_chat)
    workflow.add_node("parse_request", parse_request)
    workflow.add_node("validate_request", validate_request)
    workflow.add_node("check_clarification", check_clarification)
    workflow.add_node("retrieve_tables", retrieve_tables)
    workflow.add_node("select_tables", select_tables)
    workflow.add_node("generate_sql", generate_sql)
    workflow.add_node("guard_sql", guard_sql)
    workflow.add_node("execute_sql", execute_sql)
    workflow.add_node("normalize_result", normalize_result)
    workflow.add_node("validate_llm", validate_llm)
    workflow.add_node("generate_report", generate_report)

    # ── 진입점: 의도 분류 ──
    workflow.set_entry_point("classify_intent")

    workflow.add_conditional_edges(
        "classify_intent",
        route_by_intent,
        {"sql": "parse_request", "general": "general_chat"},
    )
    workflow.add_edge("general_chat", END)

    # ── SQL 흐름 ──
    workflow.add_edge("parse_request", "validate_request")

    # 요청 검증 성공 시 HITL 체크, 실패 시 리포트
    workflow.add_conditional_edges(
        "validate_request",
        check_request_valid,
        {"valid": "check_clarification", "invalid": "generate_report"},
    )

    # HITL: 정보 충분하면 진행, 부족하면 END(역질문)
    workflow.add_conditional_edges(
        "check_clarification",
        check_clarification_needed,
        {"proceed": "retrieve_tables", "clarify": END},
    )

    workflow.add_edge("retrieve_tables", "select_tables")

    workflow.add_conditional_edges(
        "select_tables",
        has_table_context,
        {"valid": "generate_sql", "invalid": "generate_report"},
    )

    workflow.add_edge("generate_sql", "guard_sql")

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


# ─────────────────────────────────────────
# 그래프 컴파일 (Checkpointer 연동)
# ─────────────────────────────────────────

graph = build_text_to_sql_graph()

# 비동기 Checkpointer이므로 지연 초기화
_compiled_app = None


async def get_compiled_app():
    """컴파일된 그래프 반환 (AsyncPostgresSaver 지연 초기화)."""
    global _compiled_app
    if _compiled_app is not None:
        return _compiled_app

    checkpointer = await get_checkpointer()
    _compiled_app = graph.compile(checkpointer=checkpointer)
    return _compiled_app


# 하위 호환용 — import 시점에 바로 사용 불가하므로 None으로 초기화
app = None


async def run_text_to_sql(question: str, thread_id: str = "default") -> dict:
    """Text-to-SQL 워크플로우 실행."""
    compiled = await get_compiled_app()
    initial_state = {
        "user_question": question,
        "user_constraints": "",
        "classified_intent": "",
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
        "needs_clarification": False,
        "clarification_question": "",
    }
    config = {"configurable": {"thread_id": thread_id}}
    return await compiled.ainvoke(initial_state, config=config)
