
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
    retrieve_tables,
    validate_llm,
)
from src.agents.text_to_sql.graph import verdict_route
from src.agents.text_to_sql.common.helpers import _extract_time_range_from_sql
from src.agents.text_to_sql.state import TextToSQLState, make_initial_state
from src.agents.text_to_sql.middleware.parsed_request_guard import ParsedRequestGuard
from src.agents.text_to_sql.schemas import (
    ClarificationCheck,
    GenerateSqlResult,
    ParsedRequestModel,
    TimeRangeModel,
    ValidationResult,
    ValidationVerdict,
)


def _mock_structured_llm(mock_response) -> MagicMock:
    """구조화 출력 LLM 대체용 mock 객체 생성."""
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
    mock_response = ParsedRequestModel(time_range=TimeRangeModel(all_time=True))
    
    # Previous State (has time_range)
    old_parsed = {"time_range": {"start": "2025-01-01", "end": "2025-01-31"}}
    state = TextToSQLState(
        user_question="전체 기간 보여줘",
        parsed_request=old_parsed # 이전 턴의 상태
    )
    
    with patch("src.agents.text_to_sql.nodes.parse_request_llm", _mock_structured_llm(mock_response)):
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
    mock_response = ParsedRequestModel(intent="sql")  # time_range 비어있음
    
    # Previous State
    old_parsed = {"time_range": {"start": "2025-01-01", "end": "2025-01-31"}}
    state = TextToSQLState(
        user_question="매출은?",
        parsed_request=old_parsed
    )
    
    with patch("src.agents.text_to_sql.nodes.parse_request_llm", _mock_structured_llm(mock_response)):
        result = await parse_request(state)
        parsed = result["parsed_request"]
        
        # 상속되었어야 함
        assert parsed["time_range"] == old_parsed["time_range"]


@pytest.mark.asyncio
async def test_time_inheritance_followup_end_only_inherits_start():
    """후속 질문에서 end만 있으면 이전 start를 상속하는지 테스트."""
    mock_response = ParsedRequestModel(
        intent="sql",
        is_followup=True,
        time_range=TimeRangeModel(end="2025-01-20T00:00:00+09:00"),
    )

    old_parsed = {
        "time_range": {
            "start": "2025-01-01T00:00:00+09:00",
            "end": "2025-01-31T23:59:59+09:00",
            "timezone": "Asia/Seoul",
        }
    }
    state = TextToSQLState(
        user_question="그 결과에서 1월 20일까지로 좁혀줘",
        parsed_request=old_parsed,
    )

    with patch("src.agents.text_to_sql.nodes.parse_request_llm", _mock_structured_llm(mock_response)):
        result = await parse_request(state)
        parsed = result["parsed_request"]

        assert parsed["time_range"]["start"] == old_parsed["time_range"]["start"]
        assert parsed["time_range"]["end"] == "2025-01-20T00:00:00+09:00"
        assert parsed["time_range"]["inherit"] is True


def test_parsed_request_guard_start_only_autofills_end():
    """start만 있으면 end를 현재 시각으로 자동 보정하는지 테스트."""
    parsed = {
        "intent": "cpu_usage",
        "time_range": {"start": "2025-01-01T00:00:00+09:00"},
    }

    is_valid, error, normalized, adjustment = ParsedRequestGuard.validate(parsed)
    assert is_valid is True
    assert error == ""
    assert normalized["time_range"].get("end")
    assert "종료 시각이 없어 현재 시각으로 자동 보정" in (adjustment or "")


def test_parsed_request_guard_end_only_marks_from_beginning():
    """end만 있으면 처음부터 종료 시각까지로 보정되는지 테스트."""
    parsed = {
        "intent": "cpu_usage",
        "time_range": {"end": "2025-01-31T23:59:59+09:00"},
    }

    is_valid, error, normalized, adjustment = ParsedRequestGuard.validate(parsed)
    assert is_valid is True
    assert error == ""
    assert normalized["time_range"]["from_beginning"] is True
    assert normalized["time_range"].get("end") == "2025-01-31T23:59:59+09:00"
    assert normalized["time_range"].get("start") is None
    assert "처음부터 종료 시각까지" in (adjustment or "")


