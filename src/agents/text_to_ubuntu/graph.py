"""Text-to-Ubuntu 에이전트 그래프 (LangGraph)"""
from langgraph.graph import StateGraph, END
from langgraph.checkpoint.memory import MemorySaver
from typing import TypedDict, Optional

class UbuntuAgentState(TypedDict):
    """Text-to-Ubuntu 에이전트 상태"""
    user_request: str
    generated_command: str
    risk_level: str  # "safe", "caution", "danger"
    user_confirmed: Optional[bool]
    execution_result: str
    final_answer: str

def generate_command(state: UbuntuAgentState) -> UbuntuAgentState:
    """LLM: 명령어 생성"""
    # TODO: 자연어 요청을 명령어로 변환
    return {"generated_command": ""}

def classify_risk(state: UbuntuAgentState) -> str:
    """MCP Tool: 위험도 분류"""
    # TODO: MCP classify_risk Tool 호출
    # 위험도에 따라 분기
    risk = state.get("risk_level", "safe")
    if risk == "safe":
        return "execute"
    else:
        return "ask_confirmation"

def ask_confirmation(state: UbuntuAgentState) -> UbuntuAgentState:
    """사용자 확인 요청 (Human-in-the-Loop)"""
    # TODO: 사용자에게 확인 요청
    return {"user_confirmed": None}

def check_user_response(state: UbuntuAgentState) -> str:
    """사용자 응답 확인"""
    if state.get("user_confirmed") is True:
        return "execute"
    else:
        return "cancel"

def execute_command(state: UbuntuAgentState) -> UbuntuAgentState:
    """MCP Tool: 명령어 실행"""
    # TODO: MCP execute_command Tool 호출
    return {"execution_result": ""}

def cancel(state: UbuntuAgentState) -> UbuntuAgentState:
    """취소 처리"""
    return {"final_answer": "작업이 취소되었습니다"}

def summarize(state: UbuntuAgentState) -> UbuntuAgentState:
    """결과 요약"""
    # TODO: 실행 결과를 자연어로 요약
    return {"final_answer": ""}

# 그래프 구성
def create_ubuntu_agent():
    """Text-to-Ubuntu 에이전트 그래프 생성"""
    workflow = StateGraph(UbuntuAgentState)
    
    # 노드 추가
    workflow.add_node("generate_command", generate_command)
    workflow.add_node("ask_confirmation", ask_confirmation)
    workflow.add_node("execute", execute_command)
    workflow.add_node("cancel", cancel)
    workflow.add_node("summarize", summarize)
    
    # 엣지 연결
    workflow.set_entry_point("generate_command")
    
    # 위험도에 따른 분기
    workflow.add_conditional_edges(
        "generate_command",
        classify_risk,
        {
            "execute": "execute",
            "ask_confirmation": "ask_confirmation"
        }
    )
    
    # 사용자 응답에 따른 분기
    workflow.add_conditional_edges(
        "ask_confirmation",
        check_user_response,
        {
            "execute": "execute",
            "cancel": "cancel"
        }
    )
    
    workflow.add_edge("execute", "summarize")
    workflow.add_edge("cancel", END)
    workflow.add_edge("summarize", END)
    
    # Human-in-the-Loop을 위한 중단점 설정
    return workflow.compile(
        checkpointer=MemorySaver(),
        interrupt_before=["ask_confirmation"]
    )
