"""Text-to-SQL 노드 공통 헬퍼 함수."""

import json
import re
import logging

from langchain_openai import ChatOpenAI
from langchain_core.messages import SystemMessage, HumanMessage, AIMessage
from langchain_core.messages import trim_messages

from config.settings import settings
from ..state import TextToSQLState
from ..prompts import (
    RERANK_TABLE_SYSTEM,
    RERANK_TABLE_USER,
    GENERATE_SQL_SYSTEM,
    GENERATE_SQL_USER,
    VALIDATE_RESULT_SYSTEM,
    VALIDATE_RESULT_USER,
)
from .constants import TOP_K
from .utils import (
    get_current_time,
    parse_json_from_llm,
    rebuild_context_from_candidates,
    apply_elbow_cut,
)

logger = logging.getLogger("TEXT_TO_SQL")

# ─────────────────────────────────────────
# LLM Runtime Objects
# ─────────────────────────────────────────

llm_fast = ChatOpenAI(
    model=settings.model_fast,
    temperature=0,
    api_key=settings.openai_api_key,
)
llm_smart = ChatOpenAI(
    model=settings.model_smart,
    temperature=0,
    api_key=settings.openai_api_key,
)

# JSON Mode 강제 바인딩
structured_llm_fast = llm_fast.bind(response_format={"type": "json_object"})


# ─────────────────────────────────────────
# 대화 히스토리 기반 SQL 추출
# ─────────────────────────────────────────

def _extract_previous_sql_from_messages(state: TextToSQLState) -> str:
    """state['messages']에서 가장 최근 AI 응답 안의 SQL 블록을 추출.

    SSOT 원칙: 'generated_sql'은 현재 턴의 임시 상태일 수 있으므로 참조하지 않고,
    오직 확정된 대화 히스토리(messages)에서만 이전 쿼리를 찾습니다.
    """
    for msg in reversed(state.get("messages", [])):
        if isinstance(msg, AIMessage) and not msg.tool_calls:
            sql_match = re.search(r"```sql\n(.*?)\n```", msg.content, re.DOTALL)
            if sql_match:
                return sql_match.group(1).strip()
    return ""


# ─────────────────────────────────────────
# 테이블 선택(리랭킹) 보조 함수
# ─────────────────────────────────────────

def _format_candidates_for_rerank(candidates: list, top_col_limit: int = 5) -> str:
    """리랭킹을 위해 후보 테이블 정보를 문자열로 포맷팅."""
    lines = []
    for i, c in enumerate(candidates, 1):
        cols = c.get("columns", []) or []
        col_lines = []
        for col in cols[:top_col_limit]:
            desc = col.get("description", "") or ""
            if len(desc) > 100:
                desc = desc[:100] + "..."
            col_lines.append(f"- {col.get('name')} ({col.get('type')}): {desc}")

        lines.append(
            "\n".join(
                [
                    f"[{i}] {c.get('table_name')}",
                    f"  - description: {c.get('description') or ''}",
                    f"  - primary_time_col: {c.get('primary_time_col') or '없음'}",
                    f"  - join_keys: {', '.join(c.get('join_keys') or []) or '없음'}",
                    f"  - score: {c.get('score')}",
                    "  - columns:",
                    *col_lines,
                ]
            )
        )
    return "\n\n".join(lines)


async def _call_rerank_llm(parsed: dict, candidates_str: str) -> list | None:
    """LLM을 호출하여 테이블 리랭킹 수행 (JSON 응답)."""
    messages = [
        SystemMessage(content=RERANK_TABLE_SYSTEM),
        HumanMessage(
            content=RERANK_TABLE_USER.format(
                intent=parsed.get("intent", ""),
                metric=parsed.get("metric", ""),
                condition=parsed.get("condition", ""),
                time_range=parsed.get("time_range", {}),
                candidates=candidates_str,
            )
        ),
    ]
    response = await llm_smart.ainvoke(messages)
    parsed_json, error = parse_json_from_llm(response.content)
    if error:
        logger.error("TEXT_TO_SQL:select_tables rerank_json_error=%s", error)
        return None
    return parsed_json


