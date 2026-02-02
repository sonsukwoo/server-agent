"""Text-to-SQL 에이전트 노드/미들웨어 (통합형)"""
import json
import re
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
import dateparser

from langchain_openai import ChatOpenAI
from langchain_core.messages import SystemMessage, HumanMessage

import logging
from config.settings import settings
from src.agents.mcp_clients.connector import postgres_client, qdrant_search_client

logger = logging.getLogger("TEXT_TO_SQL")

from .state import TextToSQLState
from .prompts import (
    PARSE_REQUEST_SYSTEM, PARSE_REQUEST_USER,
    RERANK_TABLE_SYSTEM, RERANK_TABLE_USER,
    GENERATE_SQL_SYSTEM, GENERATE_SQL_USER,
    VALIDATE_RESULT_SYSTEM, VALIDATE_RESULT_USER,
    GENERATE_REPORT_SYSTEM, GENERATE_REPORT_USER,
)
from .constants import (
    RETRIEVE_K, TOP_K, TIMEZONE
)
from .utils import (
    get_current_time, get_now, parse_json_from_llm, normalize_sql,
    build_table_context, rebuild_context_from_candidates,
    classify_sql_error, next_batch, apply_elbow_cut
)
from .middleware.output_guard import SqlOutputGuard

llm_fast = ChatOpenAI(model=settings.model_fast, temperature=0)
llm_smart = ChatOpenAI(model=settings.model_smart, temperature=0)

# JSON Mode 강제 바인딩 (전역 생성)
structured_llm_fast = llm_fast.bind(response_format={"type": "json_object"})


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


def _detect_date_anchor(question: str) -> datetime.date:
    """질문에서 '오늘', '어제' 같은 날짜 앵커 추출 (없으면 오늘)"""
    now = get_now()
    if not question:
        return now.date()

    # 시간 해석 없이 날짜만 잡기 위한 설정
    settings = {
        'TIMEZONE': TIMEZONE,
        'RETURN_AS_TIMEZONE_AWARE': True,
        'RELATIVE_BASE': now,
        'PREFER_DATES_FROM': 'past'
    }
    
    try:
        # dateparser가 시간을 포함해 해석하더라도 .date()만 취함
        dt = dateparser.parse(question, settings=settings, languages=['ko'])
        if dt:
            return dt.date()
    except Exception:
        pass
        
    return now.date()


def _extract_time_range_text(question: str) -> tuple[int, int, int, int] | None:
    """
    질문에서 'N시~M시' 패턴 추출
    :return: (h1, m1, h2, m2) 튜플 or None
    """
    if not question:
        return None

    # 패턴: (오전/오후)? N시 (분)? ~ (오전/오후)? M시 (분)?
    # 예: "13:00~15:00", "1시부터 3시", "오후 1시~3시"
    range_pattern = re.compile(r"(오전|오후)?\s*(\d{1,2})(?:시|:)(\d{2})?\s*(?:~|-|부터|에서)\s*(오전|오후)?\s*(\d{1,2})(?:시|:)(\d{2})?")
    match = range_pattern.search(question)
    
    if not match:
        return None

    ampm1, h1, m1, ampm2, h2, m2 = match.groups()

    # 오전/오후가 한쪽만 있으면 같은 값으로 맞춤
    if ampm1 and not ampm2:
        ampm2 = ampm1
    if ampm2 and not ampm1:
        ampm1 = ampm2

    def to_24hr(ampm, h, m):
        h = int(h)
        m = int(m) if m else 0
        # 오전/오후가 모두 없으면 기본을 '오후'로 간주
        if ampm is None:
            ampm = "오후"
        if ampm == "오후" and h < 12:
            h += 12
        if ampm == "오전" and h == 12:
            h = 0
        return h, m

    h1_val, m1_val = to_24hr(ampm1, h1, m1)
    h2_val, m2_val = to_24hr(ampm2, h2, m2)

    return h1_val, m1_val, h2_val, m2_val


