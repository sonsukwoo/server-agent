
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from langchain_core.messages import SystemMessage, HumanMessage, AIMessage
from src.agents.text_to_sql.nodes import _extract_previous_sql_from_messages, parse_request
from src.agents.text_to_sql.state import TextToSQLState

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
    
    with patch("src.agents.text_to_sql.nodes.structured_llm_fast.ainvoke", new_callable=AsyncMock) as mock_invoke:
        mock_invoke.return_value = mock_response
        
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
    
    with patch("src.agents.text_to_sql.nodes.structured_llm_fast.ainvoke", new_callable=AsyncMock) as mock_invoke:
        mock_invoke.return_value = mock_response
        
        result = await parse_request(state)
        parsed = result["parsed_request"]
        
        # 상속되었어야 함
        assert parsed["time_range"] == old_parsed["time_range"]
