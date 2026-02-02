"""Qdrant MCP 서버 - 검색 + 임베딩 통합"""
import os
import json
import uuid
from urllib import request
from typing import Any

from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import Tool, TextContent
from qdrant_client import QdrantClient
from qdrant_client.models import VectorParams, Distance, PointStruct
from langchain_openai import OpenAIEmbeddings

import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("QDRANT_MCP")

app = Server("qdrant-tools")

# 설정 (환경변수)
QDRANT_URL = os.getenv("QDRANT_URL", "http://localhost:6333")
QDRANT_API_KEY = os.getenv("QDRANT_API_KEY", "")
QDRANT_COLLECTION = os.getenv("QDRANT_COLLECTION", "table_index")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
EMBEDDING_MODEL = os.getenv("EMBEDDING_MODEL", "text-embedding-3-small")

embeddings = OpenAIEmbeddings(model=EMBEDDING_MODEL, api_key=OPENAI_API_KEY)

_client = None


def get_client() -> QdrantClient:
    global _client
    if _client is None:
        _client = QdrantClient(
            url=QDRANT_URL,
            api_key=QDRANT_API_KEY if QDRANT_API_KEY else None,
        )
    return _client


def _http_json(method: str, url: str, payload: dict | None = None, timeout: int = 30):
    data = None
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
    headers = {"Content-Type": "application/json"}
    if QDRANT_API_KEY:
        headers["api-key"] = QDRANT_API_KEY
    req = request.Request(url, data=data, headers=headers, method=method)
    try:
        with request.urlopen(req, timeout=timeout) as resp:
            body = resp.read().decode("utf-8")
        return json.loads(body) if body else {}
    except Exception as e:
        return {"error": str(e)}


def _ensure_collection(vector_size: int = 1536):
    client = get_client()
    collections = client.get_collections()
    names = [c.name for c in collections.collections]
    if QDRANT_COLLECTION not in names:
        client.create_collection(
            collection_name=QDRANT_COLLECTION,
            vectors_config=VectorParams(size=vector_size, distance=Distance.COSINE),
        )
        logger.info("ensure_collection: created collection=%s size=%s", QDRANT_COLLECTION, vector_size)
        return f"컬렉션 '{QDRANT_COLLECTION}' 생성 완료"
    logger.info("ensure_collection: exists collection=%s", QDRANT_COLLECTION)
    return f"컬렉션 '{QDRANT_COLLECTION}' 이미 존재"


def _search_qdrant(query: str, top_k: int) -> list[dict]:
    client = get_client()
    query_vector = embeddings.embed_query(query)

    if hasattr(client, "search"):
        results = client.search(
            collection_name=QDRANT_COLLECTION,
            query_vector=query_vector,
            limit=top_k,
        )
    else:
        url = f"{QDRANT_URL}/collections/{QDRANT_COLLECTION}/points/search"
        payload = json.dumps({
            "vector": query_vector,
            "limit": top_k,
            "with_payload": True,
        }).encode("utf-8")
        headers = {"Content-Type": "application/json"}
        if QDRANT_API_KEY:
            headers["api-key"] = QDRANT_API_KEY
        req = request.Request(url, data=payload, headers=headers, method="POST")
        with request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        results = data.get("result", [])

    candidates = []
    for hit in results:
        payload = getattr(hit, "payload", None) or (hit.get("payload", {}) if isinstance(hit, dict) else {}) or {}
        score = getattr(hit, "score", None)
        if score is None and isinstance(hit, dict):
            score = hit.get("score")

        schema = payload.get("schema", "")
        table_name = payload.get("table_name", "")
        full_name = f"{schema}.{table_name}" if schema and table_name else table_name

        raw_columns = payload.get("columns", [])
        columns = [
            {
                "name": col.get("name", ""),
                "type": col.get("type", ""),
                "description": col.get("description", ""),
                "role": col.get("role", ""),
                "category": col.get("category", ""),
            }
            for col in raw_columns
            if col.get("visible_to_llm") is True
        ]

        candidates.append({
            "table_name": full_name,
            "description": payload.get("description", ""),
            "primary_time_col": payload.get("primary_time_col", ""),
            "join_keys": payload.get("join_keys", []),
            "columns": columns,
            "score": round(score or 0.0, 4),
        })

    return candidates


