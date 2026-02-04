"""파싱된 요청 검증 미들웨어."""

from datetime import datetime, timedelta
import logging
from typing import Tuple, Dict, Any, Optional

from config.settings import settings
from src.agents.text_to_sql.common.utils import get_now

logger = logging.getLogger("PARSED_REQUEST_GUARD")

# 상수: 허용 오차 (분)
FUTURE_TOLERANCE_MINUTES = 5

class ParsedRequestGuard:
    """
    LLM이 파싱한 요청(parsed_request)의 구조적 유효성을 검증하고,
    필요한 경우 기본값(시간 범위 등)을 채워넣는 미들웨어.
    """

    @staticmethod
    def validate(parsed: Dict[str, Any]) -> Tuple[bool, str, Dict[str, Any], Optional[str]]:
        """
        요청 검증 및 보정 메인 로직
        :param parsed: LLM이 생성한 파싱 결과 (dict)
        :return: (is_valid, error_reason, normalized_parsed, adjustment_info)
        """
        if not isinstance(parsed, dict):
            return False, "Parsed result must be a dictionary", parsed, None

        # 1. intent 필수 확인
        if not parsed.get("intent"):
            return False, "Missing 'intent' field", parsed, None

        adjustment_info = None

        # 2. Time Range 처리 (Null이면 기본값, 아니면 검증)
        time_range = parsed.get("time_range")
        is_followup = parsed.get("is_followup", False)
        
        if (not time_range) or (
            isinstance(time_range, dict)
            and not time_range.get("start")
            and not time_range.get("end")
        ):
            # 후속 질문인 경우: 시간 범위를 강제로 '전체'로 설정하지 않음 (이전 쿼리 상속 유도)
            if is_followup:
                # 상속임을 명시하기 위해 비어있는 상태 유지 또는 명시적 플래그 (여기서는 빈 dict + timezone)
                parsed["time_range"] = {"timezone": settings.tz, "inherit": True}
                adjustment_info = "후속 질문: 시간 범위가 명시되지 않아 이전 쿼리의 시간을 상속합니다."
                logger.debug("ParsedRequestGuard: followup detected, time_range set to inherit")
            else:
                # 일반 질문인 경우: 기본값 적용 (전체 조회)
                default_tr = ParsedRequestGuard._create_default_time_range()
                parsed["time_range"] = default_tr
                adjustment_info = "시간 범위가 지정되지 않아 전체 기간 조회를 시작합니다."
                logger.debug("ParsedRequestGuard: time_range was null, defaulted to all_time")
        else:
            # 구조 및 값 검증
            if not isinstance(time_range, dict):
                return False, "time_range must be a dictionary", parsed, None
            
            start_str = time_range.get("start")
            end_str = time_range.get("end")
            
            if not start_str or not end_str:
                return False, "time_range must have 'start' and 'end' fields", parsed, None

            # 타임존 보정 (settings.tz 사용)
            if not time_range.get("timezone"):
                time_range["timezone"] = settings.tz

            # 시간 유효성 검증 (미래 차단 등) - Auto Clipping 적용
            is_valid_time, time_error, adjusted_end = ParsedRequestGuard._validate_time_values(start_str, end_str)
            if not is_valid_time:
                return False, time_error, parsed, None
            
            # Auto Clipping 적용: End time이 조정되었다면 업데이트
            if adjusted_end:
                 parsed["time_range"]["end"] = adjusted_end.isoformat()
                 adjustment_info = f"미래 시점({end_str})이 포함되어 현재 시각으로 조정했습니다."
                 logger.info("ParsedRequestGuard: Adjusted future end time from %s to %s", end_str, parsed["time_range"]["end"])
            
            # 파싱된 값으로 업데이트 (포맷 보정 등 가능성 고려)
            # 여기서는 검증만 통과하면 원본 유지

        return True, "", parsed, adjustment_info

    @staticmethod
    def _create_default_time_range() -> Dict[str, Any]:
        """기본 시간 범위 생성 (전체 조회 - 시간 필터 생략)"""
        # all_time이 True면 SQL 생성 시 시간 조건을 붙이지 않음
        return {
            "all_time": True,
            "timezone": settings.tz
        }

    @staticmethod
    def _validate_time_values(start_str: str, end_str: str) -> Tuple[bool, str, Any]:
        """
        시간 값의 논리적 타당성 검증 (미래 차단, 역전 방지)
        - 미래 End Time에 대해서는 현재 시간으로 Clipping 수행
        """
        try:
            # ISO format 파싱 (Z 처리 포함)
            # 3.11 이전 버전 호환성을 위해 replace('Z', '+00:00') 처리
            s = str(start_str).replace("Z", "+00:00")
            e = str(end_str).replace("Z", "+00:00")
            
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
            
            # Start time이 미래인 경우는 여전히 에러 처리 (시작 자체가 미래면 조회 불가)
            if start_dt > future_limit:
                 return False, f"Start time ({start_str}) is in the future.", None
            
            adjusted_end = None
            # End time이 미래인 경우 -> 현재 시간으로 Clipping
            if end_dt > future_limit:
                 # 단, 미래라도 시작 시간보다는 뒤여야 함 (Clipping 후 역전되면 안됨)
                 # 하지만 여기서는 '현재'로 당기는 것이므로, start가 현재보다 과거라면 문제 없음.
                 # 만약 start도 거의 현재라면? -> 역전 검사에서 걸러짐
                 adjusted_end = now
                 end_dt = now # 역전 검사를 위해 업데이트

            # 2. 역전 검사
            if start_dt > end_dt:
                return False, "Start time is later than End time.", None

            return True, "", adjusted_end

        except ValueError as e:
            return False, f"Invalid time format: {str(e)}", None
