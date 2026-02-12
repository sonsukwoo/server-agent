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
    "RETRY_SQL",
    "TABLE_MISSING",
    "DATA_MISSING",
    "COLUMN_MISSING",
    "PERMISSION",
    "TYPE_ERROR",
    "TIMEOUT",
    "DB_CONN_ERROR",
    "AMBIGUOUS",
]


IntentType = Literal["sql", "general"]


class TextToSQLState(TypedDict, total=False):
    """Text-to-SQL 에이전트 상태.
    
    [SSOT 원칙]
    - messages: 대화의 진실 공급원(Source of Truth)입니다. 모든 의사결정은 이 히스토리를 기반으로 합니다.
    - 나머지 필드(generated_sql, parsed_request 등): 현재 턴 내에서 노드 간 데이터를 전달하기 위한 '일시적(Transient)' 상태입니다.
      다음 턴으로 넘어갈 때 이 값들에 의존하지 않도록 주의해야 합니다.
    """

    # 대화 히스토리 (LangGraph 내장 add_messages 리듀서 적용 - SSOT)
    messages: Annotated[list[BaseMessage], add_messages]

    # 입력
    user_question: str
    user_constraints: Optional[str]

    # 의도 분류
    classified_intent: Optional[IntentType]

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
    sql_error: Optional[str]
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
    # - sql_retry_count: SQL 생성/가드/실행 에러 복구 재시도 횟수
    # - table_expand_count: generate_sql 내 테이블 확장 시도 횟수
    # - validation_retry_count: validate_llm 단계에서 재생성 요구 횟수
    # - total_loops: 전체 루프 상한 제어용 통합 카운터
    sql_retry_count: int
    table_expand_count: int
    validation_retry_count: int
    total_loops: int

    # 기록
    failed_queries: list[str]

    # 보고서
    report: str
    suggested_actions: list[str]


def make_initial_state(
    user_question: str,
    user_constraints: str = "",
) -> TextToSQLState:
    """새 요청 시작 시 공통으로 사용하는 초기 상태 생성."""
    return {
        "user_question": user_question,
        "user_constraints": user_constraints,
        "classified_intent": None,
        "request_error": "",
        "validation_reason": "",
        "sql_guard_error": "",
        "sql_error": None,
        "last_tool_usage": None,
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
