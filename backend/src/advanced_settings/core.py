"""
[DEPRECATED] advanced_settings.core
이 모듈은 하위 호환성을 위해 유지됩니다. 
대신 src.advanced_settings 에서 직접 import 하거나 
schemas, service, listener 모듈을 각각 이용하세요.
"""
from .schemas import AlertRuleCreate, AlertRuleResponse, AlertHistoryResponse
from .templates import TRIGGER_FUNC_TEMPLATE, TRIGGER_CREATE_TEMPLATE, TRIGGER_DROP_TEMPLATE
from .service import AlertService
from .listener import AlertListener

__all__ = [
    "AlertRuleCreate",
    "AlertRuleResponse",
    "AlertHistoryResponse",
    "TRIGGER_FUNC_TEMPLATE",
    "TRIGGER_CREATE_TEMPLATE",
    "TRIGGER_DROP_TEMPLATE",
    "AlertService",
    "AlertListener"
]
