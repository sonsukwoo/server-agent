"""채팅 컨텍스트(메시지/요약) 조회 및 업데이트.

Note: 수동 컨텍스트 조립 함수(`get_messages_before_recent`, `get_messages_to_summarize`)는
     LangGraph Checkpointer 도입으로 제거되었습니다.
     이 모듈은 UI 표시용 데이터 조회/갱신만 담당합니다.
"""

import json
import logging
from typing import Optional

from src.db.db_manager import db_manager

logger = logging.getLogger("DB_CHAT_CONTEXT")


async def get_recent_messages(
    session_id: str, limit: int = 4
) -> list[dict[str, str]]:
    """최근 N개 메시지 조회 (UI 표시용)."""
    pool = await db_manager.get_pool()
    async with pool.acquire() as conn:
        db_manager._log_pool_usage(pool, "acquire")
        rows = await conn.fetch(
            """
            SELECT role, content, payload_json
            FROM chat.messages
            WHERE session_id = $1
            ORDER BY created_at DESC
            LIMIT $2
            """,
            session_id,
            limit,
        )
    return [
        {
            "role": r["role"],
            "content": r["content"],
            "payload_json": (
                json.loads(r["payload_json"])
                if isinstance(r["payload_json"], str)
                else r["payload_json"]
            )
            if r["payload_json"]
            else None,
        }
        for r in reversed(rows)
    ]


async def update_summary(
    session_id: str,
    summary: str,
    last_message_id: Optional[str] = None,
    last_created_at: Optional[str] = None,
) -> None:
    """세션 요약 내용 갱신 (UI 사이드바 표시용)."""
    pool = await db_manager.get_pool()
    async with pool.acquire() as conn:
        db_manager._log_pool_usage(pool, "acquire")
        await conn.execute(
            """
            UPDATE chat.sessions
            SET summary = $2,
                summary_updated_at = now(),
                summary_last_message_id = COALESCE($3::uuid, summary_last_message_id),
                summary_last_created_at = COALESCE($4::timestamptz, summary_last_created_at),
                updated_at = now()
            WHERE id = $1
            """,
            session_id,
            summary,
            last_message_id,
            last_created_at,
        )
