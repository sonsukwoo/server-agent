"""Text-to-SQL 에이전트 노드 및 미들웨어 통합 모듈."""
import json
import re
import logging

from langchain_openai import ChatOpenAI
from langchain_core.messages import SystemMessage, HumanMessage, AIMessage

from config.settings import settings
from src.agents.mcp_clients.connector import postgres_client, qdrant_search_client
from src.agents.text_to_sql.middleware.parsed_request_guard import ParsedRequestGuard
from src.agents.text_to_sql.middleware.sql_safety_guard import SqlOutputGuard
from .table_expand_too import expand_tables_tool

from .state import TextToSQLState
from .prompts import (
    PARSE_REQUEST_SYSTEM, PARSE_REQUEST_USER,
    RERANK_TABLE_SYSTEM, RERANK_TABLE_USER,
    GENERATE_SQL_SYSTEM, GENERATE_SQL_USER,
    VALIDATE_RESULT_SYSTEM, VALIDATE_RESULT_USER,
    GENERATE_REPORT_SYSTEM, GENERATE_REPORT_USER,
    CLASSIFY_INTENT_SYSTEM, CLASSIFY_INTENT_USER,
    GENERAL_CHAT_SYSTEM, GENERAL_CHAT_USER,
    CLARIFICATION_CHECK_SYSTEM, CLARIFICATION_CHECK_USER,
)
from .common.constants import (
    RETRIEVE_K, TOP_K
)
from .common.utils import (
    get_current_time, parse_json_from_llm, normalize_sql,
    build_table_context, rebuild_context_from_candidates,
    classify_sql_error, next_batch, apply_elbow_cut
)
from langchain_core.messages import trim_messages


logger = logging.getLogger("TEXT_TO_SQL")

llm_fast = ChatOpenAI(model=settings.model_fast, temperature=0)
llm_smart = ChatOpenAI(model=settings.model_smart, temperature=0)

# JSON Mode 강제 바인딩 (전역 생성)
structured_llm_fast = llm_fast.bind(response_format={"type": "json_object"})

# ─────────────────────────────────────────
# 대화 히스토리에서 이전 SQL 추출
# ─────────────────────────────────────────

def _extract_previous_sql_from_messages(state: TextToSQLState) -> str:
    """state['messages']에서 가장 최근 AI 응답 안의 SQL 블록을 추출.

    SSOT 원칙: 'generated_sql'은 현재 턴의 임시 상태일 수 있으므로 참조하지 않고,
    오직 확정된 대화 히스토리(messages)에서만 이전 쿼리를 찾습니다.
    """
    import re
    # messages 역순 탐색 (가장 최근 대화부터)
    for msg in reversed(state.get("messages", [])):
        # AI 메시지이면서 툴 호출이 아닌 경우 (최종 답변)
        if isinstance(msg, AIMessage) and not msg.tool_calls:
            # Markdown Code Block 추출
            sql_match = re.search(r'```sql\n(.*?)\n```', msg.content, re.DOTALL)
            if sql_match:
                return sql_match.group(1).strip()
            
            # (옵션) 코드 블록이 없더라도 SELECT 문이 포함되어 있다면 추출 고려
            # 하지만 안전을 위해 코드 블록만 인정
            
    return ""


# ─────────────────────────────────────────
# Helper functions (used by specific nodes)
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
            
    # 점수 내림차순 정렬
    scored.sort(key=lambda x: x["score"], reverse=True)
    
    # Elbow Cut 적용
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


def _extract_tables_from_sql(sql: str) -> list[str]:
    """SQL 쿼리에서 FROM/JOIN 테이블 이름 추출."""
    tables = []
    # FROM/JOIN 뒤의 테이블 이름 추출 (schema.table 형식 지원)
    pattern = r'(?:FROM|JOIN)\s+([a-zA-Z_][a-zA-Z0-9_]*(?:\.[a-zA-Z_][a-zA-Z0-9_]*)?)'
    matches = re.findall(pattern, sql, re.IGNORECASE)
    for match in matches:
        if match.upper() not in ('SELECT', 'WHERE', 'AND', 'OR', 'ON', 'AS'):
            tables.append(match)
    return list(set(tables))


