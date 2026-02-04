"""
DB 이벤트 트리거 자동 설정 모듈
서버 시작 시 트리거 존재 여부를 확인하고, 없으면 최적화된 트리거를 생성합니다.
"""
import logging
import asyncpg
from config.settings import settings

logger = logging.getLogger("SCHEMA_SETUP")

async def ensure_event_trigger() -> bool:
    """
    이벤트 트리거가 존재하는지 확인하고, 없으면 생성합니다.
    
    Returns:
        bool: 사용 가능 여부 (성공적으로 확인/생성되었으면 True)
    """
    dsn = f"postgresql://{settings.db_user}:{settings.db_password}@{settings.db_host}:{settings.db_port}/{settings.db_name}"
    conn = None
    try:
        conn = await asyncpg.connect(dsn)
        
        # 1. 트리거 및 함수 존재 여부 확인
        check_trigger_sql = "SELECT count(*) FROM pg_event_trigger WHERE evtname = $1"
        trigger_count = await conn.fetchval(check_trigger_sql, settings.schema_trigger_name)

        check_func_sql = "SELECT count(*) FROM pg_proc WHERE proname = $1"
        func_count = await conn.fetchval(check_func_sql, settings.schema_trigger_name)
        
        if trigger_count > 0 and func_count > 0:
            logger.info("Schema trigger and function exist: %s", settings.schema_trigger_name)
            return True
            
        # 2. 하나라도 없으면 생성 (최적화된 버전)
        if trigger_count == 0:
             logger.info("Schema trigger missing. Creating...")
        elif func_count == 0:
             logger.info("Schema trigger function missing. Recreating...")
        
        async with conn.transaction():
            # 2-1. 알림 함수 생성 (없을 때만 생성 또는 덮어쓰기)
            # CREATE OR REPLACE 라서 안전하지만 불필요한 실행 방지
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
            
            # 2-2. 이벤트 트리거 생성 (없을 때만 실행 - 중요)
            if trigger_count == 0:
                logger.info("Creating schema event trigger...")
                # ddl_command_end 이벤트 중 'CREATE TABLE', 'ALTER TABLE', 'DROP TABLE' 태그에만 반응
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
        # 권한 부족 등의 이유로 실패할 수 있음. 
        # 실패하더라도 서버 시작 자체를 막지는 않도록 False 반환 후 로그만 남김.
        return False
        
    finally:
        if conn:
            await conn.close()
