"""고급 설정 서비스 계층."""

import logging
import httpx
from config.settings import settings
from src.db.db_manager import db_manager
from .schemas import AlertRuleCreate
from .templates import TRIGGER_FUNC_TEMPLATE, TRIGGER_CREATE_TEMPLATE, TRIGGER_DROP_TEMPLATE

logger = logging.getLogger("ALERT_SERVICE")

class AlertService:
    @classmethod
    async def get_pool(cls):
        return await db_manager.get_pool()

    @staticmethod
    async def _execute_mcp_advanced(sql: str):
        """MCP 서버의 execute_sql 툴 호출 (Bypass 옵션 사용)"""
        url = f"{settings.mcp_postgres_url}/call"
        async with httpx.AsyncClient() as client:
            try:
                resp = await client.post(url, json={
                    "name": "execute_sql",
                    "arguments": {
                        "query": sql,
                        "bypass_validation": True  # 핵심: 보안 우회 플래그
                    }
                })
                resp.raise_for_status()
                return resp.json()
            except Exception as e:
                logger.error(f"MCP Call Failed: {e}")
                raise

    @classmethod
    async def create_rule(cls, rule: AlertRuleCreate):
        """규칙 등록 -> DB 저장 -> MCP로 트리거 생성"""
        pool = await cls.get_pool()
        async with pool.acquire() as conn:
            # 1. 메타 데이터 저장
            row = await conn.fetchrow("""
                INSERT INTO monitor.alert_rules (target_table, target_column, operator, threshold, message_template)
                VALUES ($1, $2, $3, $4, $5)
                RETURNING id;
            """, rule.target_table, rule.target_column, rule.operator, rule.threshold, rule.message)
            rule_id = row['id']
            
            # 2. 동적 SQL 생성
            func_sql = TRIGGER_FUNC_TEMPLATE.format(
                rule_id=rule_id,
                target_column=rule.target_column,
                operator=rule.operator,
                threshold=rule.threshold,
                message=rule.message
            )
            trigger_sql = TRIGGER_CREATE_TEMPLATE.format(
                rule_id=rule_id,
                target_table=rule.target_table
            )
            
            # 3. MCP를 통해 트리거/함수 생성 (DDL)
            full_sql = f"{func_sql}\n{trigger_sql}"
            await cls._execute_mcp_advanced(full_sql)
            
            # 4. 생성된 전체 데이터 리턴
            new_row = await conn.fetchrow("SELECT * FROM monitor.alert_rules WHERE id = $1", rule_id)
            return dict(new_row)

    @classmethod
    async def delete_rule(cls, rule_id: int):
        """규칙 삭제 -> DB 삭제 -> MCP로 트리거 제거"""
        pool = await cls.get_pool()
        async with pool.acquire() as conn:
            # 1. 정보 조회
            row = await conn.fetchrow("SELECT target_table FROM monitor.alert_rules WHERE id = $1", rule_id)
            if not row:
                return False
            
            target_table = row['target_table']
            
            # 2. DB에서 메타 삭제
            await conn.execute("DELETE FROM monitor.alert_rules WHERE id = $1", rule_id)
            
            # 3. MCP를 통해 트리거/함수 제거
            drop_sql = TRIGGER_DROP_TEMPLATE.format(
                rule_id=rule_id,
                target_table=target_table
            )
            await cls._execute_mcp_advanced(drop_sql)
            return True

    @classmethod
    async def list_rules(cls):
        pool = await cls.get_pool()
        async with pool.acquire() as conn:
            rows = await conn.fetch("SELECT * FROM monitor.alert_rules ORDER BY created_at DESC")
            return [dict(r) for r in rows]

    @classmethod
    async def list_alerts(cls):
        pool = await cls.get_pool()
        async with pool.acquire() as conn:
            rows = await conn.fetch("SELECT * FROM monitor.alert_history ORDER BY created_at DESC LIMIT 100")
            return [dict(r) for r in rows]

    @classmethod
    async def delete_alert(cls, alert_id: int):
        pool = await cls.get_pool()
        async with pool.acquire() as conn:
            await conn.execute("DELETE FROM monitor.alert_history WHERE id = $1", alert_id)
            return True
