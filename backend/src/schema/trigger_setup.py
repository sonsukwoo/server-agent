"""DB 이벤트 트리거 자동 설정 (서버 시작 시)."""
import logging
import asyncpg
from config.settings import settings

logger = logging.getLogger("SCHEMA_SETUP")

async def ensure_event_trigger() -> bool:
    """이벤트 트리거 존재 확인 및 생성 (없을 경우)."""
    dsn = f"postgresql://{settings.db_user}:{settings.db_password}@{settings.db_host}:{settings.db_port}/{settings.db_name}"
    conn = None
    try:
        conn = await asyncpg.connect(dsn)
        
        # 1. 트리거 및 함수 존재 확인
        check_trigger_sql = "SELECT count(*) FROM pg_event_trigger WHERE evtname = $1"
        trigger_count = await conn.fetchval(check_trigger_sql, settings.schema_trigger_name)

        check_func_sql = "SELECT count(*) FROM pg_proc WHERE proname = $1"
        func_count = await conn.fetchval(check_func_sql, settings.schema_trigger_name)
        
        if trigger_count > 0 and func_count > 0:
            logger.info("Schema trigger and function exist: %s", settings.schema_trigger_name)
            return True
            
        # 2. 미존재 시 생성 트랜잭션
        if trigger_count == 0:
             logger.info("Schema trigger missing. Creating...")
        elif func_count == 0:
             logger.info("Schema trigger function missing. Recreating...")
        
        async with conn.transaction():
            # 2-1. 알림 함수 생성
            if func_count == 0:
                logger.info("Creating schema trigger function...")
                await conn.execute(f"""
                CREATE OR REPLACE FUNCTION {settings.schema_trigger_name}()
                RETURNS event_trigger AS $$
                BEGIN
                    PERFORM pg_notify('{settings.schema_notify_channel}', 'schema_changed');
                END;
                $$ LANGUAGE plpgsql;
                """)
            
            # 2-2. 이벤트 트리거 생성 (ddl_command_end)
            if trigger_count == 0:
                logger.info("Creating schema event trigger...")
                await conn.execute(f"""
                CREATE EVENT TRIGGER {settings.schema_trigger_name}
                ON ddl_command_end
                WHEN TAG IN ('CREATE TABLE', 'ALTER TABLE', 'DROP TABLE')
                EXECUTE FUNCTION {settings.schema_trigger_name}();
                """)
            
        logger.info("Schema trigger setup completed successfully: %s", settings.schema_trigger_name)
        return True

    except Exception as e:
        logger.warning("Failed to ensure schema trigger: %s. (Listener might not work if trigger is missing)", e)
        return False
        
    finally:
        if conn:
            await conn.close()
