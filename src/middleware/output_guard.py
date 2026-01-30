"""출력 검증 미들웨어 - SQL/명령어 안전성 체크"""
import re

class OutputGuard:
    """LLM 출력을 검증하는 미들웨어"""
    
    # SQL 위험 키워드
    SQL_DANGEROUS_KEYWORDS = [
        "DROP", "DELETE", "UPDATE", "INSERT", "ALTER", 
        "TRUNCATE", "CREATE", "GRANT", "REVOKE"
    ]
    
    # 명령어 위험 키워드
    CMD_DANGEROUS_KEYWORDS = [
        "rm -rf", "shutdown", "reboot", "mkfs", 
        "dd if=", "> /dev/", ":(){ :|:& };:"
    ]
    
    @classmethod
    def validate_sql(cls, sql: str) -> tuple[bool, str]:
        """
        SQL 안전성 검증 (SELECT만 허용)
        
        Returns:
            (is_valid, error_message)
        """
        sql_upper = sql.strip().upper()
        
        # SELECT로 시작하는지 확인
        if not sql_upper.startswith("SELECT"):
            return False, "SELECT 쿼리만 실행 가능합니다"
        
        # 위험한 키워드 체크
        for keyword in cls.SQL_DANGEROUS_KEYWORDS:
            if keyword in sql_upper:
                return False, f"위험한 SQL 키워드가 감지되었습니다: {keyword}"
        
        return True, ""
    
    @classmethod
    def validate_command(cls, command: str) -> tuple[bool, str, str]:
        """
        명령어 안전성 검증
        
        Returns:
            (is_valid, risk_level, error_message)
            risk_level: "safe", "caution", "danger"
        """
        # 위험 명령어 체크
        for keyword in cls.CMD_DANGEROUS_KEYWORDS:
            if keyword in command:
                return True, "danger", f"위험한 명령어: {keyword}"
        
        # 주의 명령어 체크
        if any(kw in command for kw in ["restart", "stop", "kill"]):
            return True, "caution", "시스템 변경 명령어"
        
        return True, "safe", ""
