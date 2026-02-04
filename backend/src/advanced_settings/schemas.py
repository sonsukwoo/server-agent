from datetime import datetime
from typing import Optional, Union
from pydantic import BaseModel, Field

# -----------------------------------------------------------------------------
# Pydantic Schemas (입력 데이터 검증)
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
    created_at: datetime

class AlertHistoryResponse(BaseModel):
    id: int
    rule_id: Optional[int]
    message: str
    value: float
    created_at: datetime