def _extract_time_range_from_sql(sql: str) -> tuple[str, str]:
    """SQL에서 ts BETWEEN A AND B 구문을 찾아 A, B 시간값 반환."""
    # 예: ts BETWEEN '2023-01-01T00:00:00' AND '2023-01-02T00:00:00'
    pattern = r"ts\s+BETWEEN\s+'([^']+)'\s+AND\s+'([^']+)'"
    match = re.search(pattern, sql, re.IGNORECASE)
    if match:
        return match.group(1), match.group(2)
    return "", ""


def _build_sql_prompt_inputs(state: TextToSQLState) -> dict:
    """SQL 생성 프롬프트에 주입할 변수 딕셔너리 구성."""
    failed = state.get("failed_queries", []) or []
    time_range = state.get("parsed_request", {}).get("time_range", {})

    # 이전 SQL 쿼리 추출 (대화 히스토리 또는 이전 턴의 generated_sql)
    previous_sql = _extract_previous_sql_from_messages(state)

    # Time Mode 결정
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


def _append_failed_query(failed_queries: list[str], sql: str) -> list[str]:
    """실패한 쿼리 히스토리 업데이트 (최근 3개 유지)."""
    if not sql:
        return failed_queries
    failed_queries.append(sql)
    return failed_queries[-3:]


def _build_validation_messages(state: TextToSQLState, current_sql: str) -> list:
    """결과 검증용 LLM 메시지 리스트 생성."""
    time_range = state.get("parsed_request", {}).get("time_range", {})
    
    # Time Mode 결정
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
    new_context = rebuild_context_from_candidates(
        state.get("table_candidates", []) or [], filtered
    )
    logger.info("TEXT_TO_SQL:validate_llm unnecessary tables found, retrying with filtered context")
    return {
        "selected_tables": filtered,
        "table_context": new_context,
        "verdict": "RETRY_SQL",
        "validation_reason": "불필요한 테이블 제거 후 재시도",
        "failed_queries": failed_queries,
        "total_loops": state.get("total_loops", 0) + 1,
    }


def _format_failed_feedback(feedback: str, hint: str) -> str:
    """검증 실패 피드백 및 힌트 포맷팅."""
    full_feedback = f"### 이전 시도 실패 원인\n{feedback}\n"
    if hint:
        full_feedback += f"\n### 올바른 쿼리 예시 및 힌트\n{hint}\n"
    return full_feedback


# ─────────────────────────────────────────
# Node 1: parse_request
# ─────────────────────────────────────────

