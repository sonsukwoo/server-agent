import asyncio
import logging
import json
import asyncpg
from config.settings import settings

logger = logging.getLogger("ALERT_LISTENER")

class AlertListener:
    def __init__(self):
        self._conn = None
        self._task = None
        self.running = False
        self.dsn = f"postgresql://{settings.db_user}:{settings.db_password}@{settings.db_host}:{settings.db_port}/{settings.db_name}"

    async def start(self):
        self.running = True
        self._task = asyncio.create_task(self._listen_loop())

    async def stop(self):
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
        while self.running:
            try:
                # Listener needs a dedicated specific connection, not a pool
                self._conn = await asyncpg.connect(self.dsn)
                await self._conn.add_listener("alert_channel", self._on_notification)
                while self.running:
                    await asyncio.sleep(1)
            except Exception as e:
                logger.error(f"Alert Listener Error: {e}")
                await asyncio.sleep(5)
            finally:
                if self._conn and not self._conn.is_closed():
                    await self._conn.close()

    def _on_notification(self, connection, pid, channel, payload):
        try:
            data = json.loads(payload)
            logger.info(f"🔔 [ALERT] Rule {data.get('rule_id')}: {data.get('message')} (Value: {data.get('value')})")
        except:
            logger.info(f"🔔 [ALERT] Raw Payload: {payload}")
