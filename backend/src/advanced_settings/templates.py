"""고급 설정 템플릿 및 쿼리."""

# -----------------------------------------------------------------------------
# SQL Templates (Hardcoded for Safety)
# -----------------------------------------------------------------------------

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