async def parse_request(state: TextToSQLState) -> dict:
    """사용자 질의 파싱: 자연어 -> JSON 구조화."""
    logger.info("TEXT_TO_SQL:parse_request start")
    messages = [
        SystemMessage(content=PARSE_REQUEST_SYSTEM),
        HumanMessage(content=PARSE_REQUEST_USER.format(
            current_time=get_current_time(),
            user_question=state["user_question"],
        )),
    ]
    
    try:
        response = await structured_llm_fast.ainvoke(messages)
        parsed = json.loads(response.content)
        error = None
    except json.JSONDecodeError as e:
        logger.error("TEXT_TO_SQL:parse_request json_decode_error=%s", e)
        return {
            "parsed_request": {},
            "is_request_valid": False,
            "request_error": f"JSON 파싱 실패: {e}",
        }
    except Exception as e:
        logger.error("TEXT_TO_SQL:parse_request llm_error=%s", e)
        return {
            "parsed_request": {},
            "is_request_valid": False,
            "request_error": f"LLM 호출 실패: {str(e)}",
        }

    if not isinstance(parsed, dict):
        logger.error("TEXT_TO_SQL:parse_request invalid_type=%s", type(parsed))
        return {
            "parsed_request": {},
            "is_request_valid": False,
            "request_error": "LLM 응답이 JSON 객체(dict) 형식이 아닙니다",
        }

    # 기본값 보정 및 이전 맥락 병합 (Context Merging)
    old_parsed = state.get("parsed_request", {}) or {}

    # 1. Intent는 이번 턴의 판단을 우선
    if not parsed.get("intent"):
        parsed["intent"] = "unknown"

    # 2. Time Range 상속 로직 개선
    new_time = parsed.get("time_range", {})
    is_all_time = new_time.get("all_time") if new_time else False

    if is_all_time:
        # 사용자가 명시적으로 "전체 기간"을 요청한 경우 -> 상속 금지
        logger.info("TEXT_TO_SQL:parse_request all_time=True detected. Skipping inheritance.")
    elif not new_time or (not new_time.get("start") and not new_time.get("end")):
        # 새 요청에 시간 범위가 없고 all_time도 아닌 경우 -> 이전 맥락 상속
        if old_parsed.get("time_range"):
            parsed["time_range"] = old_parsed.get("time_range")
            logger.info("TEXT_TO_SQL:parse_request inherited time_range from history")

    # 3. Metric/Condition 상속 (새 요청이 구체적이지 않으면 이전 맥락 유지)
    # "상위 5개 보여줘" 같은 경우 metric이 비어있을 수 있음 -> 이전 metric 상속
    if not parsed.get("metric"):
        if old_parsed.get("metric"):
            parsed["metric"] = old_parsed.get("metric")
            logger.info("TEXT_TO_SQL:parse_request inherited metric from history")
        # 4. 편의 기능: 그래도 없으면 CPU를 기본값으로 (사용자 요청 반영: 융통성)
        elif "사용률" in state["user_question"] or "상위" in state["user_question"]:
             # 간단하게 처리 (정교한 건 프롬프트 튜닝 필요하지만 코드 레벨에서 힌트 제공)
             pass

    return {"parsed_request": parsed}


# ─────────────────────────────────────────
# Node 2: validate_request
# ─────────────────────────────────────────

async def validate_request(state: TextToSQLState) -> dict:
    """파싱된 질의 유효성 검증 및 보정 (미들웨어 위임)."""
    logger.info("TEXT_TO_SQL:validate_request start")

    if state.get("is_request_valid") is False:
        err = state.get("request_error") or "알 수 없는 오류"
        return {
            "is_request_valid": False,
            "request_error": err,
            "validation_reason": err,
            "result_status": "error",
        }

    parsed = state.get("parsed_request", {})
    
    # 미들웨어 호출
    is_valid, error_reason, normalized_parsed, adjustment_info = ParsedRequestGuard.validate(parsed)
    
    if not is_valid:
        logger.info("TEXT_TO_SQL:validate_request failed: %s", error_reason)
        return {
            "is_request_valid": False,
            "request_error": error_reason,
            "validation_reason": error_reason,
            "result_status": "error",
            "last_tool_usage": f"검증 실패: {error_reason}"
        }
        
    log_msg = "질문 검증 완료"
    if adjustment_info:
        log_msg = f"질문 보정: {adjustment_info}"

    return {
        "parsed_request": normalized_parsed,
        "is_request_valid": True,
        "request_error": "",
        "last_tool_usage": log_msg
    }


# ─────────────────────────────────────────
# Node 3: retrieve_tables
# ─────────────────────────────────────────

