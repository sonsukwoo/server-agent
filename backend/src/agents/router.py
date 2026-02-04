"""질의 라우팅(설명형 vs SQL 실행) 판별 및 설명 응답 생성."""

import json
import logging
from typing import Any, Dict

from langchain_openai import ChatOpenAI

from config.settings import settings

logger = logging.getLogger("TEXT_TO_SQL_ROUTER")

ROUTER_SYSTEM_PROMPT = """
너는 사용자의 질문이 SQL 실행이 필요한지, 아니면 설명형 답변인지 분류하는 라우터다.
반드시 JSON만 출력한다.

분류 기준:
- needs_sql: 데이터 조회/집계/순위/필터/기간 분석이 필요한 질문
- explain: 방금 만든 쿼리/결과/작업 이유 설명, 해석, 요약, 왜 이렇게 했는지 등의 질문
- clarifying: 질문이 너무 모호해서 추가 질문이 필요함

필드:
- intent: "needs_sql" | "explain" | "clarifying"
- reason: 한 문장 근거
""".strip()

ROUTER_USER_PROMPT = """
사용자 질문: {question}

이전 대화 컨텍스트:
{context}
""".strip()

EXPLAIN_SYSTEM_PROMPT = """
너는 데이터 분석 보조 설명가다. 사용자의 질문이 "왜 이렇게 했는지" 같은 설명형 요청이면,
이전 대화 컨텍스트를 근거로 짧고 명확하게 설명한다.
규칙:
- SQL을 새로 만들거나 실행하지 않는다.
- 이전 대화 컨텍스트에 없는 내용은 추측하지 않는다.
- 필요한 정보가 없으면 어떤 정보가 필요한지 짧게 요청한다.
- 컨텍스트에 실행된 SQL, 결과 요약, 작업 근거, 적용 제약이 있으면 이를 근거로 설명한다.
""".strip()

EXPLAIN_USER_PROMPT = """
사용자 질문: {question}

이전 대화 컨텍스트:
{context}
""".strip()

CONSTRAINT_SYSTEM_PROMPT = """
너는 사용자의 수정 지시를 SQL 제약으로 추출하는 도우미다.
반드시 JSON만 출력한다.

목표:
- 사용자가 "이렇게 말고 ~ 방식으로", "다른 방식으로", "이 기준을 적용해서" 같은 요청을 하면
  SQL 생성에 반영할 제약을 한두 문장으로 추출한다.
- 추출할 제약이 없다면 빈 문자열을 반환한다.

필드:
- constraints: string (없으면 "")
- reason: 한 문장 근거
""".strip()

CONSTRAINT_USER_PROMPT = """
사용자 질문: {question}

이전 대화 컨텍스트:
{context}
""".strip()


def _safe_json_load(text: str) -> Dict[str, Any]:
    try:
        return json.loads(text)
    except Exception:
        # Handle fenced JSON: ```json ... ```
        cleaned = text.strip()
        if cleaned.startswith("```"):
            cleaned = cleaned.strip("`")
            cleaned = cleaned.replace("json", "", 1).strip()
        try:
            return json.loads(cleaned)
        except Exception:
            return {}


async def classify_intent(question: str, context: str) -> Dict[str, Any]:
    llm = ChatOpenAI(model_name=settings.model_fast, temperature=0)
    prompt = ROUTER_USER_PROMPT.format(question=question, context=context or "(없음)")
    result = await llm.ainvoke([
        {"role": "system", "content": ROUTER_SYSTEM_PROMPT},
        {"role": "user", "content": prompt},
    ])
    data = _safe_json_load(result.content)
    intent = data.get("intent")
    if intent not in {"needs_sql", "explain", "clarifying"}:
        logger.warning("Router fallback to needs_sql. raw=%s", result.content)
        return {"intent": "needs_sql", "reason": "fallback"}
    return {"intent": intent, "reason": data.get("reason", "")}


async def build_explain_report(question: str, context: str) -> str:
    llm = ChatOpenAI(model_name=settings.model_fast, temperature=0)
    prompt = EXPLAIN_USER_PROMPT.format(question=question, context=context or "(없음)")
    result = await llm.ainvoke([
        {"role": "system", "content": EXPLAIN_SYSTEM_PROMPT},
        {"role": "user", "content": prompt},
    ])
    return result.content.strip()


async def extract_constraints(question: str, context: str) -> Dict[str, Any]:
    llm = ChatOpenAI(model_name=settings.model_fast, temperature=0)
    prompt = CONSTRAINT_USER_PROMPT.format(question=question, context=context or "(없음)")
    result = await llm.ainvoke([
        {"role": "system", "content": CONSTRAINT_SYSTEM_PROMPT},
        {"role": "user", "content": prompt},
    ])
    data = _safe_json_load(result.content)
    constraints = data.get("constraints", "")
    if isinstance(constraints, str):
        return {
            "constraints": constraints.strip(),
            "reason": data.get("reason", "")
        }
    return {"constraints": "", "reason": ""}
