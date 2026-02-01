"""Text-to-SQL 에이전트 노드/미들웨어 (통합형)"""
import json
import os
import re
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from dotenv import load_dotenv
from langchain_openai import ChatOpenAI
from langchain_core.messages import SystemMessage, HumanMessage

from config.settings import settings
from src.agents.tools.qdrant_client import search_related_tables
from src.agents.tools.connector import postgres_client

from .state import TextToSQLState
from .prompts import (
    PARSE_REQUEST_SYSTEM, PARSE_REQUEST_USER,
    RERANK_TABLE_SYSTEM, RERANK_TABLE_USER,
    GENERATE_SQL_SYSTEM, GENERATE_SQL_USER,
    VALIDATE_RESULT_SYSTEM, VALIDATE_RESULT_USER,
    GENERATE_REPORT_SYSTEM, GENERATE_REPORT_USER,
)

load_dotenv(override=True)

# ─────────────────────────────────────────
# 기본 설정 (루프 가드 포함)
# - RETRIEVE_K: 벡터 검색에서 확보할 후보 테이블 수
# - TOP_K: LLM에게 제공할 초기 테이블 수
# - EXPAND_STEP: TABLE_MISSING 발생 시 추가로 확장할 개수
# - MAX_*: 각 루프/확장 제한
# ─────────────────────────────────────────
RETRIEVE_K = 15
TOP_K = 5
EXPAND_STEP = 5
MAX_TABLE_EXPAND = 2
MAX_SQL_RETRY = 2
MAX_VALIDATION_RETRY = 1
MAX_TOTAL_LOOPS = 5

TIMEZONE = settings.tz

llm_fast = ChatOpenAI(model=settings.model_fast, temperature=0)
llm_smart = ChatOpenAI(model=settings.model_smart, temperature=0)


# ─────────────────────────────────────────
# 유틸리티
# - 시간 처리, JSON 파싱, SQL 안전 규칙, 에러 분류, 테이블 컨텍스트 구성
# ─────────────────────────────────────────

def get_current_time() -> str:
    # 현재 시간을 ISO 8601 문자열로 반환
    return datetime.now(ZoneInfo(TIMEZONE)).isoformat()


def get_now() -> datetime:
    # 현재 시간을 datetime 객체로 반환 (타임존 포함)
    return datetime.now(ZoneInfo(TIMEZONE))


def parse_json_from_llm(text: str) -> tuple[dict | None, str | None]:
    # LLM 응답에서 JSON 블록을 안전하게 추출하고 파싱
    try:
        match = re.search(r"```(?:json)?\s*([\s\S]*?)```", text)
        if match:
            text = match.group(1)
        return json.loads(text.strip()), None
    except json.JSONDecodeError as e:
        return None, f"JSON 파싱 실패: {e}. 원본: {text[:100]}..."


def normalize_sql(sql: str) -> str:
    # SQL 코드 블록 제거 및 안전 규칙 적용
    sql = sql.strip()
    match = re.search(r"```(?:sql)?\s*([\s\S]*?)```", sql)
    if match:
        sql = match.group(1).strip()

    upper_sql = sql.upper()
    if not (upper_sql.startswith("SELECT") or upper_sql.startswith("WITH")):
        raise ValueError(f"SELECT 또는 WITH 쿼리만 허용됩니다. 받은 쿼리: {sql[:50]}...")

    dangerous = ["DROP", "DELETE", "UPDATE", "INSERT", "TRUNCATE", "ALTER"]
    for keyword in dangerous:
        if keyword in upper_sql:
            raise ValueError(f"위험한 키워드 포함: {keyword}")

    # 다중 쿼리 차단 (세미콜론 중복 방지)
    if ";" in sql.rstrip(";"):
        raise ValueError("다중 쿼리는 허용되지 않습니다")

    if "LIMIT" not in upper_sql:
        sql = sql.rstrip(";") + " LIMIT 100"

    return sql


