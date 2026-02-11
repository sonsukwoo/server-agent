"""Text-to-SQL 에이전트 State 정의."""

from typing import TypedDict, Optional, Literal, Annotated

from langgraph.graph.message import add_messages
from langchain_core.messages import BaseMessage


class ParsedRequest(TypedDict, total=False):
    """구조화된 요구사항"""
    intent: str
    time_range: dict  # {"start": str, "end": str, "timezone": str}
    metric: Optional[str]
    condition: Optional[str]
    output: Optional[str]


class TableCandidate(TypedDict, total=False):
    """Qdrant 검색 결과 테이블 후보"""
    table_name: str
    description: str
    columns: list[dict]
    score: float
    join_keys: list[str]
    primary_time_col: Optional[str]


Verdict = Literal[
    "OK",
    "SQL_BAD",
    "TABLE_MISSING",
    "DATA_MISSING",
    "COLUMN_MISSING",
    "PERMISSION",
    "TYPE_ERROR",
    "TIMEOUT",
    "DB_CONN_ERROR",
    "AMBIGUOUS",
]


class TextToSQLState(TypedDict, total=False):
    """Text-to-SQL 에이전트 상태."""

    # 대화 히스토리 (LangGraph 내장 add_messages 리듀서 적용)
    messages: Annotated[list[BaseMessage], add_messages]

    # 입력
    user_question: str
    user_constraints: Optional[str]

    # 의도 분류
    classified_intent: str  # "sql" | "general"

    # 파싱
    parsed_request: ParsedRequest
    is_request_valid: bool
    request_error: str

    # HITL: 정보 부족 시 역질문
    needs_clarification: bool
    clarification_question: str

    # 검색/선택
    table_candidates: list[TableCandidate]
    selected_tables: list[str]
    table_context: str
    candidate_offset: int

    # SQL 생성/실행
    generated_sql: str
    sql_guard_error: str
    sql_result: list[dict]
    sql_error: str
    raw_sql_result: str

    # 결과/검증
    result_status: str
    verdict: Verdict
    validation_reason: str
    feedback_to_sql: str
    last_tool_usage: Optional[str]

    # 확장 상태
    table_expand_attempted: bool
    table_expand_failed: bool
    table_expand_reason: Optional[str]

    # 루프 카운터
    sql_retry_count: int
    table_expand_count: int
    validation_retry_count: int
    total_loops: int

    # 기록
    failed_queries: list[str]

    # 보고서
    report: str
    suggested_actions: list[str]
