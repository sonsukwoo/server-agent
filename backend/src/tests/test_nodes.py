
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from langchain_core.messages import HumanMessage, AIMessage
from src.agents.text_to_sql.nodes import (
    _extract_previous_sql_from_messages,
    check_clarification,
    generate_sql,
    _handle_unnecessary_tables,
    normalize_result,
    parse_request,
    validate_llm,
)
from src.agents.text_to_sql.state import TextToSQLState, make_initial_state


def _mock_structured_llm(mock_response: MagicMock) -> MagicMock:
    """structured_llm_fast 대체용 mock 객체 생성."""
    fake_llm = MagicMock()
    fake_llm.ainvoke = AsyncMock(return_value=mock_response)
    return fake_llm


def test_extract_previous_sql_ssot():
    """generated_sql이 있어도 messages의 히스토리를 우선하는지(SSOT) 테스트."""
    
    # 1. messages에 SQL이 있는 경우
    state = TextToSQLState(
        generated_sql="SELECT * FROM wrong_table", # 이건 무시되어야 함
        messages=[
            HumanMessage(content="질문"),
            AIMessage(content="```sql\nSELECT * FROM right_table\n```")
        ]
    )
    extracted = _extract_previous_sql_from_messages(state)
    assert extracted == "SELECT * FROM right_table"

    # 2. messages에 SQL이 없는 경우
    state = TextToSQLState(
        generated_sql="SELECT * FROM wrong_table",
        messages=[
            HumanMessage(content="질문"),
            AIMessage(content="안녕하세요")
        ]
    )
    extracted = _extract_previous_sql_from_messages(state)
    assert extracted == "" 

@pytest.mark.asyncio
async def test_time_inheritance_all_time():
    """all_time=True일 때 이전 시간 상속을 거부하는지 테스트."""
    
    # Mock LLM Response (all_time=True)
    mock_response = MagicMock()
    mock_response.content = '{"time_range": {"all_time": true}}'
    
    # Previous State (has time_range)
    old_parsed = {"time_range": {"start": "2025-01-01", "end": "2025-01-31"}}
    state = TextToSQLState(
        user_question="전체 기간 보여줘",
        parsed_request=old_parsed # 이전 턴의 상태
    )
    
    with patch("src.agents.text_to_sql.nodes.structured_llm_fast", _mock_structured_llm(mock_response)):
        result = await parse_request(state)
        parsed = result["parsed_request"]
        
        # 상속되지 않았어야 함
        assert parsed["time_range"].get("all_time") is True
        assert "start" not in parsed["time_range"]
        assert "end" not in parsed["time_range"]

@pytest.mark.asyncio
async def test_time_inheritance_normal():
    """시간 언급이 없을 때 이전 시간을 상속하는지 테스트."""
    
    # Mock LLM Response (No time_range)
    mock_response = MagicMock()
    mock_response.content = '{"intent": "sql"}' # time_range 비어있음
    
    # Previous State
    old_parsed = {"time_range": {"start": "2025-01-01", "end": "2025-01-31"}}
    state = TextToSQLState(
        user_question="매출은?",
        parsed_request=old_parsed
    )
    
    with patch("src.agents.text_to_sql.nodes.structured_llm_fast", _mock_structured_llm(mock_response)):
        result = await parse_request(state)
        parsed = result["parsed_request"]
        
        # 상속되었어야 함
        assert parsed["time_range"] == old_parsed["time_range"]


def test_handle_unnecessary_tables_rebuilds_string_context():
    """불필요 테이블 제거 시 table_context가 문자열로 재구성되는지 테스트."""
    state = TextToSQLState(
        selected_tables=["ops.cpu", "ops.mem"],
        table_candidates=[
            {
                "table_name": "ops.cpu",
                "description": "cpu metrics",
                "columns": [{"name": "ts", "type": "timestamptz", "description": "time"}],
                "join_keys": ["ts"],
                "primary_time_col": "ts",
            },
            {
                "table_name": "ops.mem",
                "description": "memory metrics",
                "columns": [{"name": "ts", "type": "timestamptz", "description": "time"}],
                "join_keys": ["ts"],
                "primary_time_col": "ts",
            },
        ],
        total_loops=0,
    )

    result = _handle_unnecessary_tables(
        state=state,
        unnecessary=["ops.mem"],
        failed_queries=[],
    )

    assert result is not None
    assert result["selected_tables"] == ["ops.cpu"]
    assert isinstance(result["table_context"], str)
    assert "테이블: ops.cpu" in result["table_context"]