def _parse_rerank_response(response_json: list, candidates_len: int) -> list[int] | None:
    """리랭킹 LLM 응답 파싱 및 Elbow Cut 적용."""
    if not isinstance(response_json, list):
        return None

    scored = []
    for item in response_json:
        if not isinstance(item, dict):
            continue
        try:
            idx = int(item.get("index"))
            score = float(item.get("score"))
            if 1 <= idx <= candidates_len:
                scored.append({"index": idx, "score": score})
        except (ValueError, TypeError):
            continue

    scored.sort(key=lambda x: x["score"], reverse=True)
    final_scored = apply_elbow_cut(scored)

    if not final_scored:
        return None

    return [s["index"] for s in final_scored]


def _select_candidates(candidates: list, selected_indices: list[int]) -> list[str]:
    """선택된 인덱스(1-based)를 기반으로 테이블명 리스트 반환."""
    selected_names = []
    for idx in selected_indices:
        real_idx = idx - 1
        if 0 <= real_idx < len(candidates):
            selected_names.append(candidates[real_idx]["table_name"])
    return selected_names


# ─────────────────────────────────────────
# SQL/시간 정보 추출 보조 함수
# ─────────────────────────────────────────

def _extract_tables_from_sql(sql: str) -> list[str]:
    """SQL 쿼리에서 FROM/JOIN 테이블 이름 추출."""
    tables = []
    pattern = r"(?:FROM|JOIN)\s+([a-zA-Z_][a-zA-Z0-9_]*(?:\.[a-zA-Z_][a-zA-Z0-9_]*)?)"
    matches = re.findall(pattern, sql, re.IGNORECASE)
    for match in matches:
        if match.upper() not in ("SELECT", "WHERE", "AND", "OR", "ON", "AS"):
            tables.append(match)
    return list(set(tables))


def _extract_time_range_from_sql(sql: str) -> tuple[str, str]:
    """SQL에서 ts BETWEEN A AND B 구문을 찾아 A, B 시간값 반환."""
    pattern = r"ts\s+BETWEEN\s+'([^']+)'\s+AND\s+'([^']+)'"
    match = re.search(pattern, sql, re.IGNORECASE)
    if match:
        return match.group(1), match.group(2)
    return "", ""


# ─────────────────────────────────────────
# SQL 생성 프롬프트 구성 함수
# ─────────────────────────────────────────

def _build_sql_prompt_inputs(state: TextToSQLState) -> dict:
    """SQL 생성 프롬프트에 주입할 변수 딕셔너리 구성."""
    failed = state.get("failed_queries", []) or []
    time_range = state.get("parsed_request", {}).get("time_range", {})

    previous_sql = _extract_previous_sql_from_messages(state)

    if time_range.get("all_time"):
        time_mode = "all_time"
        time_start = "전체"
        time_end = "현재"
        inherit_start = ""
        inherit_end = ""
    elif time_range.get("inherit"):
        time_mode = "inherit"
        p_start, p_end = _extract_time_range_from_sql(previous_sql)
        if p_start and p_end:
            time_start = f"{p_start} (상속됨)"
            time_end = f"{p_end} (상속됨)"
            inherit_start = p_start
            inherit_end = p_end
        else:
            time_start = ""
            time_end = ""
            inherit_start = ""
            inherit_end = ""
    else:
        time_mode = "explicit"
        time_start = time_range.get("start", "N/A")
        time_end = time_range.get("end", "N/A")
        inherit_start = ""
        inherit_end = ""

    return {
        "intent": state.get("parsed_request", {}).get("intent", ""),
        "time_mode": time_mode,
        "time_start": time_start,
        "time_end": time_end,
        "inherit_start": inherit_start,
        "inherit_end": inherit_end,
        "metric": state.get("parsed_request", {}).get("metric", ""),
        "condition": state.get("parsed_request", {}).get("condition", ""),
        "user_constraints": state.get("user_constraints", "") or "",
        "table_name": ", ".join(state.get("selected_tables", []) or []),
        "columns": state.get("table_context", ""),
        "previous_sql": previous_sql,
        "failed_queries": "\n".join(failed[-3:]),
        "validation_reason": state.get("validation_reason", ""),
        "_meta_table_count": len(state.get("selected_tables", []) or []),
        "_meta_failed_count": len(failed),
    }


def _build_generate_sql_messages(inputs: dict) -> list:
    """SQL 생성용 LLM 메시지 리스트 생성."""
    return [
        SystemMessage(content=GENERATE_SQL_SYSTEM),
        HumanMessage(
            content=GENERATE_SQL_USER.format(
                intent=inputs["intent"],
                time_mode=inputs["time_mode"],
                time_start=inputs["time_start"],
                time_end=inputs["time_end"],
                metric=inputs["metric"],
                condition=inputs["condition"],
                user_constraints=inputs["user_constraints"],
                table_name=inputs["table_name"],
                columns=inputs["columns"],
                previous_sql=inputs.get("previous_sql", "없음"),
                failed_queries=inputs["failed_queries"],
                validation_reason=inputs["validation_reason"],
            )
        ),
    ] 


