"""Text-to-SQL 노드 구현 모음.

노드 함수는 실제 그래프 실행 순서 기준으로 번호를 매깁니다.
(분기 노드 포함)
"""

import json

from langchain_core.messages import SystemMessage, HumanMessage, AIMessage

from src.agents.mcp_clients.connector import postgres_client, qdrant_search_client
from src.agents.text_to_sql.middleware.parsed_request_guard import ParsedRequestGuard
from src.agents.text_to_sql.middleware.sql_safety_guard import SqlOutputGuard

from .table_expand_too import expand_tables_tool
from .state import TextToSQLState
from .prompts import (
    PARSE_REQUEST_SYSTEM,
    PARSE_REQUEST_USER,
    GENERATE_REPORT_SYSTEM,
    GENERATE_REPORT_USER,
    CLASSIFY_INTENT_SYSTEM,
    CLASSIFY_INTENT_USER,
    GENERAL_CHAT_SYSTEM,
    GENERAL_CHAT_USER,
    CLARIFICATION_CHECK_SYSTEM,
    CLARIFICATION_CHECK_USER,
)
from .common.constants import RETRIEVE_K, TOP_K
from .common.utils import (
    get_current_time,
    parse_json_from_llm,
    normalize_sql,
    build_table_context,
    classify_sql_error,
)
from .common.helpers import (
    structured_llm_fast,
    llm_fast,
    llm_smart,
    logger,
    _trim_conversation,
    _extract_previous_sql_from_messages,
    _format_candidates_for_rerank,
    _call_rerank_llm,
    _parse_rerank_response,
    _select_candidates,
    _extract_tables_from_sql,
    _build_sql_prompt_inputs,
    _build_generate_sql_messages,
    _append_failed_query,
    _build_validation_messages,
    _handle_unnecessary_tables,
    _format_failed_feedback,
)


# ─────────────────────────────────────────
# Node 0: classify_intent (그래프 진입점)
# ─────────────────────────────────────────

async def classify_intent(state: TextToSQLState) -> dict:
    """사용자 질문을 SQL 조회 vs 일반 대화로 분류."""
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

        if isinstance(intent, str):
            intent = intent.lower().strip()

        if intent not in ["sql", "general"]:
            logger.warning(
                "TEXT_TO_SQL:classify_intent invalid_intent=%s, fallback to sql",
                intent,
            )
            intent = "sql"

        logger.info("TEXT_TO_SQL:classify_intent result=%s reason=%s", intent, reason)
    except Exception as e:
        logger.warning("TEXT_TO_SQL:classify_intent error=%s, defaulting to sql", e)
        intent = "sql"

    return {
        "classified_intent": intent,
        "last_tool_usage": f"질문 유형 판별: {intent}",
        "messages": [HumanMessage(content=user_question)],
    }


# ─────────────────────────────────────────
# Node 1: general_chat (general 분기 종료 노드)
# ─────────────────────────────────────────

async def general_chat(state: TextToSQLState) -> dict:
    """일반 대화(인사, 설명 등)에 대한 응답 생성."""
    logger.info("TEXT_TO_SQL:general_chat start")

    history = _trim_conversation(state)
    logger.info("TEXT_TO_SQL:general_chat history_len=%d", len(history))

    messages = [
        SystemMessage(content=GENERAL_CHAT_SYSTEM),
        *history,
    ]

    current_q = state.get("user_question", "")
    is_duplicate = False
    if history:
        last_msg = history[-1]
        if hasattr(last_msg, "content") and (
            last_msg.content == current_q or current_q in last_msg.content
        ):
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
        "messages": [AIMessage(content=answer)],
    }