async def retrieve_tables(state: TextToSQLState) -> dict:
    """테이블 검색: 후속 질문 확인 또는 Qdrant 벡터 검색."""
    user_question = state["user_question"]
    parsed_request = state.get("parsed_request", {})
    
    # 후속 질문 처리: 이전 쿼리의 테이블 재사용
    if parsed_request.get("is_followup"):
        previous_sql = ""
        previous_sql = _extract_previous_sql_from_messages(state)
        
        if previous_sql:
            tables = _extract_tables_from_sql(previous_sql)
            if tables:
                logger.info("TEXT_TO_SQL:retrieve_tables skipped (followup)")
                candidates = [{"table_name": t, "score": 1.0} for t in tables]
                return {
                    "table_candidates": candidates,
                    "selected_tables": tables,
                    "candidate_offset": len(tables),
                    "last_tool_usage": f"후속 질문: 이전 쿼리에 사용된 테이블 재사용 ({', '.join(tables)})"
                }
    
    # Qdrant 검색
    candidates = []
    try:
        async with qdrant_search_client() as client:
            result_json = await client.call_tool("search_tables", {
                "query": user_question,
                "top_k": RETRIEVE_K
            })
            if result_json:
                try:
                    candidates = json.loads(result_json)
                    logger.info("Qdrant MCP search_tables OK")
                except json.JSONDecodeError:
                    candidates = []
                except Exception:
                    candidates = []
    except Exception as e:
        logger.error(f"Qdrant MCP Tool Call Error: {e}")
        candidates = []

    # TEMP: view 제외 로직
    filtered = []
    for c in candidates:
        name = c.get("table_name", "")
        base = name.split(".")[-1]
        if base.startswith("v_"):
            continue
        filtered.append(c)

    if not filtered:
        return {
            "table_candidates": [],
            "selected_tables": [],
            "table_context": "",
            "request_error": "관련 테이블을 찾지 못했습니다",
            "last_tool_usage": "검색 결과: 관련 테이블 없음"
        }

    return {
        "table_candidates": filtered,
        "candidate_offset": 0,
        "last_tool_usage": f"벡터 검색 완료: {len(filtered)}개의 후보 테이블 확보"
    }


# ─────────────────────────────────────────
# Node 4: select_tables
# ─────────────────────────────────────────

async def select_tables(state: TextToSQLState) -> dict:
    """후보 테이블 중 최적의 테이블 선택 (LLM Rerank)."""
    parsed = state.get("parsed_request", {})
    candidates = state.get("table_candidates", []) or []

    if not candidates:
        logger.warning("TEXT_TO_SQL:select_tables no candidates found")
        return {
            "selected_tables": [],
            "table_context": "",
            "request_error": "후보 테이블이 없습니다",
        }

    # 1. Rerank 준비 및 실행
    candidates_str = _format_candidates_for_rerank(candidates)
    response_json = await _call_rerank_llm(parsed, candidates_str)
    
    selected_indices = None
    if response_json:
        selected_indices = _parse_rerank_response(response_json, len(candidates))
        if selected_indices:
            logger.info("TEXT_TO_SQL:select_tables rerank_success")
    
    # 2. 폴백: 실패 시 상위 N개 선택
    if not selected_indices:
        fallback_count = min(TOP_K, len(candidates))
        selected_indices = list(range(1, fallback_count + 1))
        logger.info("TEXT_TO_SQL:select_tables fallback applied")

    # 3. 최종 선택 및 컨텍스트 생성
    unique_indices = list(dict.fromkeys(selected_indices))
    selected_names = _select_candidates(candidates, unique_indices)
    selected_objects = [candidates[i-1] for i in unique_indices if 1 <= i <= len(candidates)]
    table_context = build_table_context(selected_objects)

    logger.info("TEXT_TO_SQL:select_tables final_selected=%s", selected_names)

    return {
        "selected_tables": selected_names,
        "table_context": table_context,
        "candidate_offset": len(selected_objects),
        "last_tool_usage": f"연관성 높은 테이블 선택: {', '.join(selected_names)}"
    }


# ─────────────────────────────────────────
# Node 5: generate_sql
# ─────────────────────────────────────────