def _build_range_from_anchor(anchor_date: datetime.date, time_tuple: tuple[int, int, int, int]) -> dict:
    """날짜 앵커와 시간 튜플을 결합하여 time_range 생성"""
    h1, m1, h2, m2 = time_tuple
    now = get_now()
    tz = getattr(now, 'tzinfo', None) or ZoneInfo(TIMEZONE)

    try:
        # datetime.combine 사용 시 time 객체 생성
        t1 = datetime.min.time().replace(hour=h1, minute=m1)
        t2 = datetime.min.time().replace(hour=h2, minute=m2)
        
        start_dt = datetime.combine(anchor_date, t1, tzinfo=tz)
        end_dt = datetime.combine(anchor_date, t2, tzinfo=tz)
        
        return {
            "start": start_dt.isoformat(),
            "end": end_dt.isoformat(),
            "timezone": TIMEZONE
        }
    except ValueError:
        return None


def _fallback_time_range(mode: str = "recent_6h") -> dict:
    """시간 정보 부재 시 Fallback 범위 생성 (기본: 최근 6시간)"""
    now = get_now()
    # mode 확장에 대비해 매개변수는 유지하되, 현재는 단일 로직으로 처리
    start_dt = now - timedelta(hours=6)
    return {
        "start": start_dt.isoformat(),
        "end": now.isoformat(),
        "timezone": TIMEZONE
    }


def _normalize_and_validate_time_range(time_range: dict) -> tuple[bool, dict | str]:
    """
    시간 범위 정규화 및 유효성 검증 (미래 차단, 역전 방지)
    :return: (is_valid, normalized_range_or_error_msg)
    """
    if not time_range or not time_range.get("start") or not time_range.get("end"):
        return False, "시간 범위 형식이 올바르지 않습니다."

    start_str = time_range["start"]
    end_str = time_range["end"]
    now = get_now()
    
    settings = {'TIMEZONE': TIMEZONE, 'RETURN_AS_TIMEZONE_AWARE': True}

    try:
        # dateparser로 파싱 (ISO 문자열도 잘 처리함)
        start_dt = dateparser.parse(str(start_str), settings=settings)
        end_dt = dateparser.parse(str(end_str), settings=settings)

        # 타임존 누락 시 보정
        if start_dt and start_dt.tzinfo is None: start_dt = start_dt.replace(tzinfo=now.tzinfo)
        if end_dt and end_dt.tzinfo is None: end_dt = end_dt.replace(tzinfo=now.tzinfo)
        
        if not start_dt or not end_dt:
             return False, "날짜 형식을 파싱할 수 없습니다."

        # 1. 미래 차단 (Start가 미래면 Fail)
        #    허용 오차 1분 (네트워크/시스템 시간 차이 고려)
        if start_dt > now + timedelta(minutes=1): 
             return False, f"미래 데이터(시작: {start_str})는 조회할 수 없습니다."
        
        # 2. 미래 차단 (End가 미래면 Fail)
        if end_dt > now + timedelta(minutes=5):
             return False, f"미래 데이터(종료: {end_str})는 조회할 수 없습니다."

        # 3. 역전 검사
        if start_dt > end_dt:
             return False, "시작 시간이 종료 시간보다 늦습니다."

        return True, {
            "start": start_dt.isoformat(),
            "end": end_dt.isoformat(),
            "timezone": TIMEZONE
        }

    except Exception as e:
        return False, f"시간 검증 중 오류 발생: {str(e)}"


