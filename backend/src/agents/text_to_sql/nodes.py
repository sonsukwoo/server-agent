"""Text-to-SQL 에이전트 노드/미들웨어 (통합형)"""
import json
import re

from langchain_openai import ChatOpenAI
from langchain_core.messages import SystemMessage, HumanMessage

import logging
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
)
from .common.constants import (
    RETRIEVE_K, TOP_K
)
from .common.utils import (
    get_current_time, parse_json_from_llm, normalize_sql,
    build_table_context, rebuild_context_from_candidates,
    classify_sql_error, next_batch, apply_elbow_cut
)

logger = logging.getLogger("TEXT_TO_SQL")

llm_fast = ChatOpenAI(model=settings.model_fast, temperature=0)
llm_smart = ChatOpenAI(model=settings.model_smart, temperature=0)

# JSON Mode 강제 바인딩 (전역 생성)
structured_llm_fast = llm_fast.bind(response_format={"type": "json_object"})

# ─────────────────────────────────────────
# Helper functions (used by specific nodes)
# ─────────────────────────────────────────

# Helper (Node 3: select_tables)
def _format_candidates_for_rerank(candidates: list, top_col_limit: int = 5) -> str:
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


# Helper (Node 3: select_tables)
async def _call_rerank_llm(parsed: dict, candidates_str: str) -> list | None:
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


# Helper (Node 3: select_tables)
def _parse_rerank_response(response_json: list, candidates_len: int) -> list[int] | None:
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


# Helper (Node 3: select_tables)
def _select_candidates(candidates: list, selected_indices: list[int]) -> list[str]:
    """선택된 인덱스들을 바탕으로 테이블명 리스트 반환 (1-based index -> 0-based 접근)"""
    selected_names = []
    for idx in selected_indices:
        real_idx = idx - 1
        if 0 <= real_idx < len(candidates):
            selected_names.append(candidates[real_idx]["table_name"])
    return selected_names


# Helper (Node 5: generate_sql)
def _build_sql_prompt_inputs(state: TextToSQLState) -> dict:
    failed = state.get("failed_queries", []) or []
    return {
        "intent": state.get("parsed_request", {}).get("intent", ""),
        "time_start": state.get("parsed_request", {}).get("time_range", {}).get("start", ""),
        "time_end": state.get("parsed_request", {}).get("time_range", {}).get("end", ""),
        "metric": state.get("parsed_request", {}).get("metric", ""),
        "condition": state.get("parsed_request", {}).get("condition", ""),
        "table_name": ", ".join(state.get("selected_tables", []) or []),
        "columns": state.get("table_context", ""),
        "failed_queries": "\n".join(failed[-3:]),
        "validation_reason": state.get("validation_reason", ""),
        "_meta_table_count": len(state.get("selected_tables", []) or []),
        "_meta_failed_count": len(failed),
    }


# Helper (Node 5: generate_sql)
def _build_generate_sql_messages(inputs: dict) -> list:
    return [
        SystemMessage(content=GENERATE_SQL_SYSTEM),
        HumanMessage(
            content=GENERATE_SQL_USER.format(
                intent=inputs["intent"],
                time_start=inputs["time_start"],
                time_end=inputs["time_end"],
                metric=inputs["metric"],
                condition=inputs["condition"],
                table_name=inputs["table_name"],
                columns=inputs["columns"],
                failed_queries=inputs["failed_queries"],
                validation_reason=inputs["validation_reason"],
            )
        ),
    ]


# Helper (Node 9: validate_llm)
def _append_failed_query(failed_queries: list[str], sql: str) -> list[str]:
    if not sql:
        return failed_queries
    failed_queries.append(sql)
    return failed_queries[-3:]