@pytest.mark.asyncio
async def test_normalize_result_sets_string_verdict():
    """normalize_result가 verdict를 문자열로 반환하는지 테스트."""
    state = TextToSQLState(
        sql_error='relation "unknown_table" does not exist',
        sql_retry_count=0,
        total_loops=0,
        failed_queries=[],
        generated_sql="SELECT * FROM unknown_table",
    )

    result = await normalize_result(state)

    assert result["verdict"] == "TABLE_MISSING"
    assert isinstance(result["verdict"], str)
    assert "validation_reason" in result


@pytest.mark.asyncio
async def test_check_clarification_emits_question():
    """정보 부족 판단 시 clarification 질문을 반환하는지 테스트."""
    mock_response = MagicMock()
    mock_response.content = '{"needs_clarification": true, "question": "어떤 기간을 조회할까요?"}'

    fake_llm = MagicMock()
    fake_llm.ainvoke = AsyncMock(return_value=mock_response)

    state = TextToSQLState(
        user_question="CPU 알려줘",
        parsed_request={"intent": "cpu", "metric": "cpu", "condition": ""},
    )

    with patch("src.agents.text_to_sql.nodes.structured_llm_fast", fake_llm):
        result = await check_clarification(state)

    assert result["needs_clarification"] is True
    assert "어떤 기간" in result["clarification_question"]


@pytest.mark.asyncio
async def test_generate_sql_updates_table_expand_count():
    """테이블 확장 시 table_expand_count가 증가하는지 테스트."""
    mock_resp_expand = MagicMock()
    mock_resp_expand.content = '{"needs_more_tables": true, "sql": ""}'
    mock_resp_final = MagicMock()
    mock_resp_final.content = '{"needs_more_tables": false, "sql": "SELECT 1"}'

    fake_llm = MagicMock()
    fake_llm.ainvoke = AsyncMock(side_effect=[mock_resp_expand, mock_resp_final])

    state = TextToSQLState(
        parsed_request={"intent": "test", "time_range": {"all_time": True}},
        selected_tables=["ops.cpu"],
        table_context="테이블: ops.cpu",
        table_candidates=[
            {"table_name": "ops.cpu", "columns": []},
            {"table_name": "ops.mem", "columns": []},
        ],
        candidate_offset=1,
        table_expand_count=0,
        table_expand_attempted=False,
        table_expand_failed=False,
        failed_queries=[],
        validation_reason="",
        user_constraints="",
    )

    with patch("src.agents.text_to_sql.nodes.llm_smart", fake_llm):
        with patch(
            "src.agents.text_to_sql.nodes.expand_tables_tool",
            return_value=(["ops.cpu", "ops.mem"], "테이블: ops.cpu\\n테이블: ops.mem", 2),
        ):
            result = await generate_sql(state)

    assert result["generated_sql"] == "SELECT 1"
    assert result["table_expand_count"] == 1
    assert result["table_expand_attempted"] is True


@pytest.mark.asyncio
async def test_validate_llm_updates_validation_retry_count():
    """검증 실패 시 validation_retry_count가 증가하는지 테스트."""
    mock_response = MagicMock()
    mock_response.content = (
        '{"verdict":"SQL_BAD","feedback_to_sql":"조건 누락","correction_hint":"WHERE ...","unnecessary_tables":[]}'
    )

    fake_llm = MagicMock()
    fake_llm.ainvoke = AsyncMock(return_value=mock_response)

    state = TextToSQLState(
        sql_error=None,
        generated_sql="SELECT * FROM ops.cpu",
        sql_retry_count=0,
        validation_retry_count=0,
        total_loops=0,
        parsed_request={"time_range": {"all_time": True}},
        user_question="질문",
        user_constraints="",
        table_context="테이블: ops.cpu",
        sql_result=[{"cpu": 10}],
        failed_queries=[],
    )

    with patch("src.agents.text_to_sql.nodes.llm_smart", fake_llm):
        result = await validate_llm(state)

    assert result["verdict"] == "SQL_BAD"
    assert result["validation_retry_count"] == 1
    assert result["sql_retry_count"] == 1
    assert result["total_loops"] == 1


def test_make_initial_state_contract_keys():
    """초기 상태가 계약상 기본 키를 모두 포함하는지 테스트."""
    state = make_initial_state(user_question="안녕")
    assert state["user_question"] == "안녕"
    assert state["classified_intent"] is None
    assert state["request_error"] == ""
    assert state["validation_reason"] == ""
    assert state["sql_guard_error"] == ""
    assert state["sql_error"] is None
    assert state["last_tool_usage"] is None