async def validate_request(state: TextToSQLState) -> dict:
    # [역할] 파싱 결과의 기본 유효성/시간 범위를 검증 및 보정 (Refactored)
    # [입력] parsed_request, user_question
    # [출력] is_request_valid, request_error (실패 시 결과 상태 error로 전환)
    
    logger.info("TEXT_TO_SQL:validate_request start")
    
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

    # 1. 질문 원문 분석 (날짜 앵커 + 시간 범위 추출)
    user_question = state.get("user_question", "")
    anchor_date = _detect_date_anchor(user_question)
    time_tuple = _extract_time_range_text(user_question)
    
    candidate_tr = None
    
    # (A) 질문에서 명시적 시간 범위가 추출된 경우
    if time_tuple:
        candidate_tr = _build_range_from_anchor(anchor_date, time_tuple)
        if candidate_tr:
            logger.info("TEXT_TO_SQL:validate_request time extracted from question: %s", candidate_tr)

    # (B) LLM 결과 사용 (Regex 추출 실패 시)
    if not candidate_tr:
        llm_tr = parsed.get("time_range")
        if isinstance(llm_tr, dict) and llm_tr.get("start") and llm_tr.get("end"):
            candidate_tr = llm_tr
            # LLM 결과에 타임존이 없으면 보정이 필요하므로 normalize에서 처리

    # (C) Fallback
    if not candidate_tr:
        candidate_tr = _fallback_time_range("recent_6h")
        logger.info("TEXT_TO_SQL:validate_request fallback applied")

    # 2. 정규화 및 유효성 검증
    is_valid, result = _normalize_and_validate_time_range(candidate_tr)
    
    if not is_valid:
        # 에러 발생
        error_msg = result if isinstance(result, str) else "Invalid Time Range"
        return {
            "is_request_valid": False,
            "request_error": error_msg,
            "validation_reason": error_msg,
            "result_status": "error",
        }

    # 성공 적용
    parsed["time_range"] = result
    
    logger.info(
        "TEXT_TO_SQL:validate_request ok start=%s end=%s",
        result.get("start"),
        result.get("end"),
    )
    
    return {
        "parsed_request": parsed,
        "is_request_valid": True,
        "request_error": ""
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
        "candidate_offset": len(filtered),
    }


# ─────────────────────────────────────────
# Node 4: select_tables
# ─────────────────────────────────────────

def _format_candidates_for_rerank(candidates: list, top_col_limit: int = 5) -> str:
    """후보 테이블 리스트를 LLM 프롬프트용 텍스트로 변환"""
    candidates_str = ""
    for i, c in enumerate(candidates, 1):
        # 1) 컬럼 제한
        all_cols = c.get("columns", [])
        display_cols = [col.get("name", "") for col in all_cols[:top_col_limit]]
        col_txt = ", ".join(display_cols)
        if len(all_cols) > top_col_limit:
            col_txt += f" ... (외 {len(all_cols) - top_col_limit}개)"
        
        # 2) Description 길이 제한
        desc = c.get("description", "") or "설명 없음"
        if len(desc) > 100:
            desc = desc[:100] + "..."
            
        # 3) 기타 정보 명시
        join_keys = ", ".join(c.get("join_keys", []) or []) or "없음"
        primary_time_col = c.get("primary_time_col", "") or "없음"
        score = c.get("score", "N/A")

        candidates_str += (
            f"{i}. {c['table_name']}\n"
            f"   설명: {desc}\n"
            f"   시간 컬럼: {primary_time_col}\n"
            f"   조인 키: {join_keys}\n"
            f"   주요 컬럼: {col_txt}\n"
            f"   유사도: {score}\n\n"
        )
    return candidates_str


async def _call_rerank_llm(parsed: dict, candidates_str: str) -> list | None:
    """LLM Rerank 호출 및 JSON 파싱"""
    messages = [
        SystemMessage(content=RERANK_TABLE_SYSTEM),
        HumanMessage(content=RERANK_TABLE_USER.format(
            intent=parsed.get("intent", ""),
            metric=parsed.get("metric", "N/A"),
            condition=parsed.get("condition", "N/A"),
            candidates=candidates_str,
        )),
    ]
    try:
        response = await llm_fast.ainvoke(messages)
        parsed_json, error = parse_json_from_llm(response.content)
        if error or not isinstance(parsed_json, list):
            logger.warning("TEXT_TO_SQL:rerank_llm parsing failed or not list: %s", error)
            return None
        return parsed_json
    except Exception as e:
        logger.error("TEXT_TO_SQL:rerank_llm invoke error: %s", e)
        return None


def _parse_rerank_response(response_json: list, candidates_len: int) -> list[int] | None:
    """LLM 응답에서 유효한 인덱스 추출 및 Elbow Cut 적용"""
    scored = []
    for item in response_json:
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
    """인덱스로 실제 테이블 객체 선택 및 이름 리스트 반환"""
    selected_names = []
    # 중복 제거 및 순서 유지: dict.fromkeys() 활용
    unique_indices = list(dict.fromkeys(selected_indices))
    
    for idx in unique_indices:
        if 1 <= idx <= len(candidates):
            c = candidates[idx - 1]
            selected_names.append(c["table_name"])
    return selected_names


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
    }


