from datetime import datetime, timedelta
import logging
from typing import Tuple, Dict, Any

from config.settings import settings
from src.agents.text_to_sql.utils import get_now

logger = logging.getLogger("PARSED_REQUEST_GUARD")

# 상수: 허용 오차 (분)
FUTURE_TOLERANCE_MINUTES = 5

class ParsedRequestGuard:
    """
    LLM이 파싱한 요청(parsed_request)의 구조적 유효성을 검증하고,
    필요한 경우 기본값(시간 범위 등)을 채워넣는 미들웨어.
    """

    @staticmethod
    def validate(parsed: Dict[str, Any]) -> Tuple[bool, str, Dict[str, Any]]:
        """
        요청 검증 및 보정 메인 로직
        :param parsed: LLM이 생성한 파싱 결과 (dict)
        :return: (is_valid, error_reason, normalized_parsed)
        """
        if not isinstance(parsed, dict):
            return False, "Parsed result must be a dictionary", parsed

        # 1. intent 필수 확인
        if not parsed.get("intent"):
            return False, "Missing 'intent' field", parsed

        # 2. Time Range 처리 (Null이면 기본값, 아니면 검증)
        time_range = parsed.get("time_range")
        
        if (not time_range) or (
            isinstance(time_range, dict)
            and not time_range.get("start")
            and not time_range.get("end")
        ):
            # 기본값 적용 (오늘 00:00 ~ 현재)
            default_tr = ParsedRequestGuard._create_default_time_range()
            parsed["time_range"] = default_tr
            logger.debug("ParsedRequestGuard: time_range was null, defaulted to %s", default_tr)
        else:
            # 구조 및 값 검증
            if not isinstance(time_range, dict):
                return False, "time_range must be a dictionary", parsed
            
            start_str = time_range.get("start")
            end_str = time_range.get("end")
            
            if not start_str or not end_str:
                return False, "time_range must have 'start' and 'end' fields", parsed

            # 타임존 보정 (settings.tz 사용)
            if not time_range.get("timezone"):
                time_range["timezone"] = settings.tz

            # 시간 유효성 검증 (미래 차단 등)
            is_valid_time, time_error = ParsedRequestGuard._validate_time_values(start_str, end_str)
            if not is_valid_time:
                return False, time_error, parsed
            
            # 파싱된 값으로 업데이트 (포맷 보정 등 가능성 고려)
            # 여기서는 검증만 통과하면 원본 유지

        return True, "", parsed

    @staticmethod
    def _create_default_time_range() -> Dict[str, str]:
        """기본 시간 범위 생성 (오늘 00:00:00 ~ 현재)"""
        now = get_now()  # timezone-aware datetime (utils.py)
        
        # 오늘 00:00:00
        start_dt = now.replace(hour=0, minute=0, second=0, microsecond=0)
        
        return {
            "start": start_dt.isoformat(),
            "end": now.isoformat(),
            "timezone": settings.tz
        }

    @staticmethod
    def _validate_time_values(start_str: str, end_str: str) -> Tuple[bool, str]:
        """시간 값의 논리적 타당성 검증 (미래 차단, 역전 방지)"""
        try:
            # ISO format 파싱 (Z 처리 포함)
            # 3.11 이전 버전 호환성을 위해 replace('Z', '+00:00') 처리
            s = str(start_str).replace("Z", "+00:00")
            e = str(end_str).replace("Z", "+00:00")
            
            # 6자리 이상 소수점(마이크로초) 잘림 방지는 여기서 고려 안함 (LLM이 보통 초단위)
            # 하지만 필요시 dateutil.parser 사용 가능
            
            start_dt = datetime.fromisoformat(s)
            end_dt = datetime.fromisoformat(e)
            now = get_now()

            # 타임존 정보가 없는 경우 현재 타임존 할당 (비교를 위해)
            if start_dt.tzinfo is None:
                start_dt = start_dt.replace(tzinfo=now.tzinfo)
            if end_dt.tzinfo is None:
                end_dt = end_dt.replace(tzinfo=now.tzinfo)

            # 1. 미래 시간 차단 (허용 오차 적용)
            future_limit = now + timedelta(minutes=FUTURE_TOLERANCE_MINUTES)
            
            if start_dt > future_limit:
                 return False, f"Start time ({start_str}) is in the future."
            
            if end_dt > future_limit:
                 return False, f"End time ({end_str}) is in the future."

            # 2. 역전 검사
            if start_dt > end_dt:
                return False, "Start time is later than End time."

            return True, ""

        except ValueError as e:
            return False, f"Invalid time format: {str(e)}"
