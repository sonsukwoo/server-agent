"""세션 대화 컨텍스트 구성 및 요약 갱신."""


import logging
import json
from typing import Optional, List, Dict, Any
from langchain_openai import ChatOpenAI

from src.db.chat_context import (
    get_recent_messages,
    get_summary_state,
    get_messages_to_summarize,
    update_summary,
)
from src.agents.text_to_sql.prompts import SUMMARY_PROMPT_TEMPLATE
from config.settings import settings

logger = logging.getLogger("CHAT_HISTORY_TOOL")

async def get_chat_context(session_id: str) -> str:
    """
    세션 ID에 대한 채팅 컨텍스트(요약 + 최근 메시지 + 이전 SQL/결과)를 문자열로 구성하여 반환합니다.
    (Tool Call 시 LLM에게 제공할 정보)
    """
    context_parts = []
    
    # 1. 요약본 조회
    summary, _, _ = await get_summary_state(session_id)
    # 2. 최근 대화 조회 (최대 4개)
    recent_messages = await get_recent_messages(session_id, limit=4)
    
    if summary or recent_messages:
        context_parts.append("### 관련 대화 컨텍스트")
        
        # 요약본 추가
        if summary:
            context_parts.append(f"[Conversation Summary]\n{summary}")
        
        # 최근 메시지 추가 (SQL 결과 및 쿼리 포함)
        if recent_messages:
            context_parts.append("[Recent Messages]")
            last_sql_query = None
            last_sql_result = None
            
            for msg in recent_messages:
                role = msg['role']
                content = msg['content']
                payload = msg.get('payload_json')
                
                context_parts.append(f"- {role}: {content}")
                
                # assistant 메시지에 SQL 관련 정보가 있으면 저장
                if role == 'assistant' and payload:
                    # payload가 문자열이면 파싱 (DB에서 문자열로 가져오는 경우 대응)
                    if isinstance(payload, str):
                        try:
                            payload = json.loads(payload)
                        except Exception:
                            logger.error(f"Failed to parse payload_json: {payload[:100]}")
                            payload = None
                    
                    if payload and isinstance(payload, dict):
                        if payload.get('generated_sql'):
                            last_sql_query = payload.get('generated_sql')
                        if payload.get('sql_result'):
                            last_sql_result = payload.get('sql_result')
            
            # 마지막 SQL 쿼리/결과 명시 (참조 질문 지원)
            if last_sql_query or last_sql_result:
                context_parts.append("\n[Previous Query Context]")
                
                # SQL에서 테이블 이름 추출
                if last_sql_query:
                    tables = _extract_tables_from_sql(last_sql_query)
                    if tables:
                        context_parts.append(f"사용된 테이블: {', '.join(tables)}")
                    context_parts.append(f"사용된 SQL:\n```sql\n{last_sql_query}\n```")
                
                if last_sql_result and isinstance(last_sql_result, list) and len(last_sql_result) > 0:
                    result_preview = last_sql_result[:3]
                    result_str = "\n".join([str(row) for row in result_preview])
                    context_parts.append(f"결과 샘플 ({len(last_sql_result)}행 중 3행):\n{result_str}")
    
    return "\n".join(context_parts)


def _extract_tables_from_sql(sql: str) -> list[str]:
    """SQL 쿼리에서 테이블 이름을 추출."""
    import re
    tables = []
    # FROM/JOIN 뒤의 테이블 이름 추출 (schema.table 형식 지원)
    pattern = r'(?:FROM|JOIN)\s+([a-zA-Z_][a-zA-Z0-9_]*(?:\.[a-zA-Z_][a-zA-Z0-9_]*)?)'
    matches = re.findall(pattern, sql, re.IGNORECASE)
    for match in matches:
        if match.upper() not in ('SELECT', 'WHERE', 'AND', 'OR', 'ON', 'AS'):
            tables.append(match)
    return list(set(tables))


async def run_background_summarization(session_id: str):
    """
    [Background Task] 
    오래된 대화가 일정량 쌓이면 요약을 수행하고 DB를 갱신합니다.
    """
    try:
        # 1. 요약 상태/커서 조회
        summary, last_message_id, last_created_at = await get_summary_state(session_id)

        # 2. 요약 대상 조회 (최근 2개 제외 + 커서 이후)
        messages_to_summarize = await get_messages_to_summarize(
            session_id,
            recent_limit=2,
            after_message_id=last_message_id,
            after_created_at=last_created_at,
            limit=30,
        )
        
        # 4개 이상 쌓였을 때만 요약 실행 (너무 잦은 요약 방지)
        if len(messages_to_summarize) < 4:
            return

        # 3. 기존 요약 조회
        current_summary = summary or "없음"

        # 4. LLM 요약 수행
        llm = ChatOpenAI(model_name=settings.model_fast, temperature=0) # 빠르고 저렴한 모델
        
        # 대화 내용을 문자열로 변환
        new_messages_text = ""
        for m in messages_to_summarize:
            role = m["role"]
            content = m["content"]
            new_messages_text += f"- {role}: {content}\n"

        prompt = SUMMARY_PROMPT_TEMPLATE.format(
            current_summary=current_summary,
            new_messages=new_messages_text
        )
        
        result = await llm.ainvoke(prompt)
        new_summary = result.content.strip()

        # 5. DB 업데이트 (커서 갱신)
        last_msg = messages_to_summarize[-1]
        await update_summary(
            session_id,
            new_summary,
            last_message_id=last_msg.get("id"),
            last_created_at=last_msg.get("created_at"),
        )
        logger.info(f"Session {session_id} summary updated.")
        
    except Exception as e:
        logger.error(f"Background summarization failed for {session_id}: {e}")