def build_table_context(selected: list[dict]) -> str:
    # 선택된 테이블 목록을 LLM 프롬프트용 컨텍스트 문자열로 변환
    blocks = []
    for t in selected:
        cols = t.get("columns", []) or []
        columns_str = "\n".join([
            f"- {c.get('name', '')} ({c.get('type', '')}): {c.get('description', 'N/A')}"
            for c in cols
        ])
        join_keys = ", ".join(t.get("join_keys", []) or [])
        primary_time_col = t.get("primary_time_col", "")
        blocks.append(
            f"테이블: {t['table_name']}\n"
            f"설명: {t.get('description', '')}\n"
            f"시간 컬럼: {primary_time_col}\n"
            f"조인 키: {join_keys}\n\n"
            f"컬럼:\n{columns_str}"
        )
    return "\n\n---\n\n".join(blocks)


def classify_sql_error(sql_error: str) -> tuple[str, str]:
    # SQL 에러 메시지를 규칙 기반으로 분류해 verdict/사유 반환
    err = sql_error.lower()
    if "relation" in err and "does not exist" in err:
        return "TABLE_MISSING", "테이블이 존재하지 않습니다"
    if "column" in err and "does not exist" in err:
        return "COLUMN_MISSING", "컬럼이 존재하지 않습니다"
    if "syntax error" in err or "at or near" in err:
        return "SQL_BAD", "SQL 문법 오류"
    if "permission denied" in err:
        return "PERMISSION", "권한 문제"
    if "invalid input syntax" in err or "cannot cast" in err:
        return "TYPE_ERROR", "타입 변환 오류"
    if "division by zero" in err:
        return "SQL_BAD", "0으로 나누기 오류"
    if "timeout" in err:
        return "TIMEOUT", "쿼리 시간 초과"
    if "could not connect" in err or "connection" in err:
        return "DB_CONN_ERROR", "DB 연결 오류"
    return "SQL_BAD", "알 수 없는 SQL 오류"


def next_batch(candidates: list[dict], offset: int) -> list[dict]:
    # 캐시된 후보 중 다음 확장 배치를 반환
    end = min(len(candidates), offset + EXPAND_STEP)
    return candidates[offset:end]


# ─────────────────────────────────────────
# Node 1: parse_request
# ─────────────────────────────────────────

async def parse_request(state: TextToSQLState) -> dict:
    # [역할] 사용자 자연어 질문을 구조화된 JSON으로 변환
    # [입력] user_question
    # [출력] parsed_request, is_request_valid/request_error(파싱 실패 시)
    messages = [
        SystemMessage(content=PARSE_REQUEST_SYSTEM),
        HumanMessage(content=PARSE_REQUEST_USER.format(
            current_time=get_current_time(),
            user_question=state["user_question"],
        )),
    ]
    response = await llm_fast.ainvoke(messages)
    parsed, error = parse_json_from_llm(response.content)
    if error:
        return {
            "parsed_request": {},
            "is_request_valid": False,
            "request_error": error,
        }
    return {"parsed_request": parsed}


# ─────────────────────────────────────────
# Node 2: validate_request
# ─────────────────────────────────────────

async def validate_request(state: TextToSQLState) -> dict:
    # [역할] 파싱 결과의 기본 유효성/시간 범위를 검증
    # [입력] parsed_request
    # [출력] is_request_valid, request_error (실패 시 결과 상태 error로 전환)
    if state.get("is_request_valid") is False:
        return {
            "is_request_valid": False,
            "request_error": state.get("request_error", "알 수 없는 오류"),
            "validation_reason": state.get("request_error", "알 수 없는 오류"),
            "result_status": "error",
        }

    parsed = state.get("parsed_request", {})
    if not parsed.get("intent"):
        return {
            "is_request_valid": False,
            "request_error": "intent 필드가 없습니다",
            "validation_reason": "intent 필드가 없습니다",
            "result_status": "error",
        }

    time_range = parsed.get("time_range", {})
    if time_range:
        start = time_range.get("start")
        end = time_range.get("end")
        if start and end:
            try:
                start_dt = datetime.fromisoformat(start.replace("Z", "+00:00"))
                end_dt = datetime.fromisoformat(end.replace("Z", "+00:00"))
                now = get_now().replace(tzinfo=end_dt.tzinfo)
                if start_dt > end_dt:
                    return {
                        "is_request_valid": False,
                        "request_error": "시작 시간이 종료 시간보다 늦습니다",
                        "validation_reason": "시작 시간이 종료 시간보다 늦습니다",
                        "result_status": "error",
                    }
                if end_dt > now + timedelta(hours=1):
                    return {
                        "is_request_valid": False,
                        "request_error": "미래 데이터는 조회할 수 없습니다",
                        "validation_reason": "미래 데이터는 조회할 수 없습니다",
                        "result_status": "error",
                    }
            except ValueError as e:
                return {
                    "is_request_valid": False,
                    "request_error": f"시간 형식 오류: {e}",
                    "validation_reason": f"시간 형식 오류: {e}",
                    "result_status": "error",
                }

    return {"is_request_valid": True, "request_error": ""}