def test_parsed_request_guard_followup_all_time_is_not_overwritten():
    """followup + all_time 조합에서 inherit로 덮어쓰지 않는지 테스트."""
    parsed = {
        "intent": "cpu_usage",
        "is_followup": True,
        "time_range": {"all_time": True},
    }

    is_valid, error, normalized, _ = ParsedRequestGuard.validate(parsed)
    assert is_valid is True
    assert error == ""
    assert normalized["time_range"].get("all_time") is True
    assert normalized["time_range"].get("inherit") is None


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
    mock_response = ClarificationCheck(
        needs_clarification=True,
        question="어떤 기간을 조회할까요?",
    )

    state = TextToSQLState(
        user_question="CPU 알려줘",
        parsed_request={"intent": "cpu", "metric": "cpu", "condition": ""},
    )

    with patch("src.agents.text_to_sql.nodes.clarification_check_llm", _mock_structured_llm(mock_response)):
        result = await check_clarification(state)

    assert result["needs_clarification"] is True
    assert "어떤 기간" in result["clarification_question"]


@pytest.mark.asyncio
async def test_generate_sql_updates_table_expand_count():
    """테이블 확장 시 table_expand_count가 증가하는지 테스트."""
    mock_resp_expand = GenerateSqlResult(needs_more_tables=True, sql="")
    mock_resp_final = GenerateSqlResult(needs_more_tables=False, sql="SELECT 1")

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

    with patch("src.agents.text_to_sql.nodes.generate_sql_llm", fake_llm):
        with patch(
            "src.agents.text_to_sql.nodes.expand_tables_tool",
            return_value=(["ops.cpu", "ops.mem"], "테이블: ops.cpu\\n테이블: ops.mem", 2),
        ):
            result = await generate_sql(state)

    assert result["generated_sql"] == "SELECT 1"
    assert result["table_expand_count"] == 1
    assert result["table_expand_attempted"] is True


@pytest.mark.asyncio
async def test_generate_sql_structured_output_error_no_raw_fallback():
    """구조화 출력 실패 시 raw 폴백 없이 실패를 반환하는지 테스트."""
    fake_llm = MagicMock()
    fake_llm.ainvoke = AsyncMock(side_effect=RuntimeError("schema mismatch"))

    state = TextToSQLState(
        parsed_request={"intent": "test", "time_range": {"all_time": True}},
        selected_tables=["ops.cpu"],
        table_context="테이블: ops.cpu",
        failed_queries=[],
        validation_reason="",
        user_constraints="",
    )

    with patch("src.agents.text_to_sql.nodes.generate_sql_llm", fake_llm):
        result = await generate_sql(state)

    assert result["generated_sql"] == ""
    assert "구조화 SQL 생성 실패" in result["sql_guard_error"]
    assert result["last_tool_usage"] == "SQL 생성 실패: 구조화 출력 오류"


@pytest.mark.asyncio
async def test_validate_llm_updates_validation_retry_count():
    """검증 실패 시 validation_retry_count가 증가하는지 테스트."""
    mock_response = ValidationResult(
        verdict=ValidationVerdict.SQL_BAD,
        reason="조건 누락",
        hint="WHERE ...",
        unnecessary_tables=[],
    )

    fake_llm = _mock_structured_llm(mock_response)

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

    with patch("src.agents.text_to_sql.nodes.validate_result_llm", fake_llm):
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


def test_extract_time_range_from_sql_comparison_operators():
    """BETWEEN 외 비교식 시간 조건에서도 start/end를 추출하는지 테스트."""
    sql = (
        "SELECT * FROM ops_metrics.metrics_memory m "
        "WHERE m.ts >= '2026-02-24T13:00:00+09:00' "
        "AND m.ts <= '2026-02-24T15:00:00+09:00'"
    )
    start, end = _extract_time_range_from_sql(sql)
    assert start == "2026-02-24T13:00:00+09:00"
    assert end == "2026-02-24T15:00:00+09:00"


def test_verdict_route_table_missing_retries_table_search():
    """TABLE_MISSING이면 generate_sql 재시도가 아니라 retrieve_tables로 분기하는지 테스트."""
    route = verdict_route(
        TextToSQLState(
            verdict="TABLE_MISSING",
            validation_retry_count=1,
            total_loops=0,
        )
    )
    assert route == "retry_tables"


