"""
연결된 DB 스키마를 조회하고 Qdrant 임베딩 서버와 동기화하는 로직

채팅 기록 테이블, 채팅 세션 테이블, 모니터링 테이블은 조회 제외
"""
import json

import hashlib
import logging
from pathlib import Path
from config.settings import settings
from src.agents.mcp_clients.connector import postgres_client, qdrant_embeddings_client

logger = logging.getLogger("uvicorn.error")

async def sync_schema_embeddings_mcp() -> None:
    """DB 스키마를 읽어 Qdrant MCP 임베딩 서버로 업서트"""
    logger.info("스키마 임베딩 동기화 시작")

    excluded_schemas = tuple(
        s.strip() for s in settings.schema_exclude_namespaces.split(",") if s.strip()
    )
    # SQL 인젝션 방지를 위해 파라미터 바인딩은 어렵지만, settings 값은 신뢰한다고 가정하거나
    # 안전하게 포맷팅. 여기서는 간단히 리스트 문자열로 변환.
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
      AND n.nspname NOT IN ('monitor', 'chat') -- [EXCLUDE] Alert system & Chat history from RAG
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
      AND n.nspname NOT IN ('monitor', 'chat') -- [EXCLUDE]
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

    # 컬렉션 존재 여부 확인/생성 (없으면 생성되므로 업서트 강제)
    collection_created = False
    async with qdrant_embeddings_client() as qclient:
        ensure_msg = await qclient.call_tool("ensure_collection", {"vector_size": 1536})
        if isinstance(ensure_msg, str) and ("생성" in ensure_msg or "created" in ensure_msg.lower()):
            collection_created = True

        # schema hash 비교 (변경 없고 컬렉션도 이미 있으면 스킵)
        schema_hash = _schema_hash(docs)
        stored_hash = _read_hash_file()
        if stored_hash == schema_hash and not collection_created:
            logger.info("스키마 변경 없음: 임베딩 스킵")
            return

        await qclient.call_tool("upsert_schema", {"docs": docs})

    _write_hash_file(schema_hash)
    logger.info("스키마 임베딩 완료: 테이블 %s개", len(docs))


def _schema_hash(docs: list[dict]) -> str:
    payload = []
    for doc in docs:
        payload.append(
            {
                "doc_type": doc.get("doc_type"),
                "schema": doc.get("schema"),
                "table_name": doc.get("table_name"),
                "description": doc.get("description"),
                "columns": [
                    {
                        "name": c.get("name"),
                        "type": c.get("type"),
                        "description": c.get("description"),
                    }
                    for c in doc.get("columns", [])
                ],
            }
        )
    canonical = json.dumps(payload, ensure_ascii=True, sort_keys=True)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


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


def _read_hash_file() -> str | None:
    path = Path(settings.schema_hash_file)
    try:
        if path.exists():
            return path.read_text(encoding="utf-8").strip() or None
    except Exception as e:
        logger.warning("SCHEMA_EMBED: hash read failed: %s", e)
    return None


def _write_hash_file(schema_hash: str) -> None:
    path = Path(settings.schema_hash_file)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(schema_hash, encoding="utf-8")
    except Exception as e:
        logger.warning("SCHEMA_EMBED: hash write failed: %s", e)
