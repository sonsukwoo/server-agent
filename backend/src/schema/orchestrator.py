"""
스키마 관리 오케스트레이터
(API 레이어와 Schema 로직 사이의 단일 진입점)
"""
import logging
from .sync import sync_schema_embeddings_mcp
from .listener import SchemaListener

logger = logging.getLogger("SCHEMA_ORCHESTRATOR")

_listener: SchemaListener | None = None

async def run_once():
    """스키마 동기화 1회 실행"""
    logger.info("Manual schema sync requested")
    await sync_schema_embeddings_mcp()

async def start_listener():
    """리스너 생성 및 시작"""
    global _listener
    if _listener:
        logger.info("Listener already running")
        return

    logger.info("Starting schema listener...")
    # 콜백으로 run_once(동기화 로직) 주입
    _listener = SchemaListener(callback=sync_schema_embeddings_mcp)
    await _listener.start()

async def stop_listener():
    """리스너 종료"""
    global _listener
    if _listener:
        logger.info("Stopping schema listener...")
        await _listener.stop()
        _listener = None
