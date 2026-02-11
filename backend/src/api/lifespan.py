"""FastAPI 앱 수명주기(Lifespan) 관리."""
import logging
from contextlib import asynccontextmanager
from fastapi import FastAPI
from config.settings import settings
from src.db.db_manager import db_manager
from src.db.checkpointer import close_checkpointer
from src.schema.orchestrator import run_once, start_listener, stop_listener
from src.advanced_settings import AlertListener


logger = logging.getLogger("LIFESPAN")

@asynccontextmanager
async def lifespan(app: FastAPI):
    """앱 시작/종료 시 실행될 초기화 및 정리 로직."""
    alert_listener = None
    
    # 1. 채팅 기록 스키마 초기화
    try:
        await db_manager.ensure_schema()
    except Exception as e:
        logger.error("CHAT_STORE: ensure schema failed: %s", e)

    # 2. 스키마 동기화 및 리스너 (설정 시)
    if settings.enable_schema_sync:
        logging.getLogger("uvicorn.error").info(
            "LIFESPAN: enable_schema_sync=%s", settings.enable_schema_sync
        )
        try:
            # 초기 동기화 (1회)
            await run_once()
            # 리스너 시작
            await start_listener()
        except Exception as e:
            logger.error("LIFESPAN: Schema sync/listener setup failed: %s", e)
            
    # 3. 알림 리스너 시작
    try:
        alert_listener = AlertListener()
        await alert_listener.start()
        logger.info("LIFESPAN: Alert listener started")
    except Exception as e:
        logger.error("LIFESPAN: Alert listener setup failed: %s", e)

    yield
    
    # 4. 종료 처리
    # Checkpointer 연결 풀 종료
    await close_checkpointer()

    # 스키마 리스너 종료
    if settings.enable_schema_sync:
        try:
            await stop_listener()
        except Exception as e:
             logger.error("LIFESPAN: Schema listener stop failed: %s", e)
    
    # 알림 리스너 종료
    if alert_listener:
        await alert_listener.stop()
        logger.info("LIFESPAN: Alert listener stopped")