async def generate_sql(state: TextToSQLState) -> dict:
    """SQL 쿼리 생성 (테이블 부족 시 Tool 호출로 확장)."""
    inputs = _build_sql_prompt_inputs(state)
    logger.info("TEXT_TO_SQL:generate_sql start")

    messages = _build_generate_sql_messages(inputs)

    loop_count = 0
    max_loops = 2
    
    current_state = state.copy()
    last_tool_usage_log = None

    while loop_count < max_loops:
        loop_count += 1
        response = await llm_smart.ainvoke(messages)
        parsed, error = parse_json_from_llm(response.content)

        # JSON 파싱 실패 -> Raw SQL Fallback
        if error or not parsed:
            logger.warning("TEXT_TO_SQL:generate_sql JSON parse failed, assuming raw SQL")
            raw_sql = response.content.strip()
            try:
                normalized = normalize_sql(raw_sql)
                return {"generated_sql": normalized, "sql_guard_error": ""}
            except ValueError:
                return {"generated_sql": raw_sql, "sql_guard_error": ""}

        needs_tables = parsed.get("needs_more_tables", False)
        sql_text = parsed.get("sql", "")

        # A. 테이블 확장 요청 처리
        if needs_tables:
            if current_state.get("table_expand_failed"):
                # 이미 실패했으면 무시하고 진행
                if sql_text: break
                break # 빈 SQL이면 다음 단계에서 에러 처리

            logger.info("TEXT_TO_SQL:generate_sql Triggering tool: expand_tables")
            
            candidates = current_state.get("table_candidates", []) or []
            offset = current_state.get("candidate_offset", TOP_K)
            selected = list(current_state.get("selected_tables", []) or [])
            
            new_selected, new_context, new_offset = expand_tables_tool(selected, candidates, offset)
            
            if new_offset > offset:
                # 확장 성공
                added_count = new_offset - offset
                tool_msg = f"테이블 확장 툴 실행 (추가됨: {new_selected[-added_count:]})"
                logger.info(tool_msg)
                last_tool_usage_log = tool_msg
                
                current_state["selected_tables"] = new_selected
                current_state["table_context"] = new_context
                current_state["candidate_offset"] = new_offset
                current_state["table_expand_attempted"] = True
                
                # 입력 재생성 후 루프 재시작
                inputs["table_name"] = ", ".join(new_selected)
                inputs["columns"] = new_context
                inputs["_meta_table_count"] = len(new_selected)
                messages = _build_generate_sql_messages(inputs)
                continue
            else:
                # 확장 실패 (후보 없음)
                fail_msg = "테이블 확장 시도했으나 추가 후보 없음"
                last_tool_usage_log = fail_msg

                current_state["table_expand_failed"] = True
                current_state["table_expand_attempted"] = True
                
                inputs["validation_reason"] += f"\n(시스템 알림: {fail_msg}. 현재 정보로 진행하세요.)"
                messages = _build_generate_sql_messages(inputs)
                continue

        # B. SQL 생성 성공
        break
    
    # 결과 반환 구성
    if not sql_text and loop_count >= max_loops:
         sql_text = ""
         state_error = "Generating SQL Loop Limit exceeded"
    else:
         state_error = ""

    result_update = {
        "generated_sql": sql_text, 
        "sql_guard_error": state_error,
        "last_tool_usage": "SQL 쿼리 생성 완료" if sql_text else "SQL 생성 실패"
    }
    
    # 변경된 상태 반영
    keys_to_update = [
        "table_expand_attempted", "table_expand_failed", 
        "selected_tables", "table_context", "candidate_offset"
    ]
    for k in keys_to_update:
        if k in current_state:
            result_update[k] = current_state[k]

    if last_tool_usage_log:
        result_update["last_tool_usage"] = last_tool_usage_log

    return result_update


# ─────────────────────────────────────────
# Node 6: guard_sql
# ─────────────────────────────────────────

# 전역 싱글톤 가드 인스턴스
sql_guard = SqlOutputGuard()

async def guard_sql(state: TextToSQLState) -> dict:
    """생성된 SQL의 안전성 검사 (Syntax, 금지어 등)."""
    current_sql = state.get("generated_sql", "")
    
    if not current_sql:
        logger.warning("TEXT_TO_SQL:guard_sql blocked: SQL is empty")
        return {
            "generated_sql": "",
            "sql_guard_error": "SQL이 비어있습니다",
            "sql_retry_count": state.get("sql_retry_count", 0) + 1,
            "total_loops": state.get("total_loops", 0) + 1,
        }

    is_valid, result_or_error = sql_guard.validate_sql(current_sql)
    
    if not is_valid:
        logger.warning(f"TEXT_TO_SQL:guard_sql blocked: {result_or_error}")
        return {
            "generated_sql": current_sql,
            "sql_guard_error": result_or_error,
            "sql_retry_count": state.get("sql_retry_count", 0) + 1,
            "total_loops": state.get("total_loops", 0) + 1,
        }

    logger.info("TEXT_TO_SQL:guard_sql passed")
    return {
        "generated_sql": result_or_error, # 정규화된 SQL
        "sql_guard_error": ""
    }


