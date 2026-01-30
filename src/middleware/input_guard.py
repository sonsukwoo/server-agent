"""입력 검증 미들웨어 - 프롬프트 인젝션 방지"""

class InputGuard:
    """사용자 입력을 검증하는 미들웨어"""
    
    BLOCKED_PATTERNS = [
        "ignore previous instructions",
        "위 지시를 무시하고",
        "forget all previous",
        "system prompt",
    ]
    
    MAX_LENGTH = 1000
    
    @classmethod
    def validate(cls, user_input: str) -> tuple[bool, str]:
        """
        입력 검증
        
        Returns:
            (is_valid, error_message)
        """
        # 길이 체크
        if len(user_input) > cls.MAX_LENGTH:
            return False, f"입력이 너무 깁니다 (최대 {cls.MAX_LENGTH}자)"
        
        # 프롬프트 인젝션 패턴 체크
        user_input_lower = user_input.lower()
        for pattern in cls.BLOCKED_PATTERNS:
            if pattern in user_input_lower:
                return False, "허용되지 않는 입력 패턴이 감지되었습니다"
        
        return True, ""
