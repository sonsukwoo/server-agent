"""질의 처리 및 스트리밍 응답 API."""
import json
import logging
from typing import Optional
from fastapi import APIRouter, HTTPException, BackgroundTasks
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
import asyncio

from src.agents.text_to_sql import app as sql_app
from src.agents.text_to_sql.middleware.input_guard import InputGuard
from src.agents.text_to_sql.chat_context import (
    get_chat_context,
    run_background_summarization,
)

logger = logging.getLogger("API_QUERY")

router = APIRouter(tags=["query"])

class QueryRequest(BaseModel):
    agent: str  # "sql" 등
    question: str
    session_id: Optional[str] = None # 세션 ID

class QueryResponse(BaseModel):
    ok: bool
    agent: str
    data: dict | None = None
    error: str | None = None

@router.post("/query")
async def query(body: QueryRequest, background_tasks: BackgroundTasks):
    """자연어 질문 처리 API (SSE 스트리밍)."""
    agent_type = body.agent.lower().strip()
    question = body.question.strip()
    session_id = body.session_id

    # 1. 입력 검증
    if not question:
        raise HTTPException(status_code=400, detail="질문이 비어있습니다")
    
    is_valid, error = InputGuard.validate(question)
    if not is_valid:
        raise HTTPException(status_code=400, detail=error)

    # 컨텍스트 로드 및 질문 결합
    base_full_question = question
    context_prefix = ""
    if session_id:
        try:
            context_prefix = await get_chat_context(session_id)
            if context_prefix:
                base_full_question = f"{context_prefix}\n{question}"
        except Exception:
            logger.exception("Failed to load chat context; proceeding without it.")

    # 프론트엔드 상태 표시용 매핑
    node_messages = {
        "parse_request": "사용자 질문 분석 중",
        "validate_request": "질문 유효성 검증 중",
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

    async def event_generator():
        if agent_type == "sql":
            full_question = base_full_question
            user_constraints = ""

            initial_state = {
                "user_question": full_question,
                "user_constraints": user_constraints,
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
            }
            
            last_reason = ""
            current_retry = 0
            try:
                # LangGraph 실행 및 스트리밍
                async for event in sql_app.astream(initial_state):
                    # 이벤트 루프 양보 (단일 프로세스 효율성)
                    await asyncio.sleep(0)
                    
                    for node_name, output in event.items():
                        # 상태 정보 업데이트
                        if "validation_reason" in output:
                            last_reason = output["validation_reason"]
                        
                        if "sql_retry_count" in output:
                            current_retry = output.get("sql_retry_count", 0)
                        elif "validation_retry_count" in output:
                            current_retry = output.get("validation_retry_count", 0)
                        
                        # 상태 메시지 결정
                        status_msg = node_messages.get(node_name)
                        
                        # Tool 사용 로그 우선
                        tool_usage = output.get("last_tool_usage")
                        if tool_usage:
                            yield f"data: {json.dumps({'type': 'status', 'message': tool_usage, 'node': node_name}, ensure_ascii=False)}\n\n"
                        elif status_msg:
                            # 재시도 중일 때 상세 메시지
                            if node_name == "generate_sql" and current_retry > 0:
                                if last_reason:
                                    status_msg = f"피드백 반영하여 SQL 재작성 중 (사유: {last_reason})"
                                else:
                                    status_msg = f"오류 복구 및 SQL 재작성 중... [재시도 {current_retry}]"
                            
                            yield f"data: {json.dumps({'type': 'status', 'message': status_msg, 'node': node_name}, ensure_ascii=False)}\n\n"
                        
                        # 최종 결과 전송
                        if node_name == "generate_report":
                            final_data = {
                                "ok": True,
                                "agent": "sql",
                                "data": {
                                    "report": output.get("report", ""),
                                    "suggested_actions": output.get("suggested_actions", []),
                                    "raw": output
                                }
                            }
                            yield f"data: {json.dumps({'type': 'result', 'payload': final_data}, ensure_ascii=False)}\n\n"
                
                # 백그라운드 요약 작업 예약
                if session_id:
                    background_tasks.add_task(run_background_summarization, session_id)

            except Exception as e:
                logger.error("STREAM_ERROR: %s", e)
                yield f"data: {json.dumps({'type': 'error', 'message': str(e)}, ensure_ascii=False)}\n\n"
        else:
            yield f"data: {json.dumps({'type': 'error', 'message': f'지원하지 않는 에이전트 타입입니다: {agent_type}'}, ensure_ascii=False)}\n\n"

    return StreamingResponse(event_generator(), media_type="text/event-stream")