# ─────────────────────────────────────────
# Node 7: execute_sql
# ─────────────────────────────────────────

async def execute_sql(state: TextToSQLState) -> dict:
    """SQL 실행 및 결과 반환 (PostgreSQL MCP)."""
    sql = state.get("generated_sql")
    logger.info(f"TEXT_TO_SQL:execute_sql executing: {sql[:50]}...")

    try:
        async with postgres_client() as client:
            result_json = await client.call_tool("execute_sql", {"query": sql})
            
            # 결과가 문자열(JSON)로 오므로 파싱
            if isinstance(result_json, str):
                try:
                    result_data = json.loads(result_json)
                except json.JSONDecodeError:
                    # 문자열 그대로인 경우 (에러 메시지 등)
                    result_data = result_json
            else:
                result_data = result_json

            # MCP 에러 응답 처리 (dict 형태일 때)
            if isinstance(result_data, dict) and result_data.get("is_error"):
                return {
                    "sql_result": [],
                    "sql_error": result_data.get("message", "Unknown DB Error"),
                }
            
            # 리스트가 아니면 빈 리스트로
            if not isinstance(result_data, list):
                result_data = []

            return {
                "sql_result": result_data,
                "sql_error": None,
                "last_tool_usage": f"SQL 실행 완료 (결과 {len(result_data)}행)"
            }

    except Exception as e:
        logger.error(f"TEXT_TO_SQL:execute_sql failed: {e}")
        return {
            "sql_result": [],
            "sql_error": str(e),
            "last_tool_usage": f"SQL 실행 에러: {str(e)}"
        }


# ─────────────────────────────────────────
# Node 8: normalize_result
# ─────────────────────────────────────────

async def normalize_result(state: TextToSQLState) -> dict:
    """실행 결과 정규화 및 에러 분류."""
    sql_error = state.get("sql_error")
    
    if sql_error:
        # 에러 분류
        error_type = classify_sql_error(str(sql_error))
        
        # 재시도 카운트 증가
        retry_count = state.get("sql_retry_count", 0) + 1
        total_loops = state.get("total_loops", 0) + 1
        
        # 실패한 SQL 기록
        failed_list = state.get("failed_queries", []) or []
        current_sql = state.get("generated_sql", "")
        failed_list = _append_failed_query(failed_list, current_sql)

        failed_msg = f"SQL 실행 실패 ({error_type}): {sql_error}"
        logger.warning(f"TEXT_TO_SQL:normalize_result {failed_msg}")

        return {
            "sql_retry_count": retry_count,
            "total_loops": total_loops,
            "verdict": error_type, # 에러 타입으로 verdict 설정 (ROUTER에서 분기 처리)
            "validation_reason": f"SQL Error: {sql_error}",
            "failed_queries": failed_list,
            "last_tool_usage": failed_msg
        }

    # 성공 시
    return {
        "verdict": "OK", # 일단 OK, validate_llm에서 최종 판정
    }


# ─────────────────────────────────────────
# Node 9: validate_llm
# ─────────────────────────────────────────