# ─────────────────────────────────────────
# Node 2: parse_request
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
    except json.JSONDecodeError as e:
        logger.error("TEXT_TO_SQL:parse_request json_decode_error=%s", e)
        err = f"JSON 파싱 실패: {e}"
        return {
            "parsed_request": {},
            "is_request_valid": False,
            "request_error": err,
            "validation_reason": err,
            "last_tool_usage": err,
        }
    except Exception as e:
        logger.error("TEXT_TO_SQL:parse_request llm_error=%s", e)
        err = f"LLM 호출 실패: {str(e)}"
        return {
            "parsed_request": {},
            "is_request_valid": False,
            "request_error": err,
            "validation_reason": err,
            "last_tool_usage": err,
        }

    if not isinstance(parsed, dict):
        logger.error("TEXT_TO_SQL:parse_request invalid_type=%s", type(parsed))
        err = "LLM 응답이 JSON 객체(dict) 형식이 아닙니다"
        return {
            "parsed_request": {},
            "is_request_valid": False,
            "request_error": err,
            "validation_reason": err,
            "last_tool_usage": err,
        }

    old_parsed = state.get("parsed_request", {}) or {}

    if not parsed.get("intent"):
        parsed["intent"] = "unknown"

    new_time = parsed.get("time_range", {})
    is_all_time = new_time.get("all_time") if new_time else False

    if is_all_time:
        logger.info("TEXT_TO_SQL:parse_request all_time=True detected. Skipping inheritance.")
    elif not new_time or (not new_time.get("start") and not new_time.get("end")):
        if old_parsed.get("time_range"):
            parsed["time_range"] = old_parsed.get("time_range")
            logger.info("TEXT_TO_SQL:parse_request inherited time_range from history")

    if not parsed.get("metric"):
        if old_parsed.get("metric"):
            parsed["metric"] = old_parsed.get("metric")
            logger.info("TEXT_TO_SQL:parse_request inherited metric from history")
        elif "사용률" in state["user_question"] or "상위" in state["user_question"]:
            pass

    return {
        "parsed_request": parsed,
        "is_request_valid": True,
        "request_error": "",
        "validation_reason": "",
        "last_tool_usage": "질문 파싱 완료",
    }


# ─────────────────────────────────────────
# Node 3: validate_request
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
            "last_tool_usage": f"질문 검증 실패: {err}",
        }

    parsed = state.get("parsed_request", {})

    is_valid, error_reason, normalized_parsed, adjustment_info = ParsedRequestGuard.validate(parsed)

    if not is_valid:
        logger.info("TEXT_TO_SQL:validate_request failed: %s", error_reason)
        return {
            "is_request_valid": False,
            "request_error": error_reason,
            "validation_reason": error_reason,
            "result_status": "error",
            "last_tool_usage": f"검증 실패: {error_reason}",
        }

    log_msg = "질문 검증 완료"
    if adjustment_info:
        log_msg = f"질문 보정: {adjustment_info}"

    return {
        "parsed_request": normalized_parsed,
        "is_request_valid": True,
        "request_error": "",
        "last_tool_usage": log_msg,
    }


# ─────────────────────────────────────────
# Node 4: check_clarification
# ─────────────────────────────────────────

async def check_clarification(state: TextToSQLState) -> dict:
    """파싱된 요청의 핵심 정보 충분 여부를 LLM으로 판단."""
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
        "last_tool_usage": "필수 정보 확인 완료",
    }


# ─────────────────────────────────────────
# Node 5: retrieve_tables
# ─────────────────────────────────────────

