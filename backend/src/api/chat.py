"""채팅 세션 및 메시지 관리 API."""

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import List, Optional, Any, Dict
from src.db.db_manager import db_manager

router = APIRouter(prefix="/chat", tags=["chat"])

# --- Models ---
class SessionCreate(BaseModel):
    title: str = "New Chat"

class MessageCreate(BaseModel):
    role: str
    content: str
    payload_json: Optional[Dict[str, Any]] = None

class SessionResponse(BaseModel):
    id: str
    title: str
    created_at: str
    updated_at: str

class MessageResponse(BaseModel):
    id: str
    role: str
    content: str
    payload_json: Optional[Dict[str, Any]]
    created_at: str

class SessionDetailResponse(SessionResponse):
    messages: List[MessageResponse]

# --- Endpoints ---

@router.get("/sessions", response_model=List[SessionResponse])
async def list_sessions():
    """모든 채팅 세션 조회 (최신순)."""
    return await db_manager.list_sessions()

@router.post("/sessions", response_model=SessionResponse)
async def create_session(session: SessionCreate):
    """새 세션 생성."""
    return await db_manager.create_session(session.title)

@router.get("/sessions/{session_id}", response_model=SessionDetailResponse)
async def get_session(session_id: str):
    """세션 상세 조회 (메시지 포함)."""
    session = await db_manager.get_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    return session

@router.delete("/sessions/{session_id}")
async def delete_session(session_id: str):
    """세션 삭제."""
    deleted = await db_manager.delete_session(session_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Session not found")
    return {"status": "ok"}

@router.post("/sessions/{session_id}/messages")
async def save_message(session_id: str, message: MessageCreate):
    """메시지 저장."""
    # 세션 존재 여부 확인
    session = await db_manager.get_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
        
    result = await db_manager.save_message(
        session_id=session_id,
        role=message.role,
        content=message.content,
        payload=message.payload_json
    )
    return result
