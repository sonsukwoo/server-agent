"""스키마 해시 계산 및 파일 저장 유틸리티."""
import json
import hashlib
import logging
from pathlib import Path
from config.settings import settings

logger = logging.getLogger("SCHEMA_HASH")

def calculate_schema_hash(docs: list[dict]) -> str:
    """문서 리스트의 정규화된 해시값 계산."""
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
    # 정렬된 JSON으로 변환하여 일관성 보장
    canonical = json.dumps(payload, ensure_ascii=True, sort_keys=True)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

def read_hash_file() -> str | None:
    """저장된 해시 파일 읽기."""
    path = Path(settings.schema_hash_file)
    try:
        if path.exists():
            return path.read_text(encoding="utf-8").strip() or None
    except Exception as e:
        logger.warning("Hash read failed: %s", e)
    return None

def write_hash_file(schema_hash: str) -> None:
    """해시값 파일 저장."""
    path = Path(settings.schema_hash_file)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(schema_hash, encoding="utf-8")
    except Exception as e:
        logger.warning("Hash write failed: %s", e)
