import asyncio
import logging
import asyncpg
from typing import Callable, Awaitable
from config.settings import settings

logger = logging.getLogger("SCHEMA_LISTENER")

class SchemaListener:
    """
    PostgreSQL의 LISTEN/NOTIFY 기능을 사용하여 스키마 변경 이벤트를 실시간으로 감지하고,
    등록된 콜백 함수(임베딩 동기화 등)를 실행하는 클래스.
    """

    def __init__(self, callback: Callable[[], Awaitable[None]]):
        self.callback = callback
        self.running = False
        self.task = None
        self.conn = None
        self.channel = settings.schema_notify_channel  # settings에서 채널명 로드
        self.trigger_name = settings.schema_trigger_name
        
        # DB 접속 정보 (settings에서 가져옴)
        self.dsn = f"postgresql://{settings.db_user}:{settings.db_password}@{settings.db_host}:{settings.db_port}/{settings.db_name}"

    async def start(self):
        """리스너 시작 (Background Task)"""
        if self.running:
            return
        
        self.running = True
        self.task = asyncio.create_task(self._listen_loop())
        # 구체적인 성공/실패 여부는 _listen_loop 내부에서 로그로 남김

    async def stop(self):
        """리스너 종료"""
        self.running = False
        if self.task:
            self.task.cancel()
            try:
                await self.task
            except asyncio.CancelledError:
                pass
            self.task = None  # 명시적 초기화
        
        if self.conn:
            try:
                await self.conn.close()
                logger.info("SchemaListener connection closed")
            except Exception as e:
                logger.warning("Error closing SchemaListener connection: %s", e)
            self.conn = None

    async def _listen_loop(self):
        """LISTEN 루프 (재연결 로직 포함)"""
        while self.running:
            try:
                # 1. DB 연결
                self.conn = await asyncpg.connect(self.dsn)
                
                # 2. 트리거 존재 여부 확인
                # 트리거가 없으면 리스닝 불필요 -> 종료
                if not await self._check_event_trigger_exists():
                    logger.info("SchemaListener: Trigger '%s' NOT FOUND. Listener DEACTIVATED.", self.trigger_name)
                    self.running = False
                    return 

                # 3. 리스닝 시작
                await self.conn.add_listener(self.channel, self._on_notification)
                logger.info("SchemaListener: ACTIVATED (listening on channel '%s')", self.channel)
                
                # 4. 연결 유지 (무한 대기)
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
        """이벤트 수신 시 콜백 실행"""
        logger.info("SchemaListener: Received event on '%s': %s", channel, payload)
        # 콜백 실행 (비동기 태스크로 스케줄링)
        asyncio.create_task(self._run_callback())

    async def _run_callback(self):
        """콜백 래퍼 (에러 핸들링 포함)"""
        try:
            logger.info("SchemaListener: Triggering sync callback...")
            await self.callback()
        except Exception as e:
            logger.error("SchemaListener: Callback execution failed: %s", e)

    async def _check_event_trigger_exists(self) -> bool:
        """
        실제 트리거 존재 여부를 확인합니다.
        (pg_trigger 시스템 카탈로그 조회, 트리거 이름 기준)
        """
        try:
            sql = "SELECT count(*) FROM pg_event_trigger WHERE evtname = $1"
            val = await self.conn.fetchval(sql, self.trigger_name)
            return val > 0
        except Exception as e:
            logger.warning("SchemaListener: Trigger check failed: %s", e)
            return False
