"""질의 처리 및 스트리밍 응답 API."""

import json
import logging
import uuid
from typing import Optional

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
import asyncio

from src.agents.text_to_sql import get_compiled_app
from src.agents.text_to_sql.middleware.input_guard import InputGuard

logger = logging.getLogger("API_QUERY")

router = APIRouter(tags=["query"])

# 프론트엔드 상태 표시용 노드별 메시지 매핑
_NODE_MESSAGES = {
    "classify_intent": "질문 유형 판별 중",
    "general_chat": "일반 대화 응답 생성 중",
    "parse_request": "사용자 질문 분석 중",
    "validate_request": "질문 유효성 검증 중",
    "check_clarification": "필수 정보 확인 중",
    "retrieve_tables": "관련 테이블 검색 중",
    "select_tables": "조회에 필요한 테이블 선택 중",
    "generate_sql": "SQL 쿼리 생성 중",
    "guard_sql": "SQL 안전성 검사 중",
    "execute_sql": "데이터베이스 조회 중",
    "normalize_result": "조회 결과 정리 중",
    "validate_llm": "결과 정확성 검증 중",
    "expand_tables": "테이블 확장 검색 중",
    "generate_report": "최종 보고서 작성 중",
}


class QueryRequest(BaseModel):
    """질의 요청 모델."""
    agent: str
    question: str
    session_id: Optional[str] = None


class QueryResponse(BaseModel):
    """질의 응답 모델."""
    ok: bool
    agent: str
    data: dict | None = None
    error: str | None = None


def _make_sse(event_type: str, **kwargs) -> str:
    """SSE 형식 이벤트 문자열 생성."""
    payload = {"type": event_type, **kwargs}
    return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"


def _build_retry_message(node_name: str, current_retry: int, last_reason: str) -> str | None:
    """재시도 시 상세 상태 메시지 생성."""
    if node_name != "generate_sql" or current_retry <= 0:
        return None
    if last_reason:
        return f"피드백 반영하여 SQL 재작성 중 (사유: {last_reason})"
    return f"오류 복구 및 SQL 재작성 중... [재시도 {current_retry}]"


@router.post("/query")
async def query(body: QueryRequest):
    """자연어 질문 처리 API (SSE 스트리밍)."""
    agent_type = body.agent.lower().strip()
    question = body.question.strip()
    session_id = body.session_id
    logger.info("API Request: session_id=%s question='%s'", session_id, question)

    if not question:
        raise HTTPException(status_code=400, detail="질문이 비어있습니다")

    is_valid, error = InputGuard.validate(question)
    if not is_valid:
        raise HTTPException(status_code=400, detail=error)

    async def event_generator():
        if agent_type != "sql":
            yield _make_sse("error", message=f"지원하지 않는 에이전트 타입입니다: {agent_type}")
            return

        # Checkpointer가 thread_id 기반으로 대화 맥락을 자동 관리
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
        thread_id = session_id or str(uuid.uuid4())
        config = {"configurable": {"thread_id": thread_id}}

        last_reason = ""
        current_retry = 0

        try:
            sql_app = await get_compiled_app()
            async for event in sql_app.astream(initial_state, config=config):
                await asyncio.sleep(0)

                for node_name, output in event.items():
                    # 상태 추적
                    if "validation_reason" in output:
                        last_reason = output["validation_reason"]
                    if "sql_retry_count" in output:
                        current_retry = output["sql_retry_count"]
                    elif "validation_retry_count" in output:
                        current_retry = output["validation_retry_count"]

                    # HITL: 정보 부족 → 역질문 이벤트
                    if output.get("needs_clarification"):
                        yield _make_sse(
                            "clarification",
                            message=output.get("clarification_question", "추가 정보가 필요합니다."),
                            session_id=thread_id,
                        )
                        return

                    # 상태 메시지 전송
                    tool_usage = output.get("last_tool_usage")
                    if tool_usage:
                        yield _make_sse("status", message=tool_usage, node=node_name)
                    else:
                        retry_msg = _build_retry_message(node_name, current_retry, last_reason)
                        status_msg = retry_msg or _NODE_MESSAGES.get(node_name)
                        if status_msg:
                            yield _make_sse("status", message=status_msg, node=node_name)

                    # 최종 결과 전송 (generate_report 또는 general_chat)
                    if node_name in ("generate_report", "general_chat"):
                        # AIMessage 등 직렬화 불가능한 객체 제거
                        safe_output = output.copy()
                        safe_output.pop("messages", None)
                        
                        final_data = {
                            "ok": True,
                            "agent": "sql" if node_name == "generate_report" else "general",
                            "session_id": thread_id,
                            "data": {
                                "report": output.get("report", ""),
                                "suggested_actions": output.get("suggested_actions", []),
                                "raw": safe_output,
                            },
                        }
                        yield _make_sse("result", payload=final_data)

        except Exception as e:
            import traceback
            error_trace = traceback.format_exc()
            logger.error("STREAM_ERROR: %s\n%s", e, error_trace)
            yield _make_sse("error", message=f"서버 에러: {str(e)}")

    return StreamingResponse(event_generator(), media_type="text/event-stream")
