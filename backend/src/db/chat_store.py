import logging
import json
import uuid
from typing import Optional, List, Dict, Any
from datetime import datetime
import asyncpg
from config.settings import settings

logger = logging.getLogger("CHAT_STORE")

class ChatStore:
    def __init__(self):
        # reuse connection string from settings (same as other parts)
        self.dsn = f"postgresql://{settings.db_user}:{settings.db_password}@{settings.db_host}:{settings.db_port}/{settings.db_name}"

    async def ensure_chat_schema(self):
        """서버 시작 시 스키마 및 테이블 생성"""
        conn = await asyncpg.connect(self.dsn)
        try:
            async with conn.transaction():
                # pgcrypto for gen_random_uuid()
                await conn.execute("CREATE EXTENSION IF NOT EXISTS pgcrypto;")
                # 1. Schema
                await conn.execute("CREATE SCHEMA IF NOT EXISTS chat;")

                # 2. Sessions Table
                await conn.execute("""
                    CREATE TABLE IF NOT EXISTS chat.sessions (
                        id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                        title TEXT NOT NULL DEFAULT 'New Chat',
                        created_at TIMESTAMPTZ DEFAULT now(),
                        updated_at TIMESTAMPTZ DEFAULT now()
                    );
                """)

                # 3. Messages Table
                await conn.execute("""
                    CREATE TABLE IF NOT EXISTS chat.messages (
                        id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                        session_id UUID NOT NULL REFERENCES chat.sessions(id) ON DELETE CASCADE,
                        role TEXT NOT NULL,
                        content TEXT,
                        payload_json JSONB,
                        created_at TIMESTAMPTZ DEFAULT now()
                    );
                """)

                # 4. Indexes
                await conn.execute("""
                    CREATE INDEX IF NOT EXISTS idx_messages_session_id ON chat.messages(session_id);
                    CREATE INDEX IF NOT EXISTS idx_messages_created_at ON chat.messages(created_at);
                    CREATE INDEX IF NOT EXISTS idx_sessions_updated_at ON chat.sessions(updated_at DESC);
                """)
                
            logger.info("Chat schema and tables ensured.")
        except Exception as e:
            logger.error(f"Failed to ensure chat schema: {e}")
            raise
        finally:
            await conn.close()

    async def list_sessions(self, limit: int = 50) -> List[Dict[str, Any]]:
        """세션 목록 조회 (최신순)"""
        conn = await asyncpg.connect(self.dsn)
        try:
            rows = await conn.fetch("""
                SELECT id, title, created_at, updated_at 
                FROM chat.sessions 
                ORDER BY updated_at DESC 
                LIMIT $1
            """, limit)
            return [
                {
                    "id": str(r["id"]),
                    "title": r["title"],
                    "created_at": r["created_at"].isoformat(),
                    "updated_at": r["updated_at"].isoformat(),
                }
                for r in rows
            ]
        finally:
            await conn.close()

    async def create_session(self, title: str = "New Chat") -> Dict[str, Any]:
        """새 세션 생성"""
        conn = await asyncpg.connect(self.dsn)
        try:
            row = await conn.fetchrow("""
                INSERT INTO chat.sessions (title) 
                VALUES ($1) 
                RETURNING id, title, created_at, updated_at
            """, title)
            return {
                "id": str(row["id"]),
                "title": row["title"],
                "created_at": row["created_at"].isoformat(),
                "updated_at": row["updated_at"].isoformat(),
            }
        finally:
            await conn.close()

    async def get_session(self, session_id: str) -> Optional[Dict[str, Any]]:
        """세션 상세 조회 (메시지 포함)"""
        conn = await asyncpg.connect(self.dsn)
        try:
            # 세션 존재 확인
            session_row = await conn.fetchrow("SELECT * FROM chat.sessions WHERE id = $1", session_id)
            if not session_row:
                return None
            
            # 메시지 조회
            messages = await conn.fetch("""
                SELECT id, role, content, payload_json, created_at
                FROM chat.messages
                WHERE session_id = $1
                ORDER BY created_at ASC
            """, session_id)

            session_data = dict(session_row)
            session_data["messages"] = [
                {
                    **dict(m),
                    "payload_json": (
                        json.loads(m["payload_json"])
                        if isinstance(m["payload_json"], str)
                        else m["payload_json"]
                    )
                    if m["payload_json"]
                    else None,
                    "id": str(m["id"]),
                    "created_at": m["created_at"].isoformat()
                } 
                for m in messages
            ]
            session_data["id"] = str(session_data["id"])
            session_data["created_at"] = session_data["created_at"].isoformat()
            session_data["updated_at"] = session_data["updated_at"].isoformat()

            return session_data
        finally:
            await conn.close()
    
    async def save_message(self, session_id: str, role: str, content: str, payload: Optional[Dict] = None) -> Dict[str, Any]:
        """메시지 저장 및 세션 업데이트 시간 갱신"""
        conn = await asyncpg.connect(self.dsn)
        try:
            async with conn.transaction():
                # 메시지 저장
                payload_json = json.dumps(payload) if payload else None
                msg_row = await conn.fetchrow("""
                    INSERT INTO chat.messages (session_id, role, content, payload_json)
                    VALUES ($1, $2, $3, $4)
                    RETURNING id, created_at
                """, session_id, role, content, payload_json)
                
                # 세션 updated_at 갱신 + 최초 사용자 질문으로 제목 설정
                if role == "user":
                    title = (content or "").strip()
                    if title:
                        truncated = title if len(title) <= 15 else f"{title[:15]}..."
                        await conn.execute("""
                            UPDATE chat.sessions
                            SET updated_at = now(),
                                title = CASE
                                    WHEN title = 'New Chat' THEN $2
                                    ELSE title
                                END
                            WHERE id = $1
                        """, session_id, truncated)
                    else:
                        await conn.execute("""
                            UPDATE chat.sessions
                            SET updated_at = now()
                            WHERE id = $1
                        """, session_id)
                else:
                    await conn.execute("""
                        UPDATE chat.sessions
                        SET updated_at = now()
                        WHERE id = $1
                    """, session_id)
                
                return {
                    "id": str(msg_row["id"]),
                    "created_at": msg_row["created_at"].isoformat()
                }
        finally:
            await conn.close()

    async def delete_session(self, session_id: str) -> bool:
        """세션 삭제 (Cascade로 메시지도 자동 삭제됨)"""
        conn = await asyncpg.connect(self.dsn)
        try:
            result = await conn.execute("DELETE FROM chat.sessions WHERE id = $1", session_id)
            # result format: 'DELETE 1'
            deleted_count = int(result.split(" ")[1])
            return deleted_count > 0
        finally:
            await conn.close()

chat_store = ChatStore()
