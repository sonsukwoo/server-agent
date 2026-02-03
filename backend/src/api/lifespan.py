"""
FastAPI Lifespan (Startup/Shutdown) 로직
"""
import logging
from contextlib import asynccontextmanager
from fastapi import FastAPI
from config.settings import settings
from src.schema.listener import SchemaListener
from src.db.chat_store import chat_store
from src.schema.sync import sync_schema_embeddings_mcp

logger = logging.getLogger("LIFESPAN")

@asynccontextmanager
async def lifespan(app: FastAPI):
    """앱 시작/종료 수명주기 핸들러"""
    schema_listener = None
    
    # 1. 채팅 기록 스키마 초기화 (항상 수행)
    try:
        await chat_store.ensure_chat_schema()
    except Exception as e:
        logger.error("CHAT_STORE: ensure schema failed: %s", e)

    # 2. 스키마 동기화 및 리스너 (설정에 따라 수행)
    if settings.enable_schema_sync:
        logging.getLogger("uvicorn.error").info(
            "LIFESPAN: enable_schema_sync=%s", settings.enable_schema_sync
        )
        try:
            # 초기 동기화 (1회)
            await sync_schema_embeddings_mcp()

            # 리스너 시작 (Background Task)
            schema_listener = SchemaListener(callback=sync_schema_embeddings_mcp)
            await schema_listener.start()
            logger.info("LIFESPAN: Schema listener started")

        except Exception as e:
            logger.error("LIFESPAN: Schema sync/listener setup failed: %s", e)
            
    yield
    
    # 3. 종료 처리
    if schema_listener:
        await schema_listener.stop()
        logger.info("LIFESPAN: Schema listener stopped")