# Helper (Node 9: validate_llm)
def _build_validation_messages(state: TextToSQLState, current_sql: str) -> list:
    return [
        SystemMessage(content=VALIDATE_RESULT_SYSTEM),
        HumanMessage(
            content=VALIDATE_RESULT_USER.format(
                user_question=state.get("user_question", ""),
                time_start=state.get("parsed_request", {}).get("time_range", {}).get("start", "N/A"),
                time_end=state.get("parsed_request", {}).get("time_range", {}).get("end", "N/A"),
                generated_sql=current_sql,
                table_context=state.get("table_context", ""),
                sql_result=json.dumps(state.get("sql_result", [])[:10], ensure_ascii=False),
                failed_queries="\n".join(state.get("failed_queries", [])[-3:]),
                validation_reason=state.get("validation_reason", ""),
            )
        ),
    ]


# Helper (Node 9: validate_llm)
def _handle_unnecessary_tables(
    state: TextToSQLState, unnecessary: list[str], failed_queries: list[str]
):
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


# Helper (Node 9: validate_llm)
def _format_failed_feedback(feedback: str, hint: str) -> str:
    full_feedback = f"### 이전 시도 실패 원인\n{feedback}\n"
    if hint:
        full_feedback += f"\n### 올바른 쿼리 예시 및 힌트\n{hint}\n"
    return full_feedback


# ─────────────────────────────────────────
# Node 1: parse_request
# ─────────────────────────────────────────

async def parse_request(state: TextToSQLState) -> dict:
    # [역할] 사용자 자연어 질문을 구조화된 JSON으로 변환
    # [입력] user_question
    # [출력] parsed_request, is_request_valid/request_error(파싱 실패 시)
    logger.info("TEXT_TO_SQL:parse_request start")
    messages = [
        SystemMessage(content=PARSE_REQUEST_SYSTEM),
        HumanMessage(content=PARSE_REQUEST_USER.format(
            current_time=get_current_time(),
            user_question=state["user_question"],
        )),
    ]
    
    # 1) JSON Mode 적용 (전역 바인딩 객체 사용)
    try:
        response = await structured_llm_fast.ainvoke(messages)
        # JSON Mode이므로 바로 json.loads 사용 (명확성)
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
        # LLM 호출 자체 실패 시
        return {
            "parsed_request": {},
            "is_request_valid": False,
            "request_error": f"LLM 호출 실패: {str(e)}",
        }

    # 2) 타입 체크 (dict가 아닌 경우)
    if not isinstance(parsed, dict):
        logger.error("TEXT_TO_SQL:parse_request invalid_type=%s", type(parsed))
        return {
            "parsed_request": {},
            "is_request_valid": False,
            "request_error": "LLM 응답이 JSON 객체(dict) 형식이 아닙니다",
        }

    # 3) 기본값 보정 로직
    # Intent 처리
    if not parsed.get("intent"):
        parsed["intent"] = "unknown"

    return {"parsed_request": parsed}


# ─────────────────────────────────────────
# Node 2: validate_request
# ─────────────────────────────────────────


# 시간 범위 추출/보정 로직은 제거됨 (LLM time_range 그대로 사용).


async def validate_request(state: TextToSQLState) -> dict:
    # [역할] 파싱 결과의 유효성 검증 및 기본값 보정 (Middleware 위임)
    # [입력] parsed_request
    # [출력] is_request_valid, request_error (실패 시 결과 상태 error로 전환)
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
    is_valid, error_reason, normalized_parsed = ParsedRequestGuard.validate(parsed)
    
    if not is_valid:
        logger.info("TEXT_TO_SQL:validate_request failed: %s", error_reason)
        return {
            "is_request_valid": False,
            "request_error": error_reason,
            "validation_reason": error_reason,
            "result_status": "error",
        }
        
    # 성공 로깅
    time_range = normalized_parsed.get("time_range")
    if time_range:
        logger.info(
            "TEXT_TO_SQL:validate_request ok (time_range: %s ~ %s)",
            time_range.get("start"), time_range.get("end")
        )
    else:
        logger.info("TEXT_TO_SQL:validate_request ok (no time_range)")
        
    return {
        "parsed_request": normalized_parsed,
        "is_request_valid": True,
        "request_error": "",
    }



# ─────────────────────────────────────────
# Node 3: retrieve_tables
# ─────────────────────────────────────────