def _upsert_schema(docs: list[dict]):
    client = get_client()
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

    logger.info("upsert_schema: embedding docs=%s model=%s", len(texts), EMBEDDING_MODEL)
    vectors = embeddings.embed_documents(texts)

    points = []
    for doc, vector in zip(docs, vectors):
        point_id = str(
            uuid.uuid5(
                uuid.NAMESPACE_URL,
                f"{doc['schema']}.{doc['table_name']}:{doc['doc_type']}",
            )
        )
        points.append(PointStruct(id=point_id, vector=vector, payload=doc))

    client.upsert(
        collection_name=QDRANT_COLLECTION,
        points=points
    )
    logger.info("upsert_schema: upserted points=%s collection=%s", len(points), QDRANT_COLLECTION)
    return f"{len(points)}개 스키마 업로드 완료"


def _error(message: str) -> list[TextContent]:
    return [TextContent(type="text", text=message)]


@app.list_tools()
async def list_tools() -> list[Tool]:
    return [
        Tool(
            name="search_tables",
            description="사용자 질문과 관련된 테이블을 검색합니다",
            inputSchema={
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "사용자 질문"},
                    "top_k": {"type": "integer", "description": "검색할 후보 수", "default": 5},
                },
                "required": ["query"],
            },
        ),
        Tool(
            name="ensure_collection",
            description="Qdrant 컬렉션 확인 및 생성",
            inputSchema={
                "type": "object",
                "properties": {
                    "vector_size": {"type": "integer", "default": 1536}
                },
            },
        ),
        Tool(
            name="upsert_schema",
            description="스키마 정보(Docs)를 받아 임베딩 후 Qdrant에 저장",
            inputSchema={
                "type": "object",
                "properties": {
                    "docs": {
                        "type": "array",
                        "description": "스키마 문서 리스트",
                        "items": {"type": "object"},
                    }
                },
                "required": ["docs"],
            },
        ),
    ]


@app.call_tool()
async def call_tool(name: str, arguments: dict) -> list[TextContent]:
    if name == "search_tables":
        query = arguments.get("query", "")
        top_k = arguments.get("top_k", 5)
        if not query:
            return _error("오류: query가 비어있습니다")
        try:
            candidates = _search_qdrant(query, top_k)
            return [TextContent(type="text", text=json.dumps(candidates, ensure_ascii=False))]
        except Exception as e:
            return _error(f"검색 실패: {str(e)}")

    if name == "ensure_collection":
        size = arguments.get("vector_size", 1536)
        try:
            msg = _ensure_collection(size)
            return [TextContent(type="text", text=msg)]
        except Exception as e:
            return _error(f"컬렉션 생성 실패: {e}")

    if name == "upsert_schema":
        docs = arguments.get("docs", [])
        if not docs:
            return _error("docs가 비어있습니다")
        try:
            msg = _upsert_schema(docs)
            return [TextContent(type="text", text=msg)]
        except Exception as e:
            return _error(f"업서트 실패: {e}")

    return _error(f"알 수 없는 도구: {name}")


# HTTP Adapter
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
import uvicorn

http_app = FastAPI()


@http_app.get("/tools")
async def handle_list_tools():
    tools = await list_tools()
    return [{"name": t.name, "description": t.description, "inputSchema": t.inputSchema} for t in tools]


class CallToolRequest(BaseModel):
    name: str
    arguments: dict = {}


@http_app.post("/call")
async def handle_call_tool(req: CallToolRequest):
    try:
        result = await call_tool(req.name, req.arguments)
        return [{"type": c.type, "text": c.text} for c in result]
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


async def main():
    async with stdio_server() as (read, write):
        await app.run(read, write, app.create_initialization_options())


if __name__ == "__main__":
    import asyncio
    import sys

    if len(sys.argv) > 1 and sys.argv[1] == "http":
        port = int(os.getenv("PORT", 8000))
        uvicorn.run(http_app, host="0.0.0.0", port=port)
    else:
        asyncio.run(main())