# ─────────────────────────────────────────
# Node 5: generate_sql
# ─────────────────────────────────────────

def _build_sql_prompt_inputs(state: TextToSQLState) -> dict:
    """SQL 생성 프롬프트에 필요한 입력 데이터 구성"""
    parsed = state.get("parsed_request", {})
    time_range = parsed.get("time_range", {})
    
    # 테이블 이름 리스트 (없으면 N/A)
    selected_tables = state.get("selected_tables", []) or []
    table_name_str = ", ".join(selected_tables) or "N/A"
    
    # 실패 쿼리 기록 포맷팅 (인덱스 추가)
    failed_queries = state.get("failed_queries", []) or []
    if failed_queries:
        failed_queries_str = "\n".join([f"[{i+1}] {q}" for i, q in enumerate(failed_queries)])
    else:
        failed_queries_str = "없음"
        
    validation_reason = state.get("feedback_to_sql") or state.get("validation_reason") or "없음"

    return {
        "intent": parsed.get("intent", ""),
        "time_start": time_range.get("start", "N/A"),
        "time_end": time_range.get("end", "N/A"),
        "metric": parsed.get("metric", "N/A"),
        "condition": parsed.get("condition", "N/A"),
        "table_name": table_name_str,
        "columns": state.get("table_context", ""),
        "failed_queries": failed_queries_str,
        "validation_reason": validation_reason,
        # 로깅용 메타데이터
        "_meta_table_count": len(selected_tables),
        "_meta_failed_count": len(failed_queries),
    }


def _build_generate_sql_messages(inputs: dict) -> list:
    """LLM 메시지 생성"""
    return [
        SystemMessage(content=GENERATE_SQL_SYSTEM),
        HumanMessage(content=GENERATE_SQL_USER.format(
            intent=inputs["intent"],
            time_start=inputs["time_start"],
            time_end=inputs["time_end"],
            metric=inputs["metric"],
            condition=inputs["condition"],
            table_name=inputs["table_name"],
            columns=inputs["columns"],
            failed_queries=inputs["failed_queries"],
            validation_reason=inputs["validation_reason"],
        )),
    ]


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

    # 3. LLM 호출
    response = await llm_smart.ainvoke(messages)
    
    logger.info("TEXT_TO_SQL:generate_sql done")
    return {"generated_sql": response.content.strip(), "sql_guard_error": ""}


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
    # [역할] MCP 도구를 통해 SQL 실행
    # [입력] generated_sql
    # [출력] sql_result, sql_error
    sql = state.get("generated_sql", "")
    logger.info("TEXT_TO_SQL:execute_sql start")
    try:
        async with postgres_client() as client:
            result = await client.call_tool("execute_sql", {"query": sql})
            sql_result = json.loads(result)
        logger.info(
            "TEXT_TO_SQL:execute_sql ok rows=%s",
            len(sql_result) if isinstance(sql_result, list) else "non-list",
        )
        return {"sql_result": sql_result, "sql_error": ""}
    except Exception as e:
        logger.error("TEXT_TO_SQL:execute_sql error=%s", e)
        return {"sql_result": [], "sql_error": str(e)}


# ─────────────────────────────────────────
# Node 8: normalize_result
# ─────────────────────────────────────────