async def retrieve_tables(state: TextToSQLState) -> dict:
    # [역할] 벡터 검색으로 후보 테이블 확보 + 캐시
    # [입력] user_question
    # [출력] table_candidates, candidate_offset
    user_question = state["user_question"]
    
    # Qdrant MCP 호출
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
                    logger.info(
                        "Qdrant MCP search_tables OK: query_len=%s top_k=%s candidates=%s",
                        len(user_question),
                        RETRIEVE_K,
                        len(candidates) if isinstance(candidates, list) else "non-list",
                    )
                except json.JSONDecodeError as e:
                    logger.error(f"Qdrant MCP JSON Parsing Error: {e}, Content: {result_json[:100]}...")
                    candidates = []
                except Exception as e:
                    # 기타 예상치 못한 파싱 에러
                    logger.error(f"Qdrant MCP Parsing Unknown Error: {e}")
                    candidates = []
    except Exception as e:
        # MCP 호출 실패 시 로그 혹은 에러 처리 (여기서는 빈 리스트로 진행하여 폴백 유도)
        logger.error(f"Qdrant MCP Tool Call Error: {e}")
        candidates = []

    # TEMP: view 제외 (추후 제거 예정)
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
        }

    return {
        "table_candidates": filtered,
        # 확장은 select_tables에서 실제 선택 개수 기준으로 설정
        "candidate_offset": 0,
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

    # 1. 후보 없음 즉시 종료
    if not candidates:
        logger.warning("TEXT_TO_SQL:select_tables no candidates found")
        return {
            "selected_tables": [],
            "table_context": "",
            "request_error": "후보 테이블이 없습니다",
        }

    # 2. Rerank 준비
    candidates_str = _format_candidates_for_rerank(candidates)
    
    # 3. LLM Rerank 실행
    response_json = await _call_rerank_llm(parsed, candidates_str)
    
    selected_indices = None
    if response_json:
        # 4. 결과 파싱 및 Elbow Cut
        selected_indices = _parse_rerank_response(response_json, len(candidates))
        if selected_indices:
            logger.info("TEXT_TO_SQL:select_tables rerank_success indices=%s", selected_indices)
    
    # 5. 폴백 로직 (Rerank 실패 또는 결과 0개)
    if not selected_indices:
        fallback_count = min(TOP_K, len(candidates))
        selected_indices = list(range(1, fallback_count + 1))
        logger.info("TEXT_TO_SQL:select_tables fallback applied indices=%s", selected_indices)

    # 6. 최종 선택 및 컨텍스트 생성 (중복 제거된 인덱스 사용)
    # _select_candidates 내부에서 중복 제거됨.
    # 하지만 build_table_context용 객체 리스트도 중복 제거된 인덱스를 써야 함.
    # 따라서 여기서 중복 제거를 명시적으로 한 번 하고 넘기는 것이 안전함.
    unique_indices = list(dict.fromkeys(selected_indices))
    
    selected_names = _select_candidates(candidates, unique_indices)
    
    # 실제 객체 리스트 확보 (build_table_context용)
    selected_objects = [candidates[i-1] for i in unique_indices if 1 <= i <= len(candidates)]
    table_context = build_table_context(selected_objects)

    logger.info("TEXT_TO_SQL:select_tables final_selected=%s", selected_names)

    return {
        "selected_tables": selected_names,
        "table_context": table_context,
        # 확장 시작점은 현재 선택된 개수 기준
        "candidate_offset": len(selected_objects),
    }


# ─────────────────────────────────────────
# Node 5: generate_sql
# ─────────────────────────────────────────

async def generate_sql(state: TextToSQLState) -> dict:
    # [역할] 선택된 테이블 컨텍스트로 SQL 생성
    # [입력] parsed_request, table_context, feedback_to_sql
    # [출력] generated_sql
    
    # 1. 입력 데이터 준비
    inputs = _build_sql_prompt_inputs(state)
    
    logger.info(
        "TEXT_TO_SQL:generate_sql start retry=%s total_loops=%s table_count=%s failed_count=%s",
        state.get("sql_retry_count", 0),
        state.get("total_loops", 0),
        inputs["_meta_table_count"],
        inputs["_meta_failed_count"],
    )

    # 2. 메시지 생성
    messages = _build_generate_sql_messages(inputs)

    # 3. LLM 호출 및 툴 확장 (최대 1회 재시도 루프)
    loop_count = 0
    max_loops = 2
    
    current_state = state.copy()  # 로컬 상태 복사
    last_tool_usage_log = None  # 툴 사용 로그 임시 저장

    while loop_count < max_loops:
        loop_count += 1
        response = await llm_smart.ainvoke(messages)
        parsed, error = parse_json_from_llm(response.content)

        # JSON 파싱 실패 시, 원본 텍스트를 SQL로 간주하고 시도 (Fallback)
        if error or not parsed:
            logger.warning("TEXT_TO_SQL:generate_sql JSON parse failed, assuming raw SQL. Error: %s", error)
            # 마크다운 제거 정도만 수행
            raw_sql = response.content.strip()
            # 간단한 정규화 시도
            try:
                normalized = normalize_sql(raw_sql)
                return {"generated_sql": normalized, "sql_guard_error": ""}
            except ValueError:
                # 정규화 실패 시 그냥 반환하여 guard_sql에서 처리
                return {"generated_sql": raw_sql, "sql_guard_error": ""}

        needs_tables = parsed.get("needs_more_tables", False)
        sql_text = parsed.get("sql", "")

        # A. 테이블 확장 요청
        if needs_tables:
            # 이미 확장 실패 경험이 있다면 -> 무시하고 SQL 반환 (혹은 Fallback 시도)
            if current_state.get("table_expand_failed"):
                logger.info("TEXT_TO_SQL:generate_sql expand requested but already failed. Using generated SQL.")
                # LLM이 fallback SQL을 줬으면 그것 사용
                if sql_text:
                     break # 루프 종료하고 결과 반환
                
                # 만약 SQL도 비어있다면 Force Generation 요청 처리 (다음 루프에서 해결되길 기대)
                # 여기서는 명시적으로 실패 로그 남기고 빈 SQL 반환 가능성 있음
                break

            # 확장 시도
            logger.info("TEXT_TO_SQL:generate_sql Triggering tool: expand_tables")
            
            candidates = current_state.get("table_candidates", []) or []
            offset = current_state.get("candidate_offset", TOP_K)
            selected = list(current_state.get("selected_tables", []) or [])
            
            new_selected, new_context, new_offset = expand_tables_tool(selected, candidates, offset)
            
            # 확장 결과 확인
            if new_offset > offset:
                # 성공
                added_count = new_offset - offset
                tool_msg = f"테이블 확장 툴 실행 (추가됨: {new_selected[-added_count:]})"
                logger.info(tool_msg)
                last_tool_usage_log = tool_msg
                
                # 상태 갱신
                current_state["selected_tables"] = new_selected
                current_state["table_context"] = new_context
                current_state["candidate_offset"] = new_offset
                current_state["table_expand_attempted"] = True
                
                # 다음 루프를 위해 inputs/messages 재생성
                inputs["table_name"] = ", ".join(new_selected)
                inputs["columns"] = new_context
                inputs["_meta_table_count"] = len(new_selected)
                messages = _build_generate_sql_messages(inputs)
                
                continue
                
            else:
                # 실패 (더 이상 후보 없음)
                logger.info("TEXT_TO_SQL:generate_sql expand requested but no candidates.")
                
                fail_msg = "테이블 확장 시도했으나 추가 후보 없음"
                last_tool_usage_log = fail_msg

                current_state["table_expand_failed"] = True
                current_state["table_expand_attempted"] = True
                
                # 프롬프트에 알림
                inputs["validation_reason"] += f"\n(시스템 알림: {fail_msg}. 현재 정보로 진행하세요.)"
                messages = _build_generate_sql_messages(inputs)
                
                continue

        # B. 정상 반환 (SQL 생성됨)
        break

    # 루프 종료 후 결과 구성
    # 마지막 루프의 sql_text 사용 (JSON 파싱 성공 시)
    # 파싱된 sql_text가 없으면(확장하다가 루프 끝난 경우 등) 빈 문자열일 수 있음
    
    # 마지막 응답이 JSON이 아니었거나 에러였으면 위에서 처리됨.
    # 여기까지 왔다는 건 'JSON 파싱 성공' AND ('needs_tables=False' OR '확장 실패 후 break')
    
    # 안전장치: sql_text가 없을 수 있음 (루프 한도 초과 등)
    if not sql_text and loop_count >= max_loops:
         sql_text = ""
         state_error = "Generating SQL Loop Limit exceeded"
    else:
         state_error = ""

    result_update = {
        "generated_sql": sql_text, 
        "sql_guard_error": state_error,
    }
    
    # 변경된 상태 반영
    keys_to_update = [
        "table_expand_attempted", "table_expand_failed", 
        "selected_tables", "table_context", "candidate_offset"
    ]
    for k in keys_to_update:
        if k in current_state:
            result_update[k] = current_state[k]

    # 툴 사용 로그
    if last_tool_usage_log:
        result_update["last_tool_usage"] = last_tool_usage_log

    return result_update


# ─────────────────────────────────────────
# Node 6: guard_sql
# ─────────────────────────────────────────

# 전역 싱글톤 가드 인스턴스
sql_guard = SqlOutputGuard()

async def guard_sql(state: TextToSQLState) -> dict:
    # [역할] generated_sql 정규화 + OutputGuard 안전성 검사
    # [입력] generated_sql
    # [출력] generated_sql (normalized) or sql_guard_error
    
    current_sql = state.get("generated_sql", "")
    
    # 1. 빈 SQL 체크
    if not current_sql:
        logger.warning("TEXT_TO_SQL:guard_sql blocked: SQL is empty")
        return {
            "generated_sql": "",
            "sql_guard_error": "SQL이 비어있습니다",
            "sql_retry_count": state.get("sql_retry_count", 0) + 1,
            "total_loops": state.get("total_loops", 0) + 1,
        }

    # 2. 가드 검증 (싱글톤 사용)
    is_valid, result_or_error = sql_guard.validate_sql(current_sql)
    
    if not is_valid:
        logger.warning(f"TEXT_TO_SQL:guard_sql blocked: {result_or_error}")
        return {
            "generated_sql": current_sql, # 원본 유지 (디버깅용)
            "sql_guard_error": result_or_error,
            "sql_retry_count": state.get("sql_retry_count", 0) + 1,
            "total_loops": state.get("total_loops", 0) + 1,
        }

    # Pass
    logger.info("TEXT_TO_SQL:guard_sql passed")
    return {
        "generated_sql": result_or_error, # 정규화된 SQL
        "sql_guard_error": ""
    }


# ─────────────────────────────────────────
# Node 7: execute_sql
# ─────────────────────────────────────────

async def execute_sql(state: TextToSQLState) -> dict:
    # [역할] MCP 도구를 통해 SQL 실행 (Refactored)
    # [입력] generated_sql
    # [출력] sql_result, sql_error, raw_sql_result(디버깅용)
    
    current_sql = state.get("generated_sql", "").strip()
    logger.info("TEXT_TO_SQL:execute_sql start")

    # 1. SQL 유효성 체크
    if not current_sql:
        logger.warning("TEXT_TO_SQL:execute_sql empty_sql")
        return {
            "sql_result": [],
            "sql_error": "SQL이 비어있음",
            "raw_sql_result": ""
        }

    raw_sql_result = ""
    sql_result = []
    
    try:
        # 2. MCP 호출
        async with postgres_client() as client:
            raw_sql_result = await client.call_tool("execute_sql", {"query": current_sql})

        # 3. JSON 파싱
        # MCP 응답은 문자열 형태의 JSON으로 가정
        try:
           sql_result = json.loads(raw_sql_result)
        except json.JSONDecodeError as e:
            logger.error(f"TEXT_TO_SQL:execute_sql parse_fail raw={raw_sql_result[:100]}... error={e}")
            return {
                "sql_result": [],
                "sql_error": "JSON 파싱 실패",
                "raw_sql_result": raw_sql_result
            }

        # 4. 타입 검증 (List 여부)
        if not isinstance(sql_result, list):
            logger.error(f"TEXT_TO_SQL:execute_sql type_fail type={type(sql_result)}")
            return {
                "sql_result": [],
                "sql_error": "결과 형식 오류 (List가 아님)",
                "raw_sql_result": raw_sql_result
            }

        # 5. 성공
        logger.info(f"TEXT_TO_SQL:execute_sql success rows={len(sql_result)}")
        return {
            "sql_result": sql_result,
            "sql_error": "",
            "raw_sql_result": raw_sql_result
        }

    except Exception as e:
        # MCP 호출 자체 에러 등
        logger.error(f"TEXT_TO_SQL:execute_sql mcp_error={e}")
        return {
            "sql_result": [],
            "sql_error": str(e),
            "raw_sql_result": raw_sql_result
        }


# ─────────────────────────────────────────
# Node 8: normalize_result
# ─────────────────────────────────────────

async def normalize_result(state: TextToSQLState) -> dict:
    # [역할] 실행 결과/에러를 표준 상태로 분류 (Node 8)
    # [입력] sql_result, sql_error
    # [출력] result_status, verdict, validation_reason, feedback_to_sql
    
    sql_error = state.get("sql_error")
    
    # 1. SQL 실행 에러 발생 시
    if sql_error:
        verdict, reason = classify_sql_error(sql_error)
        logger.error("TEXT_TO_SQL:normalize_result sql_error=%s verdict=%s", sql_error, verdict)
        
        return {
            "result_status": "error",
            "verdict": verdict,
            "validation_reason": reason,
            "feedback_to_sql": reason,
            # failed_queries 누적은 9번 노드로 이관
            "total_loops": state.get("total_loops", 0) + 1,
        }

    # 2. 결과 데이터가 없는 경우
    if not state.get("sql_result"):
        logger.info("TEXT_TO_SQL:normalize_result empty_result")
        return {
            "result_status": "empty",
            # 판정은 9번 노드(validate_llm)에서 수행
        }

    # 3. 결과 데이터가 있는 경우
    logger.info("TEXT_TO_SQL:normalize_result success_result")
    return {
        "result_status": "ok",
        # 판정은 9번 노드(validate_llm)에서 수행
    }


# ─────────────────────────────────────────
# Node 9: validate_llm
# ─────────────────────────────────────────
async def validate_llm(state: TextToSQLState) -> dict:
    # [역할] 최종 정합성 판단 및 Verdict 확정 (Node 9)
    # [입력] user_question, generated_sql, sql_result, result_status, verdict(from Node 8)
    # [출력] verdict, feedback_to_sql, validation_reason, failed_queries
    status = state.get("result_status")
    current_sql = state.get("generated_sql", "")
    failed_queries = list(state.get("failed_queries", []) or [])

    if status == "error":
        verdict = state.get("verdict", "SQL_BAD")
        logger.info("TEXT_TO_SQL:validate_llm skip (previous error status), verdict=%s", verdict)
        if verdict == "SQL_BAD":
            failed_queries = _append_failed_query(failed_queries, current_sql)
        return {"failed_queries": failed_queries}

    messages = _build_validation_messages(state, current_sql)
    response = await llm_smart.ainvoke(messages)
    parsed_json, error = parse_json_from_llm(response.content)

    if error or not parsed_json:
        logger.error("TEXT_TO_SQL:validate_llm parse_error=%s", error)
        verdict = "OK" if state.get("sql_result") else "DATA_MISSING"
        return {
            "verdict": verdict,
            "validation_reason": "검증 응답 파싱 실패",
        }

    verdict = parsed_json.get("verdict", "AMBIGUOUS")
    feedback = parsed_json.get("feedback_to_sql", "")
    hint = parsed_json.get("correction_hint", "")
    unnecessary = parsed_json.get("unnecessary_tables", []) or []

    if verdict == "SQL_BAD":
        failed_queries = _append_failed_query(failed_queries, current_sql)

    unnecessary_result = _handle_unnecessary_tables(state, unnecessary, failed_queries)
    if unnecessary_result:
        return unnecessary_result

    # C. TABLE_MISSING 처리 (내부 툴 호출로 테이블 확장)
    if verdict == "TABLE_MISSING":
        # 이미 이전에 확장 실패했다면 바로 데이터 없음 처리
        if state.get("table_expand_failed"):
            logger.info("TEXT_TO_SQL:validate_llm TABLE_MISSING but already failed to expand previously.")
            verdict = "DATA_MISSING"
            return {
                "verdict": verdict,
                "validation_reason": "추가 테이블을 찾을 수 없습니다 (확장 실패)",
                "failed_queries": failed_queries,
            }
        candidates = state.get("table_candidates", []) or []
        current_offset = state.get("candidate_offset", TOP_K)
        selected = list(state.get("selected_tables", []) or [])
        
        # 툴 호출
        new_selected, new_context, new_offset = expand_tables_tool(
            selected, candidates, current_offset
        )
        
        # 확장 성공 여부 확인 (Offset이 안 늘어났거나, 테이블이 안 늘어났거나)
        if new_offset <= current_offset:
            # 더 이상 확장할 게 없으면 실패 처리
            logger.info("TEXT_TO_SQL:validate_llm TABLE_MISSING but no more tables to expand")
            verdict = "DATA_MISSING"  # 확장 불가 -> 데이터 없음으로 종결
            return {
                "verdict": verdict,
                "validation_reason": "추가 후보 테이블이 없습니다",
                "failed_queries": failed_queries,
            }
        else:
            # 확장 성공 -> 재시도 (Node 5로 이동 유도)
            logger.info("TEXT_TO_SQL:validate_llm expanded tables: %s", new_selected)
            return {
                "selected_tables": new_selected,
                "table_context": new_context,
                "candidate_offset": new_offset,
                "verdict": "RETRY_SQL", # 그래프에서 generate_sql로 라우팅
                "validation_reason": "테이블 정보 부족으로 컨텍스트 확장",
                "failed_queries": failed_queries,
                "table_expand_count": state.get("table_expand_count", 0) + 1,
                "total_loops": state.get("total_loops", 0) + 1,
            }

    if verdict != "OK":
        full_feedback = _format_failed_feedback(feedback, hint)
        logger.info("TEXT_TO_SQL:validate_llm failed, verdict=%s", verdict)
        return {
            "verdict": verdict,
            "feedback_to_sql": full_feedback,
            "validation_reason": feedback,
            "failed_queries": failed_queries,
            "validation_retry_count": state.get("validation_retry_count", 0) + 1,
            "total_loops": state.get("total_loops", 0) + 1,
        }

    logger.info("TEXT_TO_SQL:validate_llm OK")
    return {
        "verdict": "OK",
        "validation_reason": "",
        "feedback_to_sql": "",
        "failed_queries": failed_queries,
    }




# ─────────────────────────────────────────
# Node 10: generate_report
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
            generated_sql=state.get("generated_sql", ""),
            sql_result=json.dumps(state.get("sql_result", [])[:20], ensure_ascii=False, indent=2),
            validation_reason=state.get("validation_reason", ""),
        )),
    ]
    response = await llm_fast.ainvoke(messages)
    report = response.content.strip()

    # 테이블 확장 실패 시 경고 문구 추가
    if state.get("table_expand_failed"):
        warning_msg = "\n\n> ⚠️ **주의**: 분석에 필요한 테이블이 일부 누락되었을 수 있어, 결과가 제한적일 수 있습니다."
        report += warning_msg

    suggested_actions = []
    for line in report.splitlines():
        if re.match(r"^\d+\.\s", line.strip()):
            suggested_actions.append(line.strip())

    return {
        "report": report,
        "suggested_actions": suggested_actions,
        "sql_result": state.get("sql_result", []),
        "generated_sql": state.get("generated_sql", ""),
    }
