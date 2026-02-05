"""리소스 요약 정보 조회 API."""
import json
import logging
from fastapi import APIRouter
from src.agents.mcp_clients.connector import postgres_client

router = APIRouter(tags=["resource"])
logger = logging.getLogger("API_RESOURCE")

@router.get("/resource-summary")
async def get_resource_summary():
    """리소스 요약 뷰(ops_metrics.v_resource_summary) 최신 데이터 조회."""
    sql = "SELECT * FROM ops_metrics.v_resource_summary ORDER BY \"배치 ID\" DESC LIMIT 1"
    try:
        async with postgres_client() as client:
            result_raw = await client.call_tool("execute_sql", {"query": sql})
            if not result_raw:
                return {}
            
            try:
                result = json.loads(result_raw)
            except json.JSONDecodeError:
                logger.warning("Resource summary JSON parse failed: %s", result_raw)
                return {}

            if result and isinstance(result, list):
                return result[0]
            return {}
    except Exception as e:
        logger.error("Resource summary error: %s", e)
        return {}
