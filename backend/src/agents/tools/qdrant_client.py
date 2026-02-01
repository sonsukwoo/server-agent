"""Qdrant 클라이언트 유틸리티 - 테이블 검색 및 후보 추출"""
from dotenv import load_dotenv
load_dotenv()

from qdrant_client import QdrantClient
from qdrant_client.models import Distance, VectorParams, PointStruct
from langchain_openai import OpenAIEmbeddings
import json
from urllib import request

from config.settings import settings


# 임베딩 모델 (text-embedding-3-small: 1536차원)
embeddings = OpenAIEmbeddings(model="text-embedding-3-small")

# Qdrant 클라이언트 초기화
_client = None


def get_qdrant_client() -> QdrantClient:
    """Qdrant 클라이언트 싱글톤"""
    global _client
    if _client is None:
        _client = QdrantClient(
            url=settings.qdrant_url,
            api_key=settings.qdrant_api_key if settings.qdrant_api_key else None,
        )
    return _client


def embed_text(text: str) -> list[float]:
    """텍스트를 벡터로 임베딩"""
    return embeddings.embed_query(text)


async def search_related_tables(query: str, top_k: int = 5) -> list[dict]:
    """
    사용자 질문을 기반으로 관련 테이블 검색
    
    Args:
        query: 사용자 자연어 질문
        top_k: 반환할 후보 테이블 수
        
    Returns:
        [
            {
                "table_name": "ops_metrics.metrics_system",
                "description": "시스템 전체 CPU, RAM, 부하 지표",
                "columns": [...],  # 컬럼 상세 정보 리스트
                "score": 0.87
            },
            ...
        ]
    """
    client = get_qdrant_client()
    query_vector = embed_text(query)
    
    if hasattr(client, "search"):
        results = client.search(
            collection_name=settings.qdrant_collection,
            query_vector=query_vector,
            limit=top_k,
        )
    else:
        # 구버전 qdrant-client 대비: REST API 직접 호출
        url = f"{settings.qdrant_url}/collections/{settings.qdrant_collection}/points/search"
        payload = json.dumps({
            "vector": query_vector,
            "limit": top_k,
            "with_payload": True,
        }).encode("utf-8")
        headers = {"Content-Type": "application/json"}
        if settings.qdrant_api_key:
            headers["api-key"] = settings.qdrant_api_key
        req = request.Request(url, data=payload, headers=headers, method="POST")
        with request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        results = data.get("result", [])
    
    candidates = []
    for hit in results:
        payload = getattr(hit, "payload", None) or hit.get("payload", {}) or {}
        score = getattr(hit, "score", None)
        if score is None:
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


def ensure_collection_exists(vector_size: int = 1536):
    """컬렉션이 없으면 생성"""
    client = get_qdrant_client()
    collections = [c.name for c in client.get_collections().collections]
    
    if settings.qdrant_collection not in collections:
        client.create_collection(
            collection_name=settings.qdrant_collection,
            vectors_config=VectorParams(size=vector_size, distance=Distance.COSINE),
        )
        print(f"✅ 컬렉션 '{settings.qdrant_collection}' 생성 완료")
    else:
        print(f"ℹ️ 컬렉션 '{settings.qdrant_collection}' 이미 존재")


def upsert_table_embedding(table_name: str, description: str, columns: list[dict]):
    """
    테이블 정보를 Qdrant에 업서트
    
    Args:
        table_name: 전체 테이블명 (예: ops_metrics.metrics_system)
        description: 테이블 설명 (임베딩 대상)
        columns: 컬럼 정보 리스트 [{"name": ..., "type": ..., "description": ...}, ...]
    """
    client = get_qdrant_client()
    
    # 테이블 설명 임베딩
    vector = embed_text(description)
    
    # 고유 ID 생성 (테이블명 해시)
    point_id = abs(hash(table_name)) % (10 ** 12)
    
    point = PointStruct(
        id=point_id,
        vector=vector,
        payload={
            "table_name": table_name,
            "description": description,
            "columns": columns,
        },
    )
    
    client.upsert(
        collection_name=settings.qdrant_collection,
        points=[point],
    )
