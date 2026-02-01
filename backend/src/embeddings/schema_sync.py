"""DB 스키마 임베딩 동기화 (Qdrant)"""
import hashlib
import json
import logging
import uuid
from pathlib import Path
from urllib import request
from urllib.error import HTTPError

from sqlalchemy import text, create_engine
from langchain_openai import OpenAIEmbeddings

from config.settings import settings


logger = logging.getLogger("SCHEMA_EMBED")


SYSTEM_SCHEMAS = {"pg_catalog", "information_schema"}


def _get_engine():
    db_url = (
        f"postgresql://{settings.db_user}:{settings.db_password}"
        f"@{settings.db_host}:{settings.db_port}/{settings.db_name}"
    )
    return create_engine(db_url, pool_pre_ping=True)


def _qdrant_headers():
    headers = {"Content-Type": "application/json"}
    if settings.qdrant_api_key:
        headers["api-key"] = settings.qdrant_api_key
    return headers


def _http_json(method: str, url: str, payload: dict | None = None, timeout: int = 30):
    data = None
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
    req = request.Request(url, data=data, headers=_qdrant_headers(), method=method)
    try:
        with request.urlopen(req, timeout=timeout) as resp:
            body = resp.read().decode("utf-8")
        return json.loads(body) if body else {}
    except HTTPError as e:
        if e.code == 404:
            return {"_http_status": 404}
        raise


def _read_hash_file():
    path = Path(settings.schema_hash_file)
    try:
        if path.exists():
            return path.read_text(encoding="utf-8").strip() or None
    except Exception as e:
        logger.warning(f"스키마 해시 파일 읽기 실패: {e}")
    return None


def _set_stored_hash(schema_hash: str):
    path = Path(settings.schema_hash_file)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(schema_hash, encoding="utf-8")
    except Exception as e:
        logger.warning(f"스키마 해시 파일 쓰기 실패: {e}")


def _get_target_schemas(conn) -> list[str]:
    if settings.schema_namespaces:
        return [s.strip() for s in settings.schema_namespaces.split(",") if s.strip()]
    rows = conn.execute(
        text(
            """
            SELECT nspname
            FROM pg_namespace
            WHERE nspname NOT IN :system_schemas
            ORDER BY 1;
            """
        ),
        {"system_schemas": tuple(SYSTEM_SCHEMAS)},
    ).fetchall()
    return [r[0] for r in rows]


def _fetch_schema_rows(conn, schemas: list[str]):
    tables = conn.execute(
        text(
            """
            SELECT n.nspname AS schema_name,
                   c.relname AS table_name,
                   c.relkind AS relkind,
                   d.description AS table_comment
            FROM pg_class c
            JOIN pg_namespace n ON n.oid = c.relnamespace
            LEFT JOIN pg_description d ON d.objoid = c.oid AND d.objsubid = 0
            WHERE c.relkind IN ('r','v')
              AND n.nspname = ANY(:schemas)
            ORDER BY 1,2;
            """
        ),
        {"schemas": schemas},
    ).fetchall()

    columns = conn.execute(
        text(
            """
            SELECT n.nspname AS schema_name,
                   c.relname AS table_name,
                   c.relkind AS relkind,
                   a.attname AS column_name,
                   pg_catalog.format_type(a.atttypid, a.atttypmod) AS data_type,
                   d.description AS column_comment
            FROM pg_class c
            JOIN pg_namespace n ON n.oid = c.relnamespace
            JOIN pg_attribute a ON a.attrelid = c.oid AND a.attnum > 0 AND NOT a.attisdropped
            LEFT JOIN pg_description d ON d.objoid = c.oid AND d.objsubid = a.attnum
            WHERE c.relkind IN ('r','v')
              AND n.nspname = ANY(:schemas)
            ORDER BY 1,2,4;
            """
        ),
        {"schemas": schemas},
    ).fetchall()

    return tables, columns


def _infer_role(column_name: str) -> str:
    name = column_name.lower()
    if name in {"ts", "time", "timestamp", "created_at"}:
        return "time"
    if name.endswith("_id") or name in {"id", "name"}:
        return "dimension"
    if name.endswith("_pct") or name.endswith("_percent") or name.endswith("_rate_bps"):
        return "metric"
    if any(token in name for token in ("used", "total", "free", "count", "percent", "rate")):
        return "metric"
    return "dimension"


def _infer_category(column_name: str) -> str:
    name = column_name.lower()
    if "cpu" in name:
        return "cpu"
    if "mem" in name or "ram" in name or "swap" in name:
        return "memory"
    if "disk" in name or "mount" in name:
        return "disk"
    if "rx" in name or "tx" in name or "network" in name or "interface" in name:
        return "network"
    if "docker" in name or "container" in name:
        return "docker"
    if "tmux" in name or "session" in name:
        return "runtime"
    return "general"


def _infer_primary_time(columns: list[dict]) -> str | None:
    for col in columns:
        if col["name"] == "ts":
            return "ts"
    for col in columns:
        if col["role"] == "time":
            return col["name"]
    return None


