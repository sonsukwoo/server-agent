"""고급 설정 알림 리스너 모듈."""

import asyncio
import logging
import json
import asyncpg
from config.settings import settings

logger = logging.getLogger("ALERT_LISTENER")

class AlertListener:
    """PostgreSQL LISTEN/NOTIFY를 이용한 실시간 알림 리스너."""
    
    def __init__(self):
        """초기화 및 DB 연결 정보 설정."""
        self._conn = None
        self._task = None
        self.running = False
        self.dsn = f"postgresql://{settings.db_user}:{settings.db_password}@{settings.db_host}:{settings.db_port}/{settings.db_name}"

    async def start(self):
        """리스너 비동기 태스크 시작."""
        self.running = True
        self._task = asyncio.create_task(self._listen_loop())

    async def stop(self):
        """리스너 종료 및 리소스 정리."""
        self.running = False
        if self._conn:
            await self._conn.close()
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass

    async def _listen_loop(self):
        """DB 채널 구독 및 알림 대기 루프."""
        while self.running:
            try:
                # 리스너 전용 커넥션 생성
                self._conn = await asyncpg.connect(self.dsn)
                await self._conn.add_listener("alert_channel", self._on_notification)
                
                # 연결 유지
                while self.running:
                    await asyncio.sleep(1)
            except Exception as e:
                logger.error(f"Alert Listener 오류: {e}")
                await asyncio.sleep(5)
            finally:
                if self._conn and not self._conn.is_closed():
                    await self._conn.close()

    def _on_notification(self, connection, pid, channel, payload):
        """알림 수신 시 로그 출력 처리."""
        try:
            data = json.loads(payload)
            logger.info(f"🔔 [알림] 규칙 ID {data.get('rule_id')}: {data.get('message')} (값: {data.get('value')})")
        except:
            logger.info(f"🔔 [알림] 원본 데이터: {payload}")
