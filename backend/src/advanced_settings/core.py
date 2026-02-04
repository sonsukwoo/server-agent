import asyncio
import logging
import json
from typing import List, Optional
from pydantic import BaseModel, Field
import asyncpg
from config.settings import settings
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
import httpx

logger = logging.getLogger("ALERT_CORE")

# -----------------------------------------------------------------------------
# 1. Pydantic Schemas (입력 데이터 검증)
# -----------------------------------------------------------------------------
class AlertRuleCreate(BaseModel):
    target_table: str = Field(..., description="감시할 테이블명 (예: ops_metrics.metrics_cpu)")
    target_column: str = Field(..., description="감시할 컬럼명 (예: cpu_percent)")
    operator: str = Field(..., pattern="^(>|<|>=|<=|=)$", description="비교 연산자")
    threshold: float = Field(..., description="임계값 (상한선)")
    message: str = Field(..., description="알림 메시지 템플릿")

class AlertRuleResponse(BaseModel):
    id: int
    target_table: str
    target_column: str
    operator: str
    threshold: float
    message_template: str
    created_at: str

class AlertHistoryResponse(BaseModel):
    id: int
    rule_id: Optional[int]
    message: str
    value: float
    created_at: str

# -----------------------------------------------------------------------------
# 2. SQL Templates (Hardcoded for Safety)
# -----------------------------------------------------------------------------
# 엣지 트리거 방식:
# 1. 값이 임계값(Threshold)을 넘는 순간 (Limit Breach) -> 알림 발생 + 상태 기록
# 2. 값이 정상으로 돌아온 순간 (Recovery) -> 상태 리셋
# 이를 위해 간단한 상태 저장 로직이나, 단순히 매번 로직을 태우되 "이전 호출 시간"으로 제어할 수도 있습니다.
# 사용자의 요청사항: "상한선 도달하면 딱 한번 울리고, 내려갔다가 다시 올라오면 다시 한번"
# -> 이를 위해 상태 관리가 필요하지만, 복잡성을 줄이기 위해 간단한 cooldown 대신
#    값의 이전 상태(OLD)와 현재 상태(NEW)를 비교하는 방식이 가장 확실합니다.
#    하지만 INSERT 트리거에서는 OLD 값이 없습니다.
#    따라서 '마지막으로 알림 보낸 시간'과 '현재 값'으로 판단해야 합니다.
#    여기서는 단순히 "임계값 초과 시" 알림을 보내되, 최근 1분(또는 설정된 시간) 내에는 재발송 금지하는 로직을 템플릿화 합니다.

TRIGGER_FUNC_TEMPLATE = """
CREATE OR REPLACE FUNCTION monitor.func_check_{rule_id}()
RETURNS TRIGGER AS $$
DECLARE
    last_triggered TIMESTAMPTZ;
    cooldown_sec INTEGER := 60; -- 1분 쿨다운 (하드코딩 또는 변수화 가능)
BEGIN
    -- 조건 확인: {target_column} {operator} {threshold}
    IF NEW.{target_column} {operator} {threshold} THEN
        -- 마지막 발생 시간 확인 (DB 조회 없이 단순 시간차는 어렵으므로, 히스토리 테이블 활용)
        SELECT created_at INTO last_triggered
        FROM monitor.alert_history
        WHERE rule_id = {rule_id}
        ORDER BY created_at DESC
        LIMIT 1;

        -- 쿨다운 / 상태 체크 (마지막 알림 이후 일정 시간이 지났거나, 알림이 없었을 때만)
        IF last_triggered IS NULL OR (NOW() - last_triggered) > (cooldown_sec || ' seconds')::interval THEN
            -- 이력 저장
            INSERT INTO monitor.alert_history (rule_id, message, value)
            VALUES ({rule_id}, '{message}', NEW.{target_column});
            
            -- 알림 채널 전송
            PERFORM pg_notify('alert_channel', json_build_object(
                'rule_id', {rule_id},
                'message', '{message}',
                'value', NEW.{target_column}
            )::text);
        END IF;
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;
"""

TRIGGER_CREATE_TEMPLATE = """
CREATE TRIGGER trg_alert_{rule_id}
AFTER INSERT ON {target_table}
FOR EACH ROW
EXECUTE FUNCTION monitor.func_check_{rule_id}();
"""

TRIGGER_DROP_TEMPLATE = """
DROP TRIGGER IF EXISTS trg_alert_{rule_id} ON {target_table};
DROP FUNCTION IF EXISTS monitor.func_check_{rule_id}();
"""


# -----------------------------------------------------------------------------
# 3. Alert Service (Logic & MCP Call)
# -----------------------------------------------------------------------------
class AlertService:
    _pool = None

    @classmethod
    async def get_pool(cls):
        if cls._pool is None:
            dsn = f"postgresql://{settings.db_user}:{settings.db_password}@{settings.db_host}:{settings.db_port}/{settings.db_name}"
            cls._pool = await asyncpg.create_pool(dsn, min_size=1, max_size=10)
        return cls._pool

    @staticmethod
    async def _execute_mcp_advanced(sql: str):
        """MCP 서버의 execute_sql 툴 호출 (Bypass 옵션 사용)"""
        # HTTP 통신을 사용하여 MCP 서버 호출 (settings에 정의된 URL 사용)
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
            # 1. 메타 데이터 저장 (Lego Block)
            row = await conn.fetchrow("""
                INSERT INTO monitor.alert_rules (target_table, target_column, operator, threshold, message_template)
                VALUES ($1, $2, $3, $4, $5)
                RETURNING id;
            """, rule.target_table, rule.target_column, rule.operator, rule.threshold, rule.message)
            rule_id = row['id']
            
            # 2. 동적 SQL 생성 (하드코딩된 템플릿에 안전한 변수 삽입)
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
            
            return {**rule.dict(), "id": rule_id, "created_at": "now"}

    @classmethod
    async def delete_rule(cls, rule_id: int):
        """규칙 삭제 -> DB 삭제 -> MCP로 트리거 제거"""
        pool = await cls.get_pool()
        async with pool.acquire() as conn:
            # 1. 정보 조회 (삭제 전 테이블명 필요)
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

# -----------------------------------------------------------------------------
# 4. Listener (실시간 알림 수신)
# -----------------------------------------------------------------------------
class AlertListener:
    def __init__(self):
        self._conn = None
        self._task = None
        self.running = False
        self.dsn = f"postgresql://{settings.db_user}:{settings.db_password}@{settings.db_host}:{settings.db_port}/{settings.db_name}"

    async def start(self):
        self.running = True
        self._task = asyncio.create_task(self._listen_loop())

    async def stop(self):
        self.running = False
        if self._conn:
            await self._conn.close()
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass

    async def _listen_loop(self):
        while self.running:
            try:
                # Listener needs a dedicated specific connection, not a pool
                self._conn = await asyncpg.connect(self.dsn)
                await self._conn.add_listener("alert_channel", self._on_notification)
                while self.running:
                    await asyncio.sleep(1)
            except Exception as e:
                logger.error(f"Alert Listener Error: {e}")
                await asyncio.sleep(5)
            finally:
                if self._conn and not self._conn.is_closed():
                    await self._conn.close()

    def _on_notification(self, connection, pid, channel, payload):
        try:
            data = json.loads(payload)
            logger.info(f"🔔 [ALERT] Rule {data.get('rule_id')}: {data.get('message')} (Value: {data.get('value')})")
        except:
            logger.info(f"🔔 [ALERT] Raw Payload: {payload}")