async def retrieve_tables(state: TextToSQLState) -> dict:
    """테이블 검색: 후속 질문 확인 또는 Qdrant 벡터 검색."""
    user_question = state["user_question"]
    parsed_request = state.get("parsed_request", {})

    if parsed_request.get("is_followup"):
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
                    "last_tool_usage": (
                        f"후속 질문: 이전 쿼리에 사용된 테이블 재사용 ({', '.join(tables)})"
                    ),
                }

    candidates = []
    try:
        async with qdrant_search_client() as client:
            result_json = await client.call_tool("search_tables", {
                "query": user_question,
                "top_k": RETRIEVE_K,
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
            "last_tool_usage": "검색 결과: 관련 테이블 없음",
        }

    return {
        "table_candidates": filtered,
        "candidate_offset": 0,
        "last_tool_usage": f"벡터 검색 완료: {len(filtered)}개의 후보 테이블 확보",
    }


# ─────────────────────────────────────────
# Node 6: select_tables
# ─────────────────────────────────────────

async def select_tables(state: TextToSQLState) -> dict:
    """후보 테이블 중 최적의 테이블 선택 (LLM Rerank)."""
    parsed = state.get("parsed_request", {})
    candidates = state.get("table_candidates", []) or []

    if not candidates:
        logger.warning("TEXT_TO_SQL:select_tables no candidates found")
        err = "후보 테이블이 없습니다"
        return {
            "selected_tables": [],
            "table_context": "",
            "request_error": err,
            "validation_reason": err,
            "last_tool_usage": err,
        }

    candidates_str = _format_candidates_for_rerank(candidates)
    response_json = await _call_rerank_llm(parsed, candidates_str)

    selected_indices = None
    if response_json:
        selected_indices = _parse_rerank_response(response_json, len(candidates))
        if selected_indices:
            logger.info("TEXT_TO_SQL:select_tables rerank_success")

    if not selected_indices:
        fallback_count = min(TOP_K, len(candidates))
        selected_indices = list(range(1, fallback_count + 1))
        logger.info("TEXT_TO_SQL:select_tables fallback applied")

    unique_indices = list(dict.fromkeys(selected_indices))
    selected_names = _select_candidates(candidates, unique_indices)
    selected_objects = [candidates[i - 1] for i in unique_indices if 1 <= i <= len(candidates)]
    table_context = build_table_context(selected_objects)

    logger.info("TEXT_TO_SQL:select_tables final_selected=%s", selected_names)

    return {
        "selected_tables": selected_names,
        "table_context": table_context,
        "candidate_offset": len(selected_objects),
        "last_tool_usage": f"연관성 높은 테이블 선택: {', '.join(selected_names)}",
    }


# ─────────────────────────────────────────
# Node 7: generate_sql
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

        if error or not parsed:
            logger.warning("TEXT_TO_SQL:generate_sql JSON parse failed, assuming raw SQL")
            raw_sql = response.content.strip()
            try:
                normalized = normalize_sql(raw_sql)
                return {
                    "generated_sql": normalized,
                    "sql_guard_error": "",
                    "last_tool_usage": "SQL 생성 응답 파싱 폴백 사용",
                }
            except ValueError:
                return {
                    "generated_sql": raw_sql,
                    "sql_guard_error": "",
                    "last_tool_usage": "SQL 생성 응답 파싱 폴백 사용",
                }

        needs_tables = parsed.get("needs_more_tables", False)
        sql_text = parsed.get("sql", "")

        if needs_tables:
            if current_state.get("table_expand_failed"):
                if sql_text:
                    break
                break

            logger.info("TEXT_TO_SQL:generate_sql Triggering tool: expand_tables")
            current_state["table_expand_count"] = current_state.get("table_expand_count", 0) + 1

            candidates = current_state.get("table_candidates", []) or []
            offset = current_state.get("candidate_offset", TOP_K)
            selected = list(current_state.get("selected_tables", []) or [])

            new_selected, new_context, new_offset = expand_tables_tool(selected, candidates, offset)

            if new_offset > offset:
                added_count = new_offset - offset
                tool_msg = f"테이블 확장 툴 실행 (추가됨: {new_selected[-added_count:]})"
                logger.info(tool_msg)
                last_tool_usage_log = tool_msg

                current_state["selected_tables"] = new_selected
                current_state["table_context"] = new_context
                current_state["candidate_offset"] = new_offset
                current_state["table_expand_attempted"] = True
                current_state["table_expand_reason"] = tool_msg

                inputs["table_name"] = ", ".join(new_selected)
                inputs["columns"] = new_context
                inputs["_meta_table_count"] = len(new_selected)
                messages = _build_generate_sql_messages(inputs)
                continue

            fail_msg = "테이블 확장 시도했으나 추가 후보 없음"
            last_tool_usage_log = fail_msg

            current_state["table_expand_failed"] = True
            current_state["table_expand_attempted"] = True
            current_state["table_expand_reason"] = fail_msg

            inputs["validation_reason"] += (
                f"\n(시스템 알림: {fail_msg}. 현재 정보로 진행하세요.)"
            )
            messages = _build_generate_sql_messages(inputs)
            continue

        break

    if not sql_text and loop_count >= max_loops:
        sql_text = ""
        state_error = "Generating SQL Loop Limit exceeded"
    else:
        state_error = ""

    result_update = {
        "generated_sql": sql_text,
        "sql_guard_error": state_error,
        "last_tool_usage": "SQL 쿼리 생성 완료" if sql_text else "SQL 생성 실패",
    }

    keys_to_update = [
        "table_expand_attempted",
        "table_expand_failed",
        "table_expand_count",
        "table_expand_reason",
        "selected_tables",
        "table_context",
        "candidate_offset",
    ]
    for k in keys_to_update:
        if k in current_state:
            result_update[k] = current_state[k]

    if last_tool_usage_log:
        result_update["last_tool_usage"] = last_tool_usage_log

    return result_update


# ─────────────────────────────────────────
# Node 8: guard_sql
# ─────────────────────────────────────────

sql_guard = SqlOutputGuard()


async def guard_sql(state: TextToSQLState) -> dict:
    """생성된 SQL의 안전성 검사 (Syntax, 금지어 등)."""
    current_sql = state.get("generated_sql", "")

    if not current_sql:
        logger.warning("TEXT_TO_SQL:guard_sql blocked: SQL is empty")
        return {
            "generated_sql": "",
            "sql_guard_error": "SQL이 비어있습니다",
            "validation_reason": "SQL이 비어있습니다",
            "sql_retry_count": state.get("sql_retry_count", 0) + 1,
            "total_loops": state.get("total_loops", 0) + 1,
            "last_tool_usage": "SQL 안전성 검사 실패: SQL 비어있음",
        }

    is_valid, result_or_error = sql_guard.validate_sql(current_sql)

    if not is_valid:
        logger.warning(f"TEXT_TO_SQL:guard_sql blocked: {result_or_error}")
        return {
            "generated_sql": current_sql,
            "sql_guard_error": result_or_error,
            "validation_reason": result_or_error,
            "sql_retry_count": state.get("sql_retry_count", 0) + 1,
            "total_loops": state.get("total_loops", 0) + 1,
            "last_tool_usage": f"SQL 안전성 검사 실패: {result_or_error}",
        }

    logger.info("TEXT_TO_SQL:guard_sql passed")
    return {
        "generated_sql": result_or_error,
        "sql_guard_error": "",
        "last_tool_usage": "SQL 안전성 검사 통과",
    }


# ─────────────────────────────────────────
# Node 9: execute_sql
# ─────────────────────────────────────────

async def execute_sql(state: TextToSQLState) -> dict:
    """SQL 실행 및 결과 반환 (PostgreSQL MCP)."""
    sql = state.get("generated_sql")
    logger.info(f"TEXT_TO_SQL:execute_sql executing: {sql[:50]}...")

    try:
        async with postgres_client() as client:
            result_json = await client.call_tool("execute_sql", {"query": sql})

            if isinstance(result_json, str):
                try:
                    result_data = json.loads(result_json)
                except json.JSONDecodeError:
                    result_data = result_json
            else:
                result_data = result_json

            if isinstance(result_data, dict) and result_data.get("is_error"):
                return {
                    "sql_result": [],
                    "sql_error": result_data.get("message", "Unknown DB Error"),
                    "last_tool_usage": f"SQL 실행 에러: {result_data.get('message', 'Unknown DB Error')}",
                }

            if not isinstance(result_data, list):
                result_data = []

            return {
                "sql_result": result_data,
                "sql_error": None,
                "last_tool_usage": f"SQL 실행 완료 (결과 {len(result_data)}행)",
            }

    except Exception as e:
        logger.error(f"TEXT_TO_SQL:execute_sql failed: {e}")
        return {
            "sql_result": [],
            "sql_error": str(e),
            "last_tool_usage": f"SQL 실행 에러: {str(e)}",
        }


# ─────────────────────────────────────────
# Node 10: normalize_result
# ─────────────────────────────────────────

async def normalize_result(state: TextToSQLState) -> dict:
    """실행 결과 정규화 및 에러 분류."""
    sql_error = state.get("sql_error")

    if sql_error:
        error_type, error_reason = classify_sql_error(str(sql_error))

        retry_count = state.get("sql_retry_count", 0) + 1
        total_loops = state.get("total_loops", 0) + 1

        failed_list = state.get("failed_queries", []) or []
        current_sql = state.get("generated_sql", "")
        failed_list = _append_failed_query(failed_list, current_sql)

        failed_msg = f"SQL 실행 실패 ({error_type}): {sql_error}"
        logger.warning(f"TEXT_TO_SQL:normalize_result {failed_msg}")

        return {
            "sql_retry_count": retry_count,
            "total_loops": total_loops,
            "verdict": error_type,
            "validation_reason": f"{error_reason}: {sql_error}",
            "failed_queries": failed_list,
            "last_tool_usage": failed_msg,
        }

    return {
        "verdict": "OK",
        "validation_reason": "",
        "last_tool_usage": "SQL 실행 결과 정규화 완료",
    }


# ─────────────────────────────────────────
# Node 11: validate_llm
# ─────────────────────────────────────────

async def validate_llm(state: TextToSQLState) -> dict:
    """실행 결과의 논리적 정확성 검증 (LLM)."""
    if state.get("sql_error"):
        return {"last_tool_usage": "SQL 실행 오류가 있어 결과 검증 생략"}

    current_sql = state.get("generated_sql", "")
    messages = _build_validation_messages(state, current_sql)

    response = await llm_smart.ainvoke(messages)
    parsed, error = parse_json_from_llm(response.content)

    if error or not parsed:
        logger.warning("TEXT_TO_SQL:validate_llm JSON parse failed")
        return {
            "verdict": "OK",
            "validation_reason": "검증 응답 파싱 실패, 결과 수용",
            "last_tool_usage": "결과 검증 응답 파싱 실패 (수용)",
        }

    verdict = parsed.get("verdict", "OK")
    reason = parsed.get("feedback_to_sql") or parsed.get("reason", "")
    hint = parsed.get("correction_hint") or parsed.get("hint", "")
    unnecessary_tables = parsed.get("unnecessary_tables", [])

    if verdict != "OK":
        state_update = {
            "verdict": verdict,
            "validation_reason": _format_failed_feedback(reason, hint),
            "sql_retry_count": state.get("sql_retry_count", 0) + 1,
            "validation_retry_count": state.get("validation_retry_count", 0) + 1,
            "total_loops": state.get("total_loops", 0) + 1,
            "last_tool_usage": f"결과 검증 재시도 필요: {verdict}",
        }

        table_retry = _handle_unnecessary_tables(
            state,
            unnecessary_tables,
            state.get("failed_queries", []),
        )
        if table_retry:
            return table_retry

        failed = state.get("failed_queries", []) or []
        state_update["failed_queries"] = _append_failed_query(failed, current_sql)

        return state_update

    return {
        "verdict": "OK",
        "validation_reason": reason,
        "last_tool_usage": "결과 검증 완료",
    }


# ─────────────────────────────────────────
# Node 12: generate_report
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
        )),
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
        "suggested_actions": [],
        "last_tool_usage": "최종 보고서 생성 완료",
        "messages": [AIMessage(content=answer)],
        "sql_result": state.get("sql_result", []),
    }