# ─────────────────────────────────────────
# Node 3: retrieve_tables
# ─────────────────────────────────────────

async def retrieve_tables(state: TextToSQLState) -> dict:
    # [역할] 벡터 검색으로 후보 테이블 확보 + 캐시
    # [입력] user_question
    # [출력] table_candidates, candidate_offset
    user_question = state["user_question"]
    candidates = await search_related_tables(user_question, top_k=RETRIEVE_K)

    if not candidates:
        return {
            "table_candidates": [],
            "selected_tables": [],
            "table_context": "",
            "request_error": "관련 테이블을 찾지 못했습니다",
        }

    return {
        "table_candidates": candidates,
        "candidate_offset": TOP_K,
    }


# ─────────────────────────────────────────
# Node 4: select_tables
# ─────────────────────────────────────────

async def select_tables(state: TextToSQLState) -> dict:
    # [역할] 후보 테이블 중 상위 TOP_K 선택 + 스키마 컨텍스트 구성
    # [입력] table_candidates, parsed_request
    # [출력] selected_tables, table_context
    parsed = state.get("parsed_request", {})
    candidates = state.get("table_candidates", []) or []

    if not candidates:
        return {
            "selected_tables": [],
            "table_context": "",
            "request_error": "후보 테이블이 없습니다",
        }

    if settings.enable_table_rerank:
        candidates_str = ""
        for i, c in enumerate(candidates, 1):
            col_names = ", ".join([col.get("name", "") for col in c.get("columns", [])[:5]])
            join_keys = ", ".join(c.get("join_keys", []) or [])
            primary_time_col = c.get("primary_time_col", "")
            candidates_str += (
                f"{i}. {c['table_name']}\n"
                f"   설명: {c.get('description', '')}\n"
                f"   시간 컬럼: {primary_time_col}\n"
                f"   조인 키: {join_keys}\n"
                f"   주요 컬럼: {col_names}\n"
                f"   유사도: {c.get('score', '')}\n\n"
            )

        messages = [
            SystemMessage(content=RERANK_TABLE_SYSTEM),
            HumanMessage(content=RERANK_TABLE_USER.format(
                intent=parsed.get("intent", ""),
                metric=parsed.get("metric", "N/A"),
                condition=parsed.get("condition", "N/A"),
                candidates=candidates_str,
            )),
        ]
        response = await llm_fast.ainvoke(messages)
        selected_idx_str = response.content.strip()
        selected_indices = [int(n) for n in re.findall(r"\d+", selected_idx_str)]
    else:
        selected_indices = list(range(1, min(TOP_K, len(candidates)) + 1))

    if not selected_indices:
        return {
            "selected_tables": [],
            "table_context": "",
            "request_error": "적합한 테이블을 찾지 못했습니다",
        }

    selected = []
    selected_names = []
    for idx in selected_indices:
        if 1 <= idx <= len(candidates):
            selected.append(candidates[idx - 1])
            selected_names.append(candidates[idx - 1]["table_name"])

    table_context = build_table_context(selected)
    return {
        "selected_tables": selected_names,
        "table_context": table_context,
    }


# ─────────────────────────────────────────
# Node 5: generate_sql
# ─────────────────────────────────────────

