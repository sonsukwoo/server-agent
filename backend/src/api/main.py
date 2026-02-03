from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import StreamingResponse
from contextlib import asynccontextmanager
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from src.agents.text_to_sql import run_text_to_sql, app as sql_app
from src.agents.mcp_clients.connector import postgres_client, qdrant_embeddings_client
from src.agents.middleware.input_guard import InputGuard
from config.settings import settings
import hashlib
from pathlib import Path
import logging
import json
from src.schema_listener import SchemaListener
from src.db.chat_store import chat_store
from src.api.chat import router as chat_router

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("TEXT_TO_SQL")

class QueryRequest(BaseModel):
    agent: str  # "sql" 또는 "ubuntu"
    question: str

class QueryResponse(BaseModel):
    ok: bool
    agent: str
    data: dict | None = None
    error: str | None = None

@asynccontextmanager
async def lifespan(app: FastAPI):
    """앱 시작/종료 수명주기 핸들러"""
    schema_listener = None
    try:
        # 채팅 기록 스키마 초기화 (항상)
        await chat_store.ensure_chat_schema()
    except Exception as e:
        logger.error("CHAT_STORE: ensure schema failed: %s", e)

    if settings.enable_schema_sync:
        try:
            # 1. 초기 동기화 (1회)
            await sync_schema_embeddings_mcp()

            # 2. 리스너 시작 (Background)
            schema_listener = SchemaListener(callback=sync_schema_embeddings_mcp)
            await schema_listener.start()

        except Exception as e:
            logger.error("SCHEMA_EMBED: MCP sync/listener setup failed: %s", e)
            
    yield
    
    # 종료 처리
    if schema_listener:
        await schema_listener.stop()


app = FastAPI(title="Server Agent API", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# Router 등록
app.include_router(chat_router)

@app.get("/")
async def root():
    return {"message": "Server Agent API is running"}


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


@app.post("/query")
async def query(body: QueryRequest):
    """자연어 질문을 받아서 처리 (스트리밍 지원)"""
    agent_type = body.agent.lower().strip()
    question = body.question.strip()

    # 1. 입력 검증
    if not question:
        raise HTTPException(status_code=400, detail="질문이 비어있습니다")
    
    is_valid, error = InputGuard.validate(question)
    if not is_valid:
        raise HTTPException(status_code=400, detail=error)

    # 노드 이름과 상태 메시지 매핑
    node_messages = {
        "parse_request": "사용자 질문 분석 중...",
        "validate_request": "질문 유효성 검증 중...",
        "retrieve_tables": "관련 테이블 검색 중...",
        "select_tables": "조회에 필요한 테이블 선택 중...",
        "generate_sql": "SQL 쿼리 생성 중...",
        "guard_sql": "SQL 안전성 검사 중...",
        "execute_sql": "데이터베이스 조회 중...",
        "normalize_result": "조회 결과 정리 중...",
        "validate_llm": "결과 정확성 검증 중...",
        "expand_tables": "테이블 확장 검색 중...",
        "generate_report": "최종 보고서 작성 중...",
    }

    async def event_generator():
        if agent_type == "sql":
            initial_state = {
                "user_question": question,
                "sql_retry_count": 0,
                "table_expand_count": 0,
                "validation_retry_count": 0,
                "total_loops": 0,
                "verdict": "OK",
                "result_status": "unknown",
                "failed_queries": [],
                "table_expand_attempted": False,
                "table_expand_failed": False,
                "table_expand_reason": None,
            }
            
            last_reason = ""
            current_retry = 0
            try:
                # LangGraph astream 호출
                async for event in sql_app.astream(initial_state):
                    for node_name, output in event.items():
                        # 상태 업데이트 추적
                        if "validation_reason" in output:
                            last_reason = output["validation_reason"]
                        
                        # 재시도 횟수 업데이트
                        node_retry = output.get("sql_retry_count") or output.get("validation_retry_count")
                        if node_retry is not None:
                            current_retry = node_retry
                        
                        # 특정 노드가 시작되거나 완료될 때 상태 메시지 전송
                        status_msg = node_messages.get(node_name)
                        if status_msg:
                            # 특수 케이스: generate_sql에서 재시도 중인 경우 상세 사유 포함
                            if node_name == "generate_sql" and current_retry > 0:
                                if last_reason:
                                    # 사유를 짧게 요약하거나 그대로 표시 (사용자 요청: "로그에 나오듯 나오게")
                                    status_msg = f"피드백 반영하여 SQL 재작성 중 (사유: {last_reason}) [재시도 {current_retry}]"
                                else:
                                    status_msg = f"오류 복구 및 SQL 재작성 중... [재시도 {current_retry}]"
                            
                            yield f"data: {json.dumps({'type': 'status', 'message': status_msg, 'node': node_name}, ensure_ascii=False)}\n\n"
                        
                        # 툴 사용 로그가 있으면 이벤트 전송
                        tool_usage = output.get("last_tool_usage")
                        if tool_usage:
                            tool_msg = f"🛠️ [툴 사용] {tool_usage}"
                            yield f"data: {json.dumps({'type': 'status', 'message': tool_msg, 'node': node_name}, ensure_ascii=False)}\n\n"
                        
                        # 마지막 결과인 경우 전체 데이터 전송
                        if node_name == "generate_report":
                            final_data = {
                                "ok": True,
                                "agent": "sql",
                                "data": {
                                    "report": output.get("report", ""),
                                    "suggested_actions": output.get("suggested_actions", []),
                                    "raw": output
                                }
                            }
                            yield f"data: {json.dumps({'type': 'result', 'payload': final_data}, ensure_ascii=False)}\n\n"
            except Exception as e:
                logger.error("STREAM_ERROR: %s", e)
                yield f"data: {json.dumps({'type': 'error', 'message': str(e)}, ensure_ascii=False)}\n\n"
        elif agent_type == "ubuntu":
            yield f"data: {json.dumps({'type': 'error', 'message': 'Ubuntu 에이전트는 아직 구현되지 않았습니다.'}, ensure_ascii=False)}\n\n"
        else:
            yield f"data: {json.dumps({'type': 'error', 'message': f'지원하지 않는 에이전트 타입입니다: {agent_type}'}, ensure_ascii=False)}\n\n"

    return StreamingResponse(event_generator(), media_type="text/event-stream")

@app.get("/resource-summary")
async def get_resource_summary():
    """ops_metrics.v_resource_summary 뷰에서 최근 리소스 사용량 요약을 가져옴"""
    sql = "SELECT * FROM ops_metrics.v_resource_summary ORDER BY \"배치 ID\" DESC LIMIT 1"
    try:
        async with postgres_client() as client:
            result_raw = await client.call_tool("execute_sql", {"query": sql})
            if not result_raw:
                return {}
            
            try:
                result = json.loads(result_raw)
            except json.JSONDecodeError as je:
                return {}

            if result and isinstance(result, list):
                return result[0]
            return {}
    except Exception as e:
        return {}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
