"""고급 설정 Pydantic 스키마 정의."""

from datetime import datetime
from typing import Optional
from pydantic import BaseModel, Field


class AlertRuleCreate(BaseModel):
    """알림 규칙 생성 요청 데이터 검증 스키마."""
    target_table: str = Field(..., description="감시할 테이블명 (예: ops_metrics.metrics_cpu)")
    target_column: str = Field(..., description="감시할 컬럼명 (예: cpu_percent)")
    operator: str = Field(..., pattern="^(>|<|>=|<=|=)$", description="비교 연산자")
    threshold: float = Field(..., description="임계값 (상한선)")
    message: str = Field(..., description="알림 메시지 템플릿")

class AlertRuleResponse(BaseModel):
    """알림 규칙 조회 응답 스키마."""
    id: int
    target_table: str
    target_column: str
    operator: str
    threshold: float
    message_template: str
    created_at: datetime

class AlertHistoryResponse(BaseModel):
    """발생한 알림 이력 조회 응답 스키마."""
    id: int
    rule_id: Optional[int]
    message: str
    value: float
    created_at: datetime