@pytest.mark.asyncio
async def test_retrieve_tables_followup_force_search_merges_previous_tables():
    """TABLE_MISSING 이후 followup 강제 재검색 시 이전 테이블과 신규 후보를 합치는지 테스트."""

    class _FakeQdrantClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def call_tool(self, tool_name, payload):
            assert tool_name == "search_tables"
            assert payload["top_k"] > 8
            assert "metric:cpu_usage" in payload["query"]
            return (
                '[{"table_name":"ops_metrics.docker_metrics","score":0.93,'
                '"columns":[{"name":"ts","type":"timestamptz","description":"time"}]}]'
            )

    state = TextToSQLState(
        user_question="마지막 결과에서 컨테이너 현황도 같이 보여줘",
        parsed_request={"is_followup": True, "metric": "cpu_usage"},
        force_table_search=True,
        messages=[
            AIMessage(
                content=(
                    "```sql\nSELECT * FROM ops_metrics.metrics_memory "
                    "WHERE ts >='2026-02-24T13:00:00+09:00'\n```"
                )
            )
        ],
    )

    with patch("src.agents.text_to_sql.nodes.qdrant_search_client", return_value=_FakeQdrantClient()):
        result = await retrieve_tables(state)

    table_names = [c["table_name"] for c in result["table_candidates"]]
    assert "ops_metrics.metrics_memory" in table_names
    assert "ops_metrics.docker_metrics" in table_names
    assert result["force_table_search"] is False


@pytest.mark.asyncio
async def test_retrieve_tables_followup_default_also_runs_vector_search():
    """후속 질문 기본 경로에서도 벡터 검색을 수행해 신규 후보를 보강하는지 테스트."""

    class _FakeQdrantClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def call_tool(self, tool_name, payload):
            assert tool_name == "search_tables"
            assert payload["top_k"] == 8
            return (
                '[{"table_name":"ops_metrics.metrics_disk","score":0.77,'
                '"columns":[{"name":"ts","type":"timestamptz","description":"time"}]}]'
            )

    state = TextToSQLState(
        user_question="현재 나온 결과에서 최고 램 시점의 디스크 현황",
        parsed_request={"is_followup": True},
        force_table_search=False,
        messages=[
            AIMessage(
                content=(
                    "```sql\nSELECT * FROM ops_metrics.metrics_memory "
                    "JOIN ops_metrics.metrics_cpu ON metrics_cpu.ts = metrics_memory.ts\n```"
                )
            )
        ],
    )

    with patch("src.agents.text_to_sql.nodes.qdrant_search_client", return_value=_FakeQdrantClient()):
        result = await retrieve_tables(state)

    table_names = [c["table_name"] for c in result["table_candidates"]]
    assert "ops_metrics.metrics_memory" in table_names
    assert "ops_metrics.metrics_cpu" in table_names
    assert "ops_metrics.metrics_disk" in table_names
    assert "이전 테이블 기반 + 보강 검색 완료" in result["last_tool_usage"]


@pytest.mark.asyncio
async def test_validate_llm_column_missing_followup_promotes_table_search():
    """후속 질문에서 COLUMN_MISSING이면 테이블 재검색 경로로 승격되는지 테스트."""
    mock_response = ValidationResult(
        verdict=ValidationVerdict.COLUMN_MISSING,
        reason="cpu 관련 컬럼이 현재 컨텍스트에 없습니다.",
        hint="ops_metrics.metrics_cpu를 포함하세요.",
        unnecessary_tables=[],
    )
    fake_llm = _mock_structured_llm(mock_response)

    state = TextToSQLState(
        sql_error=None,
        generated_sql="SELECT memory_usage_percent FROM ops_metrics.metrics_memory",
        sql_retry_count=0,
        validation_retry_count=0,
        total_loops=0,
        parsed_request={"is_followup": True, "time_range": {"all_time": True}},
        user_question="마지막 결과에서 최고 RAM 시점의 cpu 사용량",
        user_constraints="",
        table_context="테이블: ops_metrics.metrics_memory",
        sql_result=[{"memory_usage_percent": 71.84}],
        failed_queries=[],
        force_table_search=False,
    )

    with patch("src.agents.text_to_sql.nodes.validate_result_llm", fake_llm):
        result = await validate_llm(state)

    assert result["verdict"] == "TABLE_MISSING"
    assert result["force_table_search"] is True
    assert "테이블 재검색" in result["last_tool_usage"]
