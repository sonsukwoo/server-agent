"""
/query 엔드포인트 및 SSE 스트리밍 처리
"""
import json
import logging
from typing import Optional, Dict, Any
from fastapi import APIRouter, HTTPException, BackgroundTasks
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
import asyncio

from src.agents.text_to_sql import app as sql_app
from src.agents.text_to_sql.middleware.input_guard import InputGuard
from config.settings import settings
from src.agents.text_to_sql.chat_context import (
    get_chat_context,
    run_background_summarization,
)

logger = logging.getLogger("API_QUERY")

router = APIRouter(tags=["query"])

class QueryRequest(BaseModel):
    agent: str  # "sql" 또는 "ubuntu"
    question: str
    session_id: Optional[str] = None # 세션 컨텍스트 식별자

class QueryResponse(BaseModel):
    ok: bool
    agent: str
    data: dict | None = None
    error: str | None = None

@router.post("/query")
async def query(body: QueryRequest, background_tasks: BackgroundTasks):
    """자연어 질문을 받아서 처리 (스트리밍 지원)"""
    agent_type = body.agent.lower().strip()
    question = body.question.strip()
    session_id = body.session_id

    # 1. 입력 검증
    if not question:
        raise HTTPException(status_code=400, detail="질문이 비어있습니다")
    
    is_valid, error = InputGuard.validate(question)
    if not is_valid:
        raise HTTPException(status_code=400, detail=error)

    # 사용자 질문에 컨텍스트 결합 (Agent에게는 하나의 긴 질문처럼 보임)
    base_full_question = question
    context_prefix = ""
    if session_id:
        try:
            context_prefix = await get_chat_context(session_id)
            if context_prefix:
                base_full_question = f"{context_prefix}\n{question}"
        except Exception:
            logger.exception("Failed to load chat context; proceeding without it.")

    # 노드 이름과 상태 메시지 매핑
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
            # 라우팅: SQL 실행 vs 설명형 응답
            # 라우팅 로직 제거됨 -> 무조건 SQL 에이전트 실행
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
                # LangGraph astream 호출
                async for event in sql_app.astream(initial_state):
                    for node_name, output in event.items():
                        # 상태 업데이트 추적
                        if "validation_reason" in output:
                            last_reason = output["validation_reason"]
                        
                        # 재시도 횟수 업데이트
                        if "sql_retry_count" in output:
                            current_retry = output.get("sql_retry_count", 0)
                        elif "validation_retry_count" in output:
                            current_retry = output.get("validation_retry_count", 0)
                        
                        # 특정 노드가 시작되거나 완료될 때 상태 메시지 전송
                        status_msg = node_messages.get(node_name)
                        if status_msg:
                            # 특수 케이스: generate_sql에서 재시도 중인 경우 상세 사유 포함
                            if node_name == "generate_sql" and current_retry > 0:
                                if last_reason:
                                    # 사유를 짧게 요약하거나 그대로 표시
                                    status_msg = f"피드백 반영하여 SQL 재작성 중 (사유: {last_reason}) [재시도 {current_retry}]"
                                else:
                                    status_msg = f"오류 복구 및 SQL 재작성 중... [재시도 {current_retry}]"
                            
                            yield f"data: {json.dumps({'type': 'status', 'message': status_msg, 'node': node_name}, ensure_ascii=False)}\n\n"
                        
                        # 툴 사용 로그가 있으면 이벤트 전송
                        tool_usage = output.get("last_tool_usage")
                        if tool_usage:
                            tool_msg = f"🛠️ [툴 사용] {tool_usage}"
                            yield f"data: {json.dumps({'type': 'status', 'message': tool_msg, 'node': node_name}, ensure_ascii=False)}\n\n"
                        
                        # 마지막 결과인 경우 전체 데이터 전송
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
                
                # [Background] 응답 완료 후 요약 작업 예약
                if session_id:
                    background_tasks.add_task(run_background_summarization, session_id)

            except Exception as e:
                logger.error("STREAM_ERROR: %s", e)
                yield f"data: {json.dumps({'type': 'error', 'message': str(e)}, ensure_ascii=False)}\n\n"
        else:
            yield f"data: {json.dumps({'type': 'error', 'message': f'지원하지 않는 에이전트 타입입니다: {agent_type}'}, ensure_ascii=False)}\n\n"

    return StreamingResponse(event_generator(), media_type="text/event-stream")
