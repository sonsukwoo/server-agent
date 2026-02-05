"""스키마 관리 오케스트레이터 (API와 로직 연결)."""
import logging
from .sync import sync_schema_embeddings_mcp
from .listener import SchemaListener
from .trigger_setup import ensure_event_trigger

logger = logging.getLogger("SCHEMA_ORCHESTRATOR")

_listener: SchemaListener | None = None

async def run_once():
    """스키마 동기화 수동 실행."""
    logger.info("Manual schema sync requested")
    await sync_schema_embeddings_mcp()

async def start_listener():
    """리스너 설정 및 시작."""
    global _listener
    if _listener:
        logger.info("Listener already running")
        return

    # 트리거 확인 및 생성
    trigger_ready = await ensure_event_trigger()
    
    if not trigger_ready:
        logger.warning("Event trigger is NOT ready. Listener might receive nothing.")

    logger.info("Starting schema listener...")
    # 동기화 콜백 등록
    _listener = SchemaListener(callback=sync_schema_embeddings_mcp)
    await _listener.start()

async def stop_listener():
    """리스너 종료."""
    global _listener
    if _listener:
        logger.info("Stopping schema listener...")
        await _listener.stop()
        _listener = None