# ─────────────────────────────────────────
# 검증/재시도 상태 보조 함수
# ─────────────────────────────────────────

def _append_failed_query(failed_queries: list[str], sql: str) -> list[str]:
    """실패한 쿼리 히스토리 업데이트 (최근 3개 유지)."""
    if not sql:
        return failed_queries
    failed_queries.append(sql)
    return failed_queries[-3:]


def _build_validation_messages(state: TextToSQLState, current_sql: str) -> list:
    """결과 검증용 LLM 메시지 리스트 생성."""
    time_range = state.get("parsed_request", {}).get("time_range", {})

    if time_range.get("all_time"):
        time_mode = "all_time"
        time_start = "전체"
        time_end = "현재"
    elif time_range.get("inherit"):
        time_mode = "inherit"
        previous_sql = _extract_previous_sql_from_messages(state)

        p_start, p_end = _extract_time_range_from_sql(previous_sql)
        if p_start and p_end:
            time_start = f"{p_start} (상속됨)"
            time_end = f"{p_end} (상속됨)"
        else:
            time_start = "이전 쿼리 시간 상속"
            time_end = "이전 쿼리 시간 상속"
    else:
        time_mode = "explicit"
        time_start = time_range.get("start", "N/A")
        time_end = time_range.get("end", "N/A")

    return [
        SystemMessage(content=VALIDATE_RESULT_SYSTEM),
        HumanMessage(
            content=VALIDATE_RESULT_USER.format(
                current_time=get_current_time(),
                user_question=state.get("user_question", ""),
                time_mode=time_mode,
                time_start=time_start,
                time_end=time_end,
                user_constraints=state.get("user_constraints", "") or "",
                generated_sql=current_sql,
                table_context=state.get("table_context", ""),
                sql_result=json.dumps(state.get("sql_result", [])[:10], ensure_ascii=False),
                failed_queries="\n".join(state.get("failed_queries", [])[-3:]),
                validation_reason=state.get("validation_reason", ""),
            )
        ),
    ]


def _handle_unnecessary_tables(
    state: TextToSQLState, unnecessary: list[str], failed_queries: list[str]
):
    """불필요한 테이블 제거 및 재시도 상태 구성."""
    if not unnecessary:
        return None
    selected = state.get("selected_tables", []) or []
    filtered = [t for t in selected if t not in unnecessary]
    if not filtered or filtered == selected:
        return None
    _, new_context = rebuild_context_from_candidates(
        state.get("table_candidates", []) or [], filtered
    )
    logger.info("TEXT_TO_SQL:validate_llm unnecessary tables found, retrying with filtered context")
    return {
        "selected_tables": filtered,
        "table_context": new_context,
        "verdict": "RETRY_SQL",
        "validation_reason": "불필요한 테이블 제거 후 재시도",
        "validation_retry_count": state.get("validation_retry_count", 0) + 1,
        "failed_queries": failed_queries,
        "total_loops": state.get("total_loops", 0) + 1,
        "last_tool_usage": "검증 피드백 반영: 불필요한 테이블 제거",
    }


def _format_failed_feedback(feedback: str, hint: str) -> str:
    """검증 실패 피드백 및 힌트 포맷팅."""
    full_feedback = f"### 이전 시도 실패 원인\n{feedback}\n"
    if hint:
        full_feedback += f"\n### 올바른 쿼리 예시 및 힌트\n{hint}\n"
    return full_feedback


# ─────────────────────────────────────────
# 대화 히스토리 트리밍
# ─────────────────────────────────────────

MAX_HISTORY_TOKENS = 4000
"""대화 히스토리 최대 토큰 수. 초과 시 오래된 메시지부터 제거."""


def _trim_conversation(state: TextToSQLState) -> list:
    """State의 messages를 토큰 기준으로 트리밍하여 반환."""
    messages = state.get("messages", [])
    if not messages:
        return []

    return trim_messages(
        messages,
        max_tokens=MAX_HISTORY_TOKENS,
        strategy="last",
        token_counter=llm_fast,
        allow_partial=False,
    )
