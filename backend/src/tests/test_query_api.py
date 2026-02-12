import json
from unittest.mock import AsyncMock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.api.query import router as query_router


class FakeCompiledApp:
    def __init__(self, events):
        self._events = events

    async def astream(self, initial_state, config):
        for event in self._events:
            yield event


class FailingCompiledApp:
    async def astream(self, initial_state, config):
        if False:
            yield {}
        raise RuntimeError("mock stream failure")


def _parse_sse_events(raw_text: str) -> list[dict]:
    events = []
    for line in raw_text.splitlines():
        if line.startswith("data: "):
            events.append(json.loads(line[6:]))
    return events


def _build_client() -> TestClient:
    app = FastAPI()
    app.include_router(query_router)
    return TestClient(app)


def test_query_stream_success():
    fake_events = [
        {"classify_intent": {"last_tool_usage": "질문 유형 판별: sql"}},
        {
            "generate_report": {
                "report": "완료",
                "suggested_actions": ["다음 분석"],
                "messages": [],
            }
        },
    ]

    with patch(
        "src.api.query.get_compiled_app",
        new=AsyncMock(return_value=FakeCompiledApp(fake_events)),
    ):
        client = _build_client()
        response = client.post(
            "/query",
            json={"agent": "sql", "question": "매출 알려줘", "session_id": "session-1"},
        )

    assert response.status_code == 200
    sse_events = _parse_sse_events(response.text)
    event_types = [event["type"] for event in sse_events]

    assert "status" in event_types
    assert "result" in event_types

    result_event = next(event for event in sse_events if event["type"] == "result")
    payload = result_event["payload"]
    assert payload["ok"] is True
    assert payload["session_id"] == "session-1"
    assert payload["data"]["report"] == "완료"
    assert payload["data"]["suggested_actions"] == ["다음 분석"]


def test_query_stream_clarification():
    fake_events = [
        {
            "check_clarification": {
                "needs_clarification": True,
                "clarification_question": "기간을 알려주세요.",
            }
        }
    ]

    with patch(
        "src.api.query.get_compiled_app",
        new=AsyncMock(return_value=FakeCompiledApp(fake_events)),
    ):
        client = _build_client()
        response = client.post(
            "/query",
            json={"agent": "sql", "question": "CPU 사용률", "session_id": "session-2"},
        )

    assert response.status_code == 200
    sse_events = _parse_sse_events(response.text)
    event_types = [event["type"] for event in sse_events]

    assert "clarification" in event_types
    assert "result" not in event_types

    clarification_event = next(event for event in sse_events if event["type"] == "clarification")
    assert clarification_event["message"] == "기간을 알려주세요."
    assert clarification_event["session_id"] == "session-2"


def test_query_stream_error_event():
    with patch(
        "src.api.query.get_compiled_app",
        new=AsyncMock(return_value=FailingCompiledApp()),
    ):
        client = _build_client()
        response = client.post(
            "/query",
            json={"agent": "sql", "question": "장애율 알려줘", "session_id": "session-3"},
        )

    assert response.status_code == 200
    sse_events = _parse_sse_events(response.text)
    error_event = next(event for event in sse_events if event["type"] == "error")
    assert "서버 에러" in error_event["message"]
