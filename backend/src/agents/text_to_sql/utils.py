"""Text-to-SQL 에이전트 유틸리티 함수"""
import json
import re
from datetime import datetime
from zoneinfo import ZoneInfo

from .constants import TIMEZONE, EXPAND_STEP, ELBOW_THRESHOLD, MIN_KEEP, MAX_KEEP


def get_current_time() -> str:
    """현재 시간을 ISO 8601 문자열로 반환"""
    return datetime.now(ZoneInfo(TIMEZONE)).isoformat()


def get_now() -> datetime:
    """현재 시간을 datetime 객체로 반환 (타임존 포함)"""
    return datetime.now(ZoneInfo(TIMEZONE))


def parse_json_from_llm(text: str) -> tuple[dict | None, str | None]:
    """LLM 응답에서 JSON 블록을 안전하게 추출하고 파싱"""
    try:
        match = re.search(r"```(?:json)?\s*([\s\S]*?)```", text)
        if match:
            text = match.group(1)
        return json.loads(text.strip()), None
    except json.JSONDecodeError as e:
        return None, f"JSON 파싱 실패: {e}. 원본: {text[:100]}..."


def normalize_sql(sql: str) -> str:
    """SQL 코드 블록 제거 및 안전 규칙 적용"""
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
    """선택된 테이블 목록을 LLM 프롬프트용 컨텍스트 문자열로 변환"""
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


def rebuild_context_from_candidates(candidates: list[dict], selected_names: list[str]) -> tuple[list[dict], str]:
    """후보 목록에서 선택된 테이블을 다시 구성해 컨텍스트를 재생성"""
    selected = [t for t in candidates if t.get("table_name") in selected_names]
    return selected, build_table_context(selected)


def classify_sql_error(sql_error: str) -> tuple[str, str]:
    """SQL 에러 메시지를 규칙 기반으로 분류해 verdict/사유 반환"""
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
    """캐시된 후보 중 다음 확장 배치를 반환"""
    end = min(len(candidates), offset + EXPAND_STEP)
    return candidates[offset:end]


def apply_elbow_cut(scored: list[dict]) -> list[dict]:
    """점수 급락 지점(엘보)을 기준으로 후보를 컷"""
    if not scored:
        return []
    scored = sorted(scored, key=lambda x: x["score"], reverse=True)
    cut_idx = len(scored)
    for i in range(len(scored) - 1):
        gap = scored[i]["score"] - scored[i + 1]["score"]
        if gap >= ELBOW_THRESHOLD:
            cut_idx = i + 1
            break
    kept = scored[:cut_idx]
    if len(kept) < MIN_KEEP:
        kept = scored[:min(MIN_KEEP, len(scored))]
    if len(kept) > MAX_KEEP:
        kept = kept[:MAX_KEEP]
    return kept
