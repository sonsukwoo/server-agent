"""고급 설정 서비스 계층 모듈."""

import logging
import httpx
from config.settings import settings
from src.db.db_manager import db_manager
from .schemas import AlertRuleCreate
from .templates import TRIGGER_FUNC_TEMPLATE, TRIGGER_CREATE_TEMPLATE, TRIGGER_DROP_TEMPLATE

logger = logging.getLogger("ALERT_SERVICE")

class AlertService:
    """알림 규칙 관리 및 트리거 제어 서비스 로직."""

    @classmethod
    async def get_pool(cls):
        """DB 커넥션 풀 획득."""
        return await db_manager.get_pool()

    @staticmethod
    async def _execute_mcp_advanced(sql: str):
        """MCP 서버를 통한 동적 SQL 실행 (유효성 검사 우회)."""
        url = f"{settings.mcp_postgres_url}/call"
        async with httpx.AsyncClient() as client:
            try:
                resp = await client.post(url, json={
                    "name": "execute_sql",
                    "arguments": {
                        "query": sql,
                        "bypass_validation": True  # 보안 검사 우회
                    }
                })
                resp.raise_for_status()
                return resp.json()
            except Exception as e:
                logger.error(f"MCP 호출 실패: {e}")
                raise

    @classmethod
    async def create_rule(cls, rule: AlertRuleCreate):
        """규칙 메타 저장 및 DB 트리거 생성."""
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
        """규칙 메타 삭제 및 DB 트리거 제거."""
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
        """등록된 감시 규칙 목록 조회."""
        pool = await cls.get_pool()
        async with pool.acquire() as conn:
            rows = await conn.fetch("SELECT * FROM monitor.alert_rules ORDER BY created_at DESC")
            return [dict(r) for r in rows]

    @classmethod
    async def list_alerts(cls):
        """발생한 알림 이력 조회 (최근 100건)."""
        pool = await cls.get_pool()
        async with pool.acquire() as conn:
            rows = await conn.fetch("SELECT * FROM monitor.alert_history ORDER BY created_at DESC LIMIT 100")
            return [dict(r) for r in rows]

    @classmethod
    async def delete_alert(cls, alert_id: int):
        """특정 알림 이력 삭제."""
        pool = await cls.get_pool()
        async with pool.acquire() as conn:
            await conn.execute("DELETE FROM monitor.alert_history WHERE id = $1", alert_id)
            return True
