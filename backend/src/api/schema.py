"""스키마 테이블/컬럼 목록 조회 API."""
import logging
from fastapi import APIRouter
from config.settings import settings
from src.db.db_manager import db_manager

router = APIRouter(tags=["schema"])
logger = logging.getLogger("API_SCHEMA")


@router.get("/schema/tables")
async def list_schema_tables():
    """사용 가능한 테이블 및 컬럼 목록 조회 (설정된 제외 네임스페이스 반영)."""
    excluded_schemas = tuple(
        s.strip() for s in settings.schema_exclude_namespaces.split(",") if s.strip()
    )
    excluded_str = ", ".join(f"'{s}'" for s in excluded_schemas)

    tables_sql = f"""
    SELECT
      n.nspname AS schema,
      c.relname AS table_name
    FROM pg_class c
    JOIN pg_namespace n ON n.oid = c.relnamespace
    WHERE c.relkind IN ('r','p','v')
      AND n.nspname NOT IN ({excluded_str})
    ORDER BY n.nspname, c.relname;
    """

    columns_sql = f"""
    SELECT
      n.nspname AS schema,
      c.relname AS table_name,
      a.attname AS column_name
    FROM pg_attribute a
    JOIN pg_class c ON a.attrelid = c.oid
    JOIN pg_namespace n ON c.relnamespace = n.oid
    WHERE a.attnum > 0
      AND NOT a.attisdropped
      AND c.relkind IN ('r','p','v')
      AND n.nspname NOT IN ({excluded_str})
    ORDER BY n.nspname, c.relname, a.attnum;
    """

    try:
        pool = await db_manager.get_pool()
        async with pool.acquire() as conn:
            db_manager._log_pool_usage(pool, "acquire")
            tables = await conn.fetch(tables_sql)
            columns = await conn.fetch(columns_sql)
    except Exception as e:
        logger.error("Schema list error: %s", e)
        return []

    col_map: dict[str, list[str]] = {}
    for col in columns:
        schema = col.get("schema", "")
        table = col.get("table_name", "")
        full = f"{schema}.{table}" if schema and table else table
        col_map.setdefault(full, []).append(col.get("column_name", ""))

    result = []
    for t in tables:
        schema = t.get("schema", "")
        table = t.get("table_name", "")
        full = f"{schema}.{table}" if schema and table else table
        result.append({
            "table": full,
            "columns": col_map.get(full, []),
        })

    return result
