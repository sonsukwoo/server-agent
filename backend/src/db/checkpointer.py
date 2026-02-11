"""LangGraph AsyncPostgresSaver 초기화 및 관리."""

import logging
from psycopg_pool import AsyncConnectionPool
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver

from config.settings import settings

logger = logging.getLogger("CHECKPOINTER")

# 모듈 레벨 싱글톤
_checkpointer: AsyncPostgresSaver | None = None
_pool: AsyncConnectionPool | None = None


def _build_dsn() -> str:
    """PostgreSQL 연결 문자열 생성."""
    return (
        f"postgresql://{settings.db_user}:{settings.db_password}"
        f"@{settings.db_host}:{settings.db_port}/{settings.db_name}"
    )


async def get_checkpointer() -> AsyncPostgresSaver:
    """AsyncPostgresSaver 싱글톤 반환 (지연 초기화)."""
    global _checkpointer, _pool

    if _checkpointer is not None:
        return _checkpointer

    import psycopg
    
    dsn = _build_dsn()
    
    # setup()은 CREATE INDEX CONCURRENTLY 등을 사용할 수 있으므로
    # autocommit 모드의 별도 비동기 연결로 수행합니다.
    async with await psycopg.AsyncConnection.connect(dsn, autocommit=True) as conn:
        checkpointer = AsyncPostgresSaver(conn)
        await checkpointer.setup()

    # AsyncPostgresSaver용 풀 설정 (자동 커밋 필수)
    # kwargs={"autocommit": True} -> psycopg 3.x 풀 옵션
    _pool = AsyncConnectionPool(
        conninfo=dsn, 
        min_size=1, 
        max_size=3, 
        open=False,
        kwargs={"autocommit": True}
    )
    await _pool.open()

    _checkpointer = AsyncPostgresSaver(_pool)
    logger.info("AsyncPostgresSaver 초기화 완료 (DSN: %s)", settings.db_host)
    return _checkpointer



async def close_checkpointer() -> None:
    """서버 종료 시 연결 풀 정리."""
    global _checkpointer, _pool

    if _pool is not None:
        await _pool.close()
        logger.info("Checkpointer 연결 풀 종료 완료")

    _checkpointer = None
    _pool = None
