"""
데이터베이스 알림 트리거 및 함수 설정용 SQL 템플릿.

알림 규칙 생성 시 DB에 적용할 트리거와 함수를 정의하며,
실시간 감시 및 조건 충족 시 알림 메커니즘을 설정합니다.
"""

TRIGGER_FUNC_TEMPLATE = """
CREATE OR REPLACE FUNCTION monitor.func_check_{rule_id}()
RETURNS TRIGGER AS $$
DECLARE
    last_triggered TIMESTAMPTZ;
    cooldown_sec INTEGER := 60; -- 알림 쿨다운 (60초)
BEGIN
    -- 조건 확인
    IF NEW.{target_column} {operator} {threshold} THEN
        -- 최근 알림 시간 조회
        SELECT created_at INTO last_triggered
        FROM monitor.alert_history
        WHERE rule_id = {rule_id}
        ORDER BY created_at DESC
        LIMIT 1;

        -- 쿨다운 체크 및 알림 발송
        IF last_triggered IS NULL OR (NOW() - last_triggered) > (cooldown_sec || ' seconds')::interval THEN
            -- 이력 저장
            INSERT INTO monitor.alert_history (rule_id, message, value)
            VALUES ({rule_id}, '{message}', NEW.{target_column});
            
            -- 알림 채널로 이벤트 전송
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
