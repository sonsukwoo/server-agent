"""DB 스키마 변경 감지 리스너 (PostgreSQL LISTEN/NOTIFY)."""

import asyncio
import logging
import asyncpg
from typing import Callable, Awaitable
from config.settings import settings

logger = logging.getLogger("SCHEMA_LISTENER")

class SchemaListener:
    """스키마 변경 이벤트를 감지하여 콜백(동기화)을 실행하는 클래스."""

    def __init__(self, callback: Callable[[], Awaitable[None]]):
        self.callback = callback
        self.running = False
        self.task = None
        self.conn = None
        self.channel = settings.schema_notify_channel
        self.trigger_name = settings.schema_trigger_name
        self.dsn = f"postgresql://{settings.db_user}:{settings.db_password}@{settings.db_host}:{settings.db_port}/{settings.db_name}"

    async def start(self):
        """리스너 백그라운드 작업 시작."""
        if self.running:
            return
        
        self.running = True
        self.task = asyncio.create_task(self._listen_loop())

    async def stop(self):
        """리스너 작업 종료 및 리소스 정리."""
        self.running = False
        if self.task:
            self.task.cancel()
            try:
                await self.task
            except asyncio.CancelledError:
                pass
            self.task = None
        
        if self.conn:
            try:
                await self.conn.close()
                logger.info("SchemaListener connection closed")
            except Exception as e:
                logger.warning("Error closing SchemaListener connection: %s", e)
            self.conn = None

    async def _listen_loop(self):
        """DB 연결 및 알림 대기 루프 (재연결 지원)."""
        while self.running:
            try:
                # 1. DB 연결
                self.conn = await asyncpg.connect(self.dsn)
                
                # 2. 트리거 확인 (없으면 중단)
                if not await self._check_event_trigger_exists():
                    logger.info("SchemaListener: Trigger '%s' NOT FOUND. Listener DEACTIVATED.", self.trigger_name)
                    self.running = False
                    return 

                # 3. 리스닝 시작
                await self.conn.add_listener(self.channel, self._on_notification)
                logger.info("SchemaListener: ACTIVATED (listening on channel '%s')", self.channel)
                
                # 4. 연결 유지
                while self.running:
                    await asyncio.sleep(1)
                    if self.conn.is_closed():
                        raise ConnectionError("DB Connection closed unexpected")

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error("SchemaListener loop error: %s. Retrying in 5s...", e)
                await asyncio.sleep(5)
            finally:
                if self.conn and not self.conn.is_closed():
                    try:
                        await self.conn.close()
                    except:
                        pass
                self.conn = None

    def _on_notification(self, connection, pid, channel, payload):
        """이벤트 수신 시 콜백 스케줄링."""
        logger.info("SchemaListener: Received event on '%s': %s", channel, payload)
        asyncio.create_task(self._run_callback())

    async def _run_callback(self):
        """콜백 실행 래퍼 (에러 처리)."""
        try:
            logger.info("SchemaListener: Triggering sync callback...")
            await self.callback()
        except Exception as e:
            logger.error("SchemaListener: Callback execution failed: %s", e)

    async def _check_event_trigger_exists(self) -> bool:
        """트리거 존재 여부 확인."""
        try:
            sql = "SELECT count(*) FROM pg_event_trigger WHERE evtname = $1"
            val = await self.conn.fetchval(sql, self.trigger_name)
            return val > 0
        except Exception as e:
            logger.warning("SchemaListener: Trigger check failed: %s", e)
            return False
