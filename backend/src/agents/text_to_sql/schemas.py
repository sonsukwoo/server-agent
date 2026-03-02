"""Text-to-SQL 구조화 응답 스키마 모음.

LLM 구조화 출력을 Pydantic 모델로 검증
"""

from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator


class StructuredModel(BaseModel):
    """OpenAI 구조화 응답 호환 기본 모델."""

    model_config = ConfigDict(extra="forbid")


class IntentClassification(StructuredModel):
    """의도 분류기 출력."""

    intent: str = Field(default="sql")
    reason: str = Field(default="")

    @field_validator("intent", mode="before")
    @classmethod
    def _normalize_intent(cls, value: Any) -> str:
        if not isinstance(value, str):
            return "sql"
        normalized = value.strip().lower()
        return normalized if normalized in {"sql", "general"} else "sql"


class ClarificationCheck(StructuredModel):
    """역질문 필요 여부 판단 출력."""

    needs_clarification: bool = Field(default=False)
    question: str = Field(default="")


class TimeRangeModel(StructuredModel):
    """파싱된 시간 범위."""

    start: str | None = None
    end: str | None = None
    timezone: str | None = None
    all_time: bool | None = None
    inherit: bool | None = None


class ParsedRequestModel(StructuredModel):
    """구조화된 요청 파싱 결과."""

    intent: str = Field(default="unknown")
    is_followup: bool = Field(default=False)
    time_range: TimeRangeModel | None = None
    metric: str | None = None
    condition: str | None = None
    output: str | None = None

    @field_validator("intent", mode="before")
    @classmethod
    def _normalize_intent(cls, value: Any) -> str:
        if not isinstance(value, str):
            return "unknown"
        normalized = value.strip()
        return normalized or "unknown"


class TimeScopeMode(str, Enum):
    """시간 범위 결정 모드."""

    ALL_TIME = "all_time"
    INHERIT = "inherit"
    EXPLICIT = "explicit"
    RELATIVE = "relative"


class TimeScopeDecision(StructuredModel):
    """시간 범위 결정기 출력."""

    mode: TimeScopeMode = Field(default=TimeScopeMode.INHERIT)
    start: str | None = None
    end: str | None = None
    timezone: str | None = None
    anchor: str | None = None
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    reason: str = Field(default="")
    needs_clarification: bool = Field(default=False)
    clarification_question: str = Field(default="")


class TableRerankItem(StructuredModel):
    """리랭크 점수 단일 항목."""

    index: int = Field(ge=1)
    score: float


class TableRerankResult(StructuredModel):
    """안정적인 구조화 파싱을 위한 리랭크 리스트 래퍼."""

    items: list[TableRerankItem] = Field(default_factory=list)


class GenerateSqlResult(StructuredModel):
    """SQL 생성 결과."""

    sql: str = Field(default="")
    needs_more_tables: bool = Field(default=False)


class ValidationVerdict(str, Enum):
    """최종 검증 판정 열거형."""

    OK = "OK"
    SQL_BAD = "SQL_BAD"
    RETRY_SQL = "RETRY_SQL"
    TABLE_MISSING = "TABLE_MISSING"
    DATA_MISSING = "DATA_MISSING"
    COLUMN_MISSING = "COLUMN_MISSING"
    PERMISSION = "PERMISSION"
    TYPE_ERROR = "TYPE_ERROR"
    TIMEOUT = "TIMEOUT"
    DB_CONN_ERROR = "DB_CONN_ERROR"
    AMBIGUOUS = "AMBIGUOUS"


class ValidationResult(StructuredModel):
    """결과 검증기 출력."""

    verdict: ValidationVerdict = Field(default=ValidationVerdict.OK)
    reason: str = Field(default="")
    hint: str = Field(default="")
    unnecessary_tables: list[str] = Field(default_factory=list)
