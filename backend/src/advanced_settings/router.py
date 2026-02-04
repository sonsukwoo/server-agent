"""고급 설정 API 라우터."""

from fastapi import APIRouter, HTTPException
from typing import List, Any
from . import AlertService, AlertRuleCreate, AlertRuleResponse, AlertHistoryResponse

router = APIRouter(prefix="/advanced", tags=["Advanced Settings"])

@router.post("/rules", response_model=AlertRuleResponse)
async def create_rule(rule: AlertRuleCreate):
    """새 알림 규칙 등록 (트리거 생성 포함)"""
    try:
        return await AlertService.create_rule(rule)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/rules", response_model=List[AlertRuleResponse])
async def list_rules():
    """등록된 규칙 목록 조회"""
    try:
        return await AlertService.list_rules()
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.delete("/rules/{rule_id}")
async def delete_rule(rule_id: int):
    """규칙 삭제 (트리거 제거 포함)"""
    try:
        success = await AlertService.delete_rule(rule_id)
        if not success:
            raise HTTPException(status_code=404, detail="Rule not found")
        return {"status": "success"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/alerts", response_model=List[AlertHistoryResponse])
async def list_alerts():
    """발생한 알림 이력 조회"""
    try:
        return await AlertService.list_alerts()
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.delete("/alerts/{alert_id}")
async def delete_alert(alert_id: int):
    """알림 이력 삭제"""
    try:
        await AlertService.delete_alert(alert_id)
        return {"status": "success"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
