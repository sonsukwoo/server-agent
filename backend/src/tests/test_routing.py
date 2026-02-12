
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from src.agents.text_to_sql.nodes import classify_intent
from src.agents.text_to_sql.state import TextToSQLState


def _mock_structured_llm(mock_response: MagicMock) -> MagicMock:
    """structured_llm_fast 대체용 mock 객체 생성."""
    fake_llm = MagicMock()
    fake_llm.ainvoke = AsyncMock(return_value=mock_response)
    return fake_llm


@pytest.mark.asyncio
async def test_classify_intent_sql_normalization():
    """classify_intent가 대소문자나 공백이 섞인 ' SQL '을 'sql'로 정규화하는지 테스트."""
    
    # Mock Response
    mock_response = MagicMock()
    mock_response.content = '{"intent": " SQL ", "reason": "test"}'
    
    # RunnableBinding은 속성 직접 patch가 불가하므로 객체 자체를 교체
    with patch("src.agents.text_to_sql.nodes.structured_llm_fast", _mock_structured_llm(mock_response)):
        state = TextToSQLState(user_question="매출 알려줘")
        result = await classify_intent(state)
        
        assert result["classified_intent"] == "sql"

@pytest.mark.asyncio
async def test_classify_intent_fallback():
    """classify_intent가 이상한 값('unknown')을 반환하면 'sql'로 Fallback하는지 테스트."""
    
    # Mock Response
    mock_response = MagicMock()
    mock_response.content = '{"intent": "unknown_intent", "reason": "dunno"}'
    
    with patch("src.agents.text_to_sql.nodes.structured_llm_fast", _mock_structured_llm(mock_response)):
        state = TextToSQLState(user_question="이상한 질문")
        result = await classify_intent(state)
        
        assert result["classified_intent"] == "sql"

@pytest.mark.asyncio
async def test_classify_intent_general():
    """classify_intent가 'general'을 정상적으로 반환하는지 테스트."""
    
    # Mock Response
    mock_response = MagicMock()
    mock_response.content = '{"intent": "general", "reason": "greeting"}'
    
    with patch("src.agents.text_to_sql.nodes.structured_llm_fast", _mock_structured_llm(mock_response)):
        state = TextToSQLState(user_question="안녕")
        result = await classify_intent(state)
        
        assert result["classified_intent"] == "general"