def _infer_join_keys(columns: list[dict]) -> list[str]:
    keys = []
    for col in columns:
        name = col["name"].lower()
        if name == "ts":
            keys.append("ts")
        if name.endswith("_id") or name in {"host", "host_id", "container_id", "mount", "interface"}:
            keys.append(col["name"])
    seen = set()
    return [k for k in keys if not (k in seen or seen.add(k))]


def _build_schema_docs(tables, columns):
    docs = []
    col_map: dict[tuple, list[dict]] = {}
    for row in columns:
        key = (row.schema_name, row.table_name, row.relkind)
        col_map.setdefault(key, []).append(
            {
                "name": row.column_name,
                "type": row.data_type,
                "description": row.column_comment or "",
                "role": _infer_role(row.column_name),
                "category": _infer_category(row.column_name),
                "visible_to_llm": True,
            }
        )

    for row in tables:
        key = (row.schema_name, row.table_name, row.relkind)
        columns_list = col_map.get(key, [])
        docs.append(
            {
                "doc_type": "view" if row.relkind == "v" else "table",
                "schema": row.schema_name,
                "table_name": row.table_name,
                "description": row.table_comment or "",
                "primary_time_col": _infer_primary_time(columns_list),
                "join_keys": _infer_join_keys(columns_list),
                "columns": columns_list,
                "source": "db_schema",
            }
        )
    return docs


def _schema_hash(docs: list[dict]) -> str:
    payload = []
    for doc in docs:
        payload.append(
            {
                "doc_type": doc["doc_type"],
                "schema": doc["schema"],
                "table_name": doc["table_name"],
                "description": doc["description"],
                "columns": [
                    {
                        "name": c["name"],
                        "type": c["type"],
                        "description": c["description"],
                    }
                    for c in doc["columns"]
                ],
            }
        )
    canonical = json.dumps(payload, ensure_ascii=True, sort_keys=True)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _embed_texts(texts: list[str]) -> list[list[float]]:
    embeddings = OpenAIEmbeddings(model=settings.embedding_model, api_key=settings.openai_api_key)
    return embeddings.embed_documents(texts)


def _ensure_collection(vector_size: int):
    url = f"{settings.qdrant_url}/collections/{settings.qdrant_collection}"
    info = _http_json("GET", url, timeout=10)
    existing_size = None
    try:
        existing_size = info["result"]["config"]["params"]["vectors"]["size"]
    except Exception:
        existing_size = None

    if existing_size == vector_size:
        return

    if info.get("result"):
        _http_json("DELETE", url, timeout=10)

    payload = {"vectors": {"size": vector_size, "distance": "Cosine"}}
    _http_json("PUT", url, payload, timeout=10)


def _delete_existing_points():
    url = f"{settings.qdrant_url}/collections/{settings.qdrant_collection}/points/delete?wait=true"
    payload = {"filter": {"must": [{"key": "source", "match": {"value": "db_schema"}}]}}
    _http_json("POST", url, payload, timeout=30)


def _upsert_points(vectors: list[list[float]], docs: list[dict]):
    url = f"{settings.qdrant_url}/collections/{settings.qdrant_collection}/points?wait=true"
    points = []
    for doc, vector in zip(docs, vectors):
        point_id = str(
            uuid.uuid5(
                uuid.NAMESPACE_URL,
                f"{doc['schema']}.{doc['table_name']}:{doc['doc_type']}",
            )
        )
        points.append({"id": point_id, "vector": vector, "payload": doc})
    _http_json("PUT", url, {"points": points}, timeout=60)


def sync_schema_embeddings(force: bool = False) -> bool:
    if not settings.qdrant_api_key:
        logger.warning("QDRANT_API_KEY가 설정되지 않아 스키마 임베딩 업로드를 건너뜁니다.")
        return False
    try:
        engine = _get_engine()
        with engine.begin() as conn:
            schemas = _get_target_schemas(conn)
            if not schemas:
                logger.warning("대상 스키마가 없어 임베딩 업로드를 건너뜁니다.")
                return False

            tables, columns = _fetch_schema_rows(conn, schemas)
            docs = _build_schema_docs(tables, columns)
            if not docs:
                logger.warning("스키마 문서가 비어있어 임베딩 업로드를 건너뜁니다.")
                return False

            schema_hash = _schema_hash(docs)
            stored_hash = _read_hash_file()

            if not force and stored_hash == schema_hash:
                logger.info("스키마 변경 없음: 임베딩 업로드 스킵")
                return False

            texts = []
            for doc in docs:
                columns_text = "\n".join(
                    f"- {c['name']} ({c['type']}): {c['description']}".strip()
                    for c in doc["columns"]
                )
                texts.append(
                    f"{doc['schema']}.{doc['table_name']} ({doc['doc_type']})\n"
                    f"{doc['description']}\n"
                    f"Columns:\n{columns_text}"
                )

            vectors = _embed_texts(texts)
            _ensure_collection(len(vectors[0]))
            _delete_existing_points()
            _upsert_points(vectors, docs)

        _set_stored_hash(schema_hash)
        logger.info("스키마 임베딩 업로드 완료")
        return True
    except Exception as e:
        logger.error(f"스키마 임베딩 업로드 실패: {e}")
        return False


if __name__ == "__main__":
    sync_schema_embeddings(force=True)