async def generate_sql(state: TextToSQLState) -> dict:
    # [역할] 선택된 테이블 컨텍스트로 SQL 생성
    # [입력] parsed_request, table_context, feedback_to_sql
    # [출력] generated_sql
    parsed = state.get("parsed_request", {})
    time_range = parsed.get("time_range", {})
    table_name_str = ", ".join(state.get("selected_tables", []) or []) or "N/A"

    validation_reason = state.get("feedback_to_sql") or state.get("validation_reason") or "없음"

    messages = [
        SystemMessage(content=GENERATE_SQL_SYSTEM),
        HumanMessage(content=GENERATE_SQL_USER.format(
            intent=parsed.get("intent", ""),
            time_start=time_range.get("start", "N/A"),
            time_end=time_range.get("end", "N/A"),
            metric=parsed.get("metric", "N/A"),
            condition=parsed.get("condition", "N/A"),
            table_name=table_name_str,
            columns=state.get("table_context", ""),
            validation_reason=validation_reason,
        )),
    ]

    response = await llm_smart.ainvoke(messages)
    return {"generated_sql": response.content.strip(), "sql_guard_error": ""}


# ─────────────────────────────────────────
# Node 6: guard_sql
# ─────────────────────────────────────────

async def guard_sql(state: TextToSQLState) -> dict:
    # [역할] 생성된 SQL의 안전 규칙 검사/보정
    # [입력] generated_sql
    # [출력] generated_sql(정규화), sql_guard_error(실패 시)
    try:
        sql = normalize_sql(state.get("generated_sql", ""))
        return {"generated_sql": sql, "sql_guard_error": ""}
    except Exception as e:
        return {
            "sql_guard_error": str(e),
            "sql_retry_count": state.get("sql_retry_count", 0) + 1,
            "total_loops": state.get("total_loops", 0) + 1,
        }


# ─────────────────────────────────────────
# Node 7: execute_sql
# ─────────────────────────────────────────

async def execute_sql(state: TextToSQLState) -> dict:
    # [역할] MCP 도구를 통해 SQL 실행
    # [입력] generated_sql
    # [출력] sql_result, sql_error
    sql = state.get("generated_sql", "")
    try:
        async with postgres_client() as client:
            result = await client.call_tool("execute_sql", {"query": sql})
            sql_result = json.loads(result)
        return {"sql_result": sql_result, "sql_error": ""}
    except Exception as e:
        return {"sql_result": [], "sql_error": str(e)}


# ─────────────────────────────────────────
# Node 8: normalize_result
# ─────────────────────────────────────────

async def normalize_result(state: TextToSQLState) -> dict:
    # [역할] 실행 결과/에러를 표준 상태로 정규화
    # [입력] sql_result, sql_error
    # [출력] result_status, verdict, validation_reason, feedback_to_sql
    sql_error = state.get("sql_error")
    if sql_error:
        verdict, reason = classify_sql_error(sql_error)
        return {
            "result_status": "error",
            "verdict": verdict,
            "validation_reason": reason,
            "feedback_to_sql": reason,
            "total_loops": state.get("total_loops", 0) + 1,
        }

    if not state.get("sql_result"):
        return {
            "result_status": "empty",
        }

    return {"result_status": "ok"}


# ─────────────────────────────────────────
# Node 9: validate_llm
# ─────────────────────────────────────────