async def validate_llm(state: TextToSQLState) -> dict:
    """실행 결과의 논리적 정확성 검증 (LLM)."""
    # 이미 에러 상태라면 PASS (이미 normalize에서 verdict 설정됨)
    if state.get("sql_error"):
        return {}

    current_sql = state.get("generated_sql", "")
    messages = _build_validation_messages(state, current_sql)
    
    response = await llm_smart.ainvoke(messages)
    parsed, error = parse_json_from_llm(response.content)

    if error or not parsed:
        logger.warning("TEXT_TO_SQL:validate_llm JSON parse failed")
        # 파싱 실패 -> 안전하게 OK 처리하거나 실패 처리
        return {"verdict": "OK", "validation_reason": "검증 응답 파싱 실패, 결과 수용"}

    verdict = parsed.get("verdict", "OK")
    # 호환성: feedback_to_sql 우선, 없으면 reason fallback
    reason = parsed.get("feedback_to_sql") or parsed.get("reason", "")
    # 호환성: correction_hint 우선, 없으면 hint fallback
    hint = parsed.get("correction_hint") or parsed.get("hint", "")
    unnecessary_tables = parsed.get("unnecessary_tables", [])

    # 재시도 카운트 관리
    if verdict != "OK":
        state_update = {
            "verdict": verdict,
            "validation_reason": _format_failed_feedback(reason, hint),
            "sql_retry_count": state.get("sql_retry_count", 0) + 1,
            "total_loops": state.get("total_loops", 0) + 1,
        }
        
        # 불필요 테이블 처리 (재시도 플래그와 함께 리턴)
        table_retry = _handle_unnecessary_tables(state, unnecessary_tables, state.get("failed_queries", []))
        if table_retry:
            return table_retry
            
        # 실패 쿼리 기록
        failed = state.get("failed_queries", []) or []
        state_update["failed_queries"] = _append_failed_query(failed, current_sql)
        
        return state_update

    return {
        "verdict": "OK",
        "validation_reason": reason
    }


# ─────────────────────────────────────────
# Node 10: generate_report
# ─────────────────────────────────────────

async def generate_report(state: TextToSQLState) -> dict:
    """최종 응답 보고서 생성 (자연어 답변)."""
    logger.info("TEXT_TO_SQL:generate_report start")
    
    messages = [
        SystemMessage(content=GENERATE_REPORT_SYSTEM),
        HumanMessage(content=GENERATE_REPORT_USER.format(
            user_question=state["user_question"],
            result_status=state.get("verdict", "OK"),
            user_constraints=state.get("user_constraints", ""),
            generated_sql=state.get("generated_sql", "생성 실패"),
            sql_result=json.dumps(state.get("sql_result", []), ensure_ascii=False),
            validation_reason=state.get("validation_reason")
                or state.get("sql_error")
                or state.get("request_error")
                or "없음",
        ))
    ]

    response = await llm_fast.ainvoke(messages)
    answer = response.content
    
    status = "success"
    if state.get("sql_error") or state.get("request_error"):
        status = "error"
    elif state.get("verdict") != "OK":
        status = "fail"

    return {
        "report": answer,
        "result_status": status,
        # AI 응답을 대화 히스토리에 누적
        "messages": [AIMessage(content=answer)],
        # 표 출력을 위해 SQL 결과 명시적으로 반환 (상태 유지 보장)
        "sql_result": state.get("sql_result", []),
    }


# ─────────────────────────────────────────
# 대화 히스토리 트리밍 유틸리티
# ─────────────────────────────────────────

MAX_HISTORY_TOKENS = 4000
"""대화 히스토리 최대 토큰 수. 초과 시 오래된 메시지부터 제거."""


def _trim_conversation(state: TextToSQLState) -> list:
    """State의 messages를 토큰 기준으로 트리밍하여 반환.

    trim_messages가 LLM을 토큰 카운터로 사용하여
    MAX_HISTORY_TOKENS 이하로 자동으로 줄여줍니다.
    """
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


# ─────────────────────────────────────────
# Node 0: classify_intent (의도 분류 — 그래프 진입점)
# ─────────────────────────────────────────

