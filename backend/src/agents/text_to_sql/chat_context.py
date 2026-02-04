"""세션 대화 컨텍스트 구성 및 요약 갱신."""


import logging
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
    세션 ID에 대한 채팅 컨텍스트(요약 + 최근 메시지)를 문자열로 구성하여 반환합니다.
    (Tool Call 시 LLM에게 제공할 정보)
    """
    context_prefix = ""
    
    # 1. 요약본 조회
    summary, _, _ = await get_summary_state(session_id)
    # 2. 최근 대화 조회 (최대 2개)
    recent_messages = await get_recent_messages(session_id, limit=2)
    
    if summary or recent_messages:
        context_prefix += "관련 대화 컨텍스트:\n"
        if summary:
            # 요약이 있으면 포함
            context_prefix += f"[Conversation Summary]\n{summary}\n\n"
        else:
            context_prefix += "[Conversation Summary]\n(없음)\n\n"
        
        if recent_messages:
            context_prefix += "[Recent Messages]\n"
            for msg in recent_messages:
                context_prefix += f"- {msg['role']}: {msg['content']}\n"
        else:
            context_prefix += "[Recent Messages]\n(없음)\n"
            
    return context_prefix


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
