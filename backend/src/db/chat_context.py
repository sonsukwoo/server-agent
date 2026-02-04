"""채팅 컨텍스트(메시지/요약) 조회 및 업데이트."""

import logging
from typing import List, Dict, Optional, Tuple

from src.db.db_manager import db_manager

logger = logging.getLogger("uvicorn.error")


async def get_recent_messages(session_id: str, limit: int = 2) -> List[Dict[str, str]]:
    """최근 N개의 메시지를 시간순으로 반환."""
    pool = await db_manager.get_pool()
    async with pool.acquire() as conn:
        db_manager._log_pool_usage(pool, "acquire")
        rows = await conn.fetch(
            """
            SELECT role, content
            FROM chat.messages
            WHERE session_id = $1
            ORDER BY created_at DESC
            LIMIT $2
            """,
            session_id,
            limit,
        )
    return [{"role": r["role"], "content": r["content"]} for r in reversed(rows)]


async def get_messages_before_recent(
    session_id: str, recent_limit: int = 2
) -> List[Dict[str, str]]:
    """최근 N개를 제외한 나머지 메시지를 시간순으로 반환."""
    pool = await db_manager.get_pool()
    async with pool.acquire() as conn:
        db_manager._log_pool_usage(pool, "acquire")
        rows = await conn.fetch(
            """
            WITH recent AS (
                SELECT id
                FROM chat.messages
                WHERE session_id = $1
                ORDER BY created_at DESC
                LIMIT $2
            )
            SELECT role, content
            FROM chat.messages
            WHERE session_id = $1
              AND id NOT IN (SELECT id FROM recent)
            ORDER BY created_at ASC
            """,
            session_id,
            recent_limit,
        )
    return [{"role": r["role"], "content": r["content"]} for r in rows]


async def get_summary(session_id: str) -> Optional[str]:
    """세션 요약 조회."""
    pool = await db_manager.get_pool()
    async with pool.acquire() as conn:
        db_manager._log_pool_usage(pool, "acquire")
        row = await conn.fetchrow(
            "SELECT summary FROM chat.sessions WHERE id = $1", session_id
        )
    return row["summary"] if row else None


async def get_summary_state(
    session_id: str,
) -> Tuple[Optional[str], Optional[str], Optional[str]]:
    """세션 요약과 커서(마지막 요약 메시지)를 함께 조회."""
    pool = await db_manager.get_pool()
    async with pool.acquire() as conn:
        db_manager._log_pool_usage(pool, "acquire")
        row = await conn.fetchrow(
            """
            SELECT summary,
                   summary_last_message_id,
                   summary_last_created_at
            FROM chat.sessions
            WHERE id = $1
            """,
            session_id,
        )
    if not row:
        return None, None, None
    return (
        row["summary"],
        str(row["summary_last_message_id"]) if row["summary_last_message_id"] else None,
        row["summary_last_created_at"].isoformat()
        if row["summary_last_created_at"]
        else None,
    )


async def get_messages_to_summarize(
    session_id: str,
    recent_limit: int = 2,
    after_message_id: Optional[str] = None,
    after_created_at: Optional[str] = None,
    limit: int = 30,
) -> List[Dict[str, str]]:
    """요약 대상 메시지(최근 N개 제외, 커서 이후)를 시간순으로 반환."""
    pool = await db_manager.get_pool()
    async with pool.acquire() as conn:
        db_manager._log_pool_usage(pool, "acquire")
        rows = await conn.fetch(
            """
            WITH recent AS (
                SELECT id
                FROM chat.messages
                WHERE session_id = $1
                ORDER BY created_at DESC
                LIMIT $2
            )
            SELECT id, role, content, created_at
            FROM chat.messages
            WHERE session_id = $1
              AND id NOT IN (SELECT id FROM recent)
              AND (
                  ($3::uuid IS NULL AND $4::timestamptz IS NULL)
                  OR ($3::uuid IS NOT NULL AND id > $3::uuid)
                  OR ($3::uuid IS NULL AND $4::timestamptz IS NOT NULL AND created_at > $4::timestamptz)
              )
            ORDER BY created_at ASC
            LIMIT $5
            """,
            session_id,
            recent_limit,
            after_message_id,
            after_created_at,
            limit,
        )
    return [
        {
            "id": str(r["id"]),
            "role": r["role"],
            "content": r["content"],
            "created_at": r["created_at"].isoformat() if r["created_at"] else None,
        }
        for r in rows
    ]


async def update_summary(
    session_id: str,
    summary: str,
    last_message_id: Optional[str] = None,
    last_created_at: Optional[str] = None,
) -> None:
    """세션 요약 갱신 (커서 포함)."""
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