async def validate_llm(state: TextToSQLState) -> dict:
    # [역할] LLM으로 결과-의도 정합성 판단
    # [입력] user_question, generated_sql, sql_result, table_context, time_range
    # [출력] verdict, feedback_to_sql, validation_reason
    # SQL 에러가 있으면 스킵 (이미 verdict 결정됨)
    if state.get("sql_error"):
        return {}

    parsed = state.get("parsed_request", {})
    time_range = parsed.get("time_range", {})

    messages = [
        SystemMessage(content=VALIDATE_RESULT_SYSTEM),
        HumanMessage(content=VALIDATE_RESULT_USER.format(
            user_question=state.get("user_question", ""),
            time_start=time_range.get("start", "N/A"),
            time_end=time_range.get("end", "N/A"),
            generated_sql=state.get("generated_sql", ""),
            sql_result=json.dumps(state.get("sql_result", [])[:10], ensure_ascii=False, indent=2),
            table_context=state.get("table_context", ""),
        )),
    ]

    response = await llm_smart.ainvoke(messages)
    parsed_json, error = parse_json_from_llm(response.content)

    if error or not parsed_json:
        # 파싱 실패 시 단순 폴백
        verdict = "OK" if state.get("sql_result") else "DATA_MISSING"
        return {
            "verdict": verdict,
            "validation_reason": "검증 파싱 실패",
        }

    verdict = parsed_json.get("verdict", "AMBIGUOUS")
    feedback = parsed_json.get("feedback_to_sql", "")

    # 스키마가 충분한데 결과가 빠진 경우 DATA_MISSING을 SQL_BAD로 보정
    if verdict == "DATA_MISSING":
        question = state.get("user_question", "").lower()
        context = state.get("table_context", "").lower()
        needs_ram = "램" in question or "ram" in question
        needs_cpu = "cpu" in question or "시피유" in question
        needs_docker = "도커" in question or "docker" in question or "컨테이너" in question

        has_memory = "metrics_memory" in context
        has_cpu = "metrics_cpu" in context
        has_docker = "docker_metrics" in context

        if (needs_ram and has_memory and needs_cpu and has_cpu) or (needs_docker and has_docker):
            verdict = "SQL_BAD"
            feedback = "필요한 테이블은 제공되었으나 SQL이 요구 지표를 포함하지 않습니다. 누락된 지표를 포함해 다시 작성하세요."

    if verdict != "OK":
        return {
            "verdict": verdict,
            "feedback_to_sql": feedback,
            "validation_reason": feedback or "검증 실패",
            "validation_retry_count": state.get("validation_retry_count", 0) + 1,
            "total_loops": state.get("total_loops", 0) + 1,
        }

    return {
        "verdict": "OK",
        "validation_reason": "",
    }


# ─────────────────────────────────────────
# Node 10: expand_tables
# ─────────────────────────────────────────

async def expand_tables(state: TextToSQLState) -> dict:
    # [역할] TABLE_MISSING 시 캐시된 후보에서 추가 테이블 확장
    # [입력] table_candidates, candidate_offset, selected_tables
    # [출력] selected_tables, table_context, candidate_offset
    candidates = state.get("table_candidates", []) or []
    offset = state.get("candidate_offset", TOP_K)

    batch = next_batch(candidates, offset)
    if not batch:
        return {
            "verdict": "DATA_MISSING",
            "validation_reason": "추가 후보 테이블이 없습니다",
        }

    selected_names = list(state.get("selected_tables", []) or [])
    selected_tables = [t for t in candidates if t["table_name"] in selected_names]
    selected_tables.extend(batch)

    return {
        "selected_tables": [t["table_name"] for t in selected_tables],
        "table_context": build_table_context(selected_tables),
        "candidate_offset": offset + len(batch),
        "table_expand_count": state.get("table_expand_count", 0) + 1,
        "total_loops": state.get("total_loops", 0) + 1,
    }


# ─────────────────────────────────────────
# Node 11: generate_report
# ─────────────────────────────────────────

async def generate_report(state: TextToSQLState) -> dict:
    # [역할] 최종 사용자 보고서 생성
    # [입력] user_question, result_status, sql_result, validation_reason
    # [출력] report, suggested_actions
    messages = [
        SystemMessage(content=GENERATE_REPORT_SYSTEM),
        HumanMessage(content=GENERATE_REPORT_USER.format(
            user_question=state.get("user_question", ""),
            result_status=state.get("result_status", "unknown"),
            sql_result=json.dumps(state.get("sql_result", [])[:20], ensure_ascii=False, indent=2),
            validation_reason=state.get("validation_reason", ""),
        )),
    ]
    response = await llm_fast.ainvoke(messages)
    report = response.content.strip()

    suggested_actions = []
    for line in report.splitlines():
        if re.match(r"^\d+\.\s", line.strip()):
            suggested_actions.append(line.strip())

    return {
        "report": report,
        "suggested_actions": suggested_actions,
    }
