"""DB 연결 풀 관리 및 데이터 접근 로직."""

import logging
import json
from typing import Optional, List, Dict, Any
import asyncpg
from config.settings import settings

logger = logging.getLogger("uvicorn.error")

class DBManager:
    """PostgreSQL 연결 풀 및 비즈니스 데이터 접근 클래스."""

    def __init__(self):
        """DSN 설정 및 풀 변수 초기화."""
        self.dsn = f"postgresql://{settings.db_user}:{settings.db_password}@{settings.db_host}:{settings.db_port}/{settings.db_name}"
        self._pool = None

    async def get_pool(self):
        """비동기 DB 연결 풀 생성 및 반환 (지연 초기화)."""
        if self._pool is None:
            self._pool = await asyncpg.create_pool(
                self.dsn, 
                min_size=settings.db_pool_min, 
                max_size=settings.db_pool_max
            )
            logger.info(
                "DB pool initialized (min=%s, max=%s)",
                settings.db_pool_min,
                settings.db_pool_max,
            )
        return self._pool

    def _log_pool_usage(self, pool, tag: str = "usage") -> None:
        """연결 풀 사용량 로깅 (디버깅용)."""
        try:
            holders = getattr(pool, "_holders", None)
            if holders is not None:
                used = sum(1 for h in holders if getattr(h, "_in_use", False))
                total = len(holders)
                logger.info("DB pool %s: used=%s total=%s", tag, used, total)
                return
            size = pool.get_size() if hasattr(pool, "get_size") else None
            free = pool._queue.qsize() if hasattr(pool, "_queue") else None
            if size is not None and free is not None:
                logger.info("DB pool %s: used=%s total=%s", tag, size - free, size)
        except Exception:
            pass

    async def ensure_schema(self):
        """서버 시작 시 필수 스키마 및 테이블 생성."""
        pool = await self.get_pool()
        async with pool.acquire() as conn:
            self._log_pool_usage(pool, "acquire")
            try:
                async with conn.transaction():
                    # UUID 생성을 위한 확장 기능
                    await conn.execute("CREATE EXTENSION IF NOT EXISTS pgcrypto;")
                    
                    # 1. 스키마 생성
                    await conn.execute("CREATE SCHEMA IF NOT EXISTS chat;")

                    # 2. 세션 테이블 (채팅방)
                    await conn.execute("""
                        CREATE TABLE IF NOT EXISTS chat.sessions (
                            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                            title TEXT NOT NULL DEFAULT 'New Chat',
                            summary TEXT, -- 대화 요약
                            summary_updated_at TIMESTAMPTZ, -- 요약 갱신 시각
                            summary_last_message_id UUID, -- 요약 마지막 메시지 ID
                            summary_last_created_at TIMESTAMPTZ, -- 요약 마지막 메시지 시각
                            created_at TIMESTAMPTZ DEFAULT now(),
                            updated_at TIMESTAMPTZ DEFAULT now()
                        );
                    """)

                    # 마이그레이션 (개발 단계 편의용)
                    try:
                        await conn.execute("ALTER TABLE chat.sessions ADD COLUMN IF NOT EXISTS summary TEXT;")
                        await conn.execute("ALTER TABLE chat.sessions ADD COLUMN IF NOT EXISTS summary_updated_at TIMESTAMPTZ;")
                        await conn.execute("ALTER TABLE chat.sessions ADD COLUMN IF NOT EXISTS summary_last_message_id UUID;")
                        await conn.execute("ALTER TABLE chat.sessions ADD COLUMN IF NOT EXISTS summary_last_created_at TIMESTAMPTZ;")
                    except Exception:
                        pass

                    # 3. 메시지 테이블
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

                    # 4. 인덱스 생성
                    await conn.execute("""
                        CREATE INDEX IF NOT EXISTS idx_messages_session_id ON chat.messages(session_id);
                        CREATE INDEX IF NOT EXISTS idx_messages_created_at ON chat.messages(created_at);
                        CREATE INDEX IF NOT EXISTS idx_sessions_updated_at ON chat.sessions(updated_at DESC);
                    """)
                    
                    # 5. [모니터링] 고급 설정 알림 시스템 스키마
                    await conn.execute("CREATE SCHEMA IF NOT EXISTS monitor;")
                    
                    # 5-1. 알림 규칙 테이블
                    await conn.execute("""
                        CREATE TABLE IF NOT EXISTS monitor.alert_rules (
                            id SERIAL PRIMARY KEY,
                            target_table TEXT NOT NULL,
                            target_column TEXT NOT NULL,
                            operator TEXT NOT NULL,
                            threshold FLOAT NOT NULL,
                            message_template TEXT,
                            created_at TIMESTAMPTZ DEFAULT now()
                        );
                    """)
                    
                    # 5-2. 알림 발생 이력 테이블
                    await conn.execute("""
                        CREATE TABLE IF NOT EXISTS monitor.alert_history (
                            id SERIAL PRIMARY KEY,
                            rule_id INTEGER REFERENCES monitor.alert_rules(id) ON DELETE SET NULL,
                            message TEXT NOT NULL,
                            value FLOAT NOT NULL,
                            created_at TIMESTAMPTZ DEFAULT now()
                        );
                    """)
                    
                logger.info("Database schema and tables ensured.")
            except Exception as e:
                logger.error(f"Failed to ensure schema: {e}")
                raise

    async def list_sessions(self, limit: int = 50) -> List[Dict[str, Any]]:
        """세션 목록 조회 (최신 업데이트순)."""
        pool = await self.get_pool()
        async with pool.acquire() as conn:
            self._log_pool_usage(pool, "acquire")
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

    async def create_session(self, title: str = "New Chat") -> Dict[str, Any]:
        """새 채팅 세션 생성."""
        pool = await self.get_pool()
        async with pool.acquire() as conn:
            self._log_pool_usage(pool, "acquire")
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

    async def get_session(self, session_id: str) -> Optional[Dict[str, Any]]:
        """세션 상세 정보 및 전체 메시지 조회."""
        pool = await self.get_pool()
        async with pool.acquire() as conn:
            self._log_pool_usage(pool, "acquire")
            # 세션 정보 조회
            session_row = await conn.fetchrow("SELECT * FROM chat.sessions WHERE id = $1", session_id)
            if not session_row:
                return None
            
            # 메시지 목록 조회
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
    
    async def save_message(self, session_id: str, role: str, content: str, payload: Optional[Dict] = None) -> Dict[str, Any]:
        """메시지 저장 및 세션 제목/시간 갱신."""
        pool = await self.get_pool()
        async with pool.acquire() as conn:
            self._log_pool_usage(pool, "acquire")
            async with conn.transaction():
                # 메시지 Insert
                payload_json = json.dumps(payload) if payload else None
                msg_row = await conn.fetchrow("""
                    INSERT INTO chat.messages (session_id, role, content, payload_json)
                    VALUES ($1, $2, $3, $4)
                    RETURNING id, created_at
                """, session_id, role, content, payload_json)
                
                # 세션 갱신 (첫 사용자 질문 시 제목 자동 설정)
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

    async def delete_session(self, session_id: str) -> bool:
        """세션 삭제 (연관 메시지도 자동 삭제)."""
        pool = await self.get_pool()
        async with pool.acquire() as conn:
            self._log_pool_usage(pool, "acquire")
            result = await conn.execute("DELETE FROM chat.sessions WHERE id = $1", session_id)
            deleted_count = int(result.split(" ")[1])
            return deleted_count > 0


db_manager = DBManager()