async def normalize_result(state: TextToSQLState) -> dict:
    # [역할] 실행 결과/에러를 표준 상태로 정규화
    # [입력] sql_result, sql_error
    # [출력] result_status, verdict, validation_reason, feedback_to_sql
    sql_error = state.get("sql_error")
    failed_queries = list(state.get("failed_queries", []) or [])
    current_sql = state.get("generated_sql", "")

    if sql_error:
        verdict, reason = classify_sql_error(sql_error)
        logger.error("TEXT_TO_SQL:normalize_result sql_error=%s verdict=%s", sql_error, verdict)
        
        if current_sql and current_sql not in failed_queries:
            failed_queries.append(current_sql)
            failed_queries = failed_queries[-3:]

        return {
            "result_status": "error",
            "verdict": verdict,
            "validation_reason": reason,
            "feedback_to_sql": reason,
            "failed_queries": failed_queries,
            "total_loops": state.get("total_loops", 0) + 1,
        }

    if not state.get("sql_result"):
        logger.info("TEXT_TO_SQL:normalize_result empty_result")
        return {
            "result_status": "empty",
            "failed_queries": failed_queries, # 현재 상태 유지
            # 결과가 없어도 쿼리 자체는 문법적으로 맞으므로 아직 failed_queries에 넣지 않음 (validate_llm에서 결정)
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
        logger.error("TEXT_TO_SQL:validate_llm parse_error=%s", error)
        # 파싱 실패 시 단순 폴백
        verdict = "OK" if state.get("sql_result") else "DATA_MISSING"
        return {
            "verdict": verdict,
            "validation_reason": "검증 파싱 실패",
        }

    verdict = parsed_json.get("verdict", "AMBIGUOUS")
    feedback = parsed_json.get("feedback_to_sql", "")
    hint = parsed_json.get("correction_hint", "")
    unnecessary = parsed_json.get("unnecessary_tables", []) or []

    failed_queries = list(state.get("failed_queries", []) or [])
    current_sql = state.get("generated_sql", "")
    if current_sql and current_sql not in failed_queries:
        failed_queries.append(current_sql)
        failed_queries = failed_queries[-3:]

    if unnecessary:
        current_tables = list(state.get("selected_tables", []) or [])
        filtered = [t for t in current_tables if t not in unnecessary]
        candidates = state.get("table_candidates", []) or []
        _, new_context = rebuild_context_from_candidates(candidates, filtered)
        if filtered:
            return {
                "selected_tables": filtered,
                "table_context": new_context,
                "verdict": "SQL_BAD",
                "feedback_to_sql": "불필요한 테이블을 제외하고 다시 작성하세요.",
                "failed_queries": failed_queries,
                "validation_retry_count": state.get("validation_retry_count", 0) + 1,
                "total_loops": state.get("total_loops", 0) + 1,
            }

    if verdict == "DATA_MISSING":
        question = state.get("user_question", "").lower()
        context = state.get("table_context", "").lower()
        
        # 보정 로직 상세화
        needs_ram = any(k in question for k in ["램", "ram", "메모리", "memory"])
        needs_cpu = any(k in question for k in ["cpu", "시피유", "프로세서"])
        needs_disk = any(k in question for k in ["디스크", "disk", "저장소"])
        
        has_ram = "metrics_memory" in context
        has_cpu = "metrics_cpu" in context
        has_disk = "metrics_disk" in context

        missing = []
        if needs_ram and has_ram and "metrics_memory" not in current_sql:
            missing.append("metrics_memory (RAM 지표)")
        if needs_cpu and has_cpu and "metrics_cpu" not in current_sql:
            missing.append("metrics_cpu (CPU 지표)")
        if needs_disk and has_disk and "metrics_disk" not in current_sql:
            missing.append("metrics_disk (디스크 지표)")

        if missing:
            verdict = "SQL_BAD"
            feedback = f"제공된 스키마에 {', '.join(missing)} 테이블이 존재함에도 쿼리에 포함되지 않았습니다. 반드시 해당 테이블을 사용하여 지표를 산출하세요."

    if verdict != "OK":
        # 피드백을 [이유]와 [개선 예시]로 구조화하여 에이전트 전달
        full_feedback = f"### 이전 시도 실패 원인\n{feedback}\n"
        if hint:
            full_feedback += f"\n### 올바른 쿼리 예시 및 힌트\n{hint}\n"
            
        logger.info("TEXT_TO_SQL:validate_llm verdict=%s feedback=%s", verdict, feedback)
        return {
            "verdict": verdict,
            "feedback_to_sql": full_feedback,
            "validation_reason": feedback, # 요약된 이유
            "failed_queries": failed_queries,
            "validation_retry_count": state.get("validation_retry_count", 0) + 1,
            "total_loops": state.get("total_loops", 0) + 1,
        }


    return {
        "verdict": "OK",
        "validation_reason": "",
        "feedback_to_sql": "",
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
            generated_sql=state.get("generated_sql", ""),
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
        "sql_result": state.get("sql_result", []),
        "generated_sql": state.get("generated_sql", ""),
    }
