"""DB 스키마 조회 및 Qdrant 임베딩 동기화."""
import json
import logging
from config.settings import settings
from src.agents.mcp_clients.connector import postgres_client, qdrant_embeddings_client

logger = logging.getLogger("uvicorn.error")

async def sync_schema_embeddings_mcp() -> None:
    """DB 스키마를 Qdrant 임베딩 서버로 업서트 (해시 변경 시)."""
    logger.info("스키마 임베딩 동기화 시작")

    excluded_schemas = tuple(
        s.strip() for s in settings.schema_exclude_namespaces.split(",") if s.strip()
    )
    excluded_str = ", ".join(f"'{s}'" for s in excluded_schemas)
    
    tables_sql = f"""
    SELECT
      n.nspname AS schema,
      c.relname AS table_name,
      obj_description(c.oid, 'pg_class') AS description
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
      a.attname AS column_name,
      format_type(a.atttypid, a.atttypmod) AS data_type,
      col_description(a.attrelid, a.attnum) AS description
    FROM pg_attribute a
    JOIN pg_class c ON a.attrelid = c.oid
    JOIN pg_namespace n ON c.relnamespace = n.oid
    WHERE a.attnum > 0
      AND NOT a.attisdropped
      AND c.relkind IN ('r','p','v')
      AND n.nspname NOT IN ({excluded_str})
    ORDER BY n.nspname, c.relname, a.attnum;
    """

    async with postgres_client() as client:
        tables_raw = await client.call_tool("execute_sql", {"query": tables_sql})
        columns_raw = await client.call_tool("execute_sql", {"query": columns_sql})

    tables = json.loads(tables_raw) if tables_raw else []
    columns = json.loads(columns_raw) if columns_raw else []

    column_map: dict[tuple[str, str], list[dict]] = {}
    for col in columns:
        key = (col.get("schema", ""), col.get("table_name", ""))
        column_map.setdefault(key, []).append({
            "name": col.get("column_name", ""),
            "type": col.get("data_type", ""),
            "description": col.get("description") or "",
            "role": "dimension",
            "category": "general",
            "visible_to_llm": True,
        })

    docs = []
    for t in tables:
        schema = t.get("schema", "")
        table_name = t.get("table_name", "")
        columns_list = column_map.get((schema, table_name), [])
        docs.append({
            "doc_type": "table",
            "schema": schema,
            "table_name": table_name,
            "description": t.get("description") or "",
            "primary_time_col": _infer_primary_time(columns_list),
            "join_keys": _infer_join_keys(columns_list),
            "columns": columns_list,
            "source": "db_schema",
        })

    if not docs:
        logger.info("스키마 테이블 없음: 임베딩 스킵")
        return

    # 컬렉션 생성 및 해시 비교
    collection_created = False
    async with qdrant_embeddings_client() as qclient:
        ensure_msg = await qclient.call_tool("ensure_collection", {"vector_size": 1536})
        if isinstance(ensure_msg, str) and ("생성" in ensure_msg or "created" in ensure_msg.lower()):
            collection_created = True

        from .hash_utils import calculate_schema_hash, read_hash_file, write_hash_file
        
        schema_hash = calculate_schema_hash(docs)
        stored_hash = read_hash_file()
        
        if stored_hash == schema_hash and not collection_created:
            logger.info("스키마 변경 없음: 임베딩 스킵")
            return

        await qclient.call_tool("upsert_schema", {"docs": docs})

    write_hash_file(schema_hash)
    logger.info("스키마 임베딩 완료: 테이블 %s개", len(docs))


def _infer_primary_time(columns: list[dict]) -> str | None:
    for col in columns:
        if col.get("name") == "ts":
            return "ts"
    for col in columns:
        if col.get("name") in {"time", "timestamp", "created_at"}:
            return col.get("name")
    return None


def _infer_join_keys(columns: list[dict]) -> list[str]:
    keys: list[str] = []
    for col in columns:
        name = (col.get("name") or "").lower()
        if name == "ts":
            keys.append("ts")
        if name.endswith("_id") or name in {"host", "host_id", "container_id", "mount", "interface"}:
            keys.append(col.get("name") or "")
    seen: set[str] = set()
    deduped = []
    for k in keys:
        if k and k not in seen:
            seen.add(k)
            deduped.append(k)
    return deduped