async def classify_intent(state: TextToSQLState) -> dict:
    """사용자 질문을 SQL 조회 vs 일반 대화로 분류.

    진입점 노드이므로 사용자 메시지를 messages에 추가하여
    대화 히스토리를 누적합니다.
    """
    logger.info("TEXT_TO_SQL:classify_intent start")

    user_question = state.get("user_question", "")
    messages = [
        SystemMessage(content=CLASSIFY_INTENT_SYSTEM),
        HumanMessage(content=CLASSIFY_INTENT_USER.format(
            user_question=user_question
        )),
    ]

    try:
        response = await structured_llm_fast.ainvoke(messages)
        parsed = json.loads(response.content)
        intent = parsed.get("intent", "sql")
        reason = parsed.get("reason", "")
        
        # 라우팅 방어: 소문자 변환 및 화이트리스트 검증
        if isinstance(intent, str):
            intent = intent.lower().strip()
        
        if intent not in ["sql", "general"]:
            logger.warning("TEXT_TO_SQL:classify_intent invalid_intent=%s, fallback to sql", intent)
            intent = "sql"
            
        logger.info("TEXT_TO_SQL:classify_intent result=%s reason=%s", intent, reason)
    except Exception as e:
        logger.warning("TEXT_TO_SQL:classify_intent error=%s, defaulting to sql", e)
        intent = "sql"

    return {
        "classified_intent": intent,
        "last_tool_usage": f"질문 유형 판별: {intent}",
        # 사용자 메시지를 대화 히스토리에 누적
        "messages": [HumanMessage(content=user_question)],
    }


# ─────────────────────────────────────────
# Node 0-1: general_chat (일반 대화 응답)
# ─────────────────────────────────────────

async def general_chat(state: TextToSQLState) -> dict:
    """일반 대화(인사, 설명 등)에 대한 응답 생성."""
    logger.info("TEXT_TO_SQL:general_chat start")

    # 트리밍된 대화 히스토리 활용
    history = _trim_conversation(state)
    logger.info("TEXT_TO_SQL:general_chat history_len=%d", len(history))

    messages = [
        SystemMessage(content=GENERAL_CHAT_SYSTEM),
        *history,
    ]

    # 마지막 메시지가 현재 질문과 다르면 추가 (중복 방지)
    current_q = state.get("user_question", "")
    is_duplicate = False
    if history:
        last_msg = history[-1]
        # 내용이 같거나, 포맷팅된 내용과 유사하면 중복으로 간주
        if hasattr(last_msg, "content") and (last_msg.content == current_q or current_q in last_msg.content):
            is_duplicate = True

    if not is_duplicate:
        messages.append(HumanMessage(content=GENERAL_CHAT_USER.format(
            user_question=current_q,
        )))

    response = await llm_fast.ainvoke(messages)
    answer = response.content

    return {
        "report": answer,
        "result_status": "general",
        "suggested_actions": [],
        # AI 응답을 대화 히스토리에 누적
        "messages": [AIMessage(content=answer)],
    }


# ─────────────────────────────────────────
# Node 2-1: check_clarification (HITL 역질문 판단)
# ─────────────────────────────────────────

async def check_clarification(state: TextToSQLState) -> dict:
    """파싱된 요청의 핵심 정보 충분 여부를 LLM으로 판단.

    정보가 부족하면 needs_clarification=True를 반환하여
    그래프가 END로 분기 → API가 역질문 이벤트를 전송합니다.
    """
    logger.info("TEXT_TO_SQL:check_clarification start")

    parsed = state.get("parsed_request", {})
    messages = [
        SystemMessage(content=CLARIFICATION_CHECK_SYSTEM),
        HumanMessage(content=CLARIFICATION_CHECK_USER.format(
            intent=parsed.get("intent", ""),
            metric=parsed.get("metric", ""),
            condition=parsed.get("condition", ""),
            user_question=state.get("user_question", ""),
        )),
    ]

    try:
        response = await structured_llm_fast.ainvoke(messages)
        result = json.loads(response.content)
        needs = result.get("needs_clarification", False)
        question = result.get("question", "")
    except Exception as e:
        logger.warning("TEXT_TO_SQL:check_clarification error=%s, proceeding", e)
        needs = False
        question = ""

    if needs:
        logger.info("TEXT_TO_SQL:check_clarification needs_clarification=True")
        return {
            "needs_clarification": True,
            "clarification_question": question,
            "last_tool_usage": f"추가 정보 필요: {question}",
        }

    return {
        "needs_clarification": False,
        "clarification_question": "",
    }
