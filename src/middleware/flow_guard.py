"""흐름 제어 미들웨어 - 무한루프/타임아웃 방지"""
import time
from typing import Optional

class FlowGuard:
    """에이전트 실행 흐름을 제어하는 미들웨어"""
    
    MAX_RETRIES = 3
    MAX_EXECUTION_TIME = 30  # 초
    
    def __init__(self):
        self.retry_count = 0
        self.start_time: Optional[float] = None
        self.previous_queries = []
    
    def start(self):
        """실행 시작"""
        self.start_time = time.time()
        self.retry_count = 0
        self.previous_queries = []
    
    def check_timeout(self) -> tuple[bool, str]:
        """타임아웃 체크"""
        if self.start_time is None:
            return True, ""
        
        elapsed = time.time() - self.start_time
        if elapsed > self.MAX_EXECUTION_TIME:
            return False, f"실행 시간 초과 ({self.MAX_EXECUTION_TIME}초)"
        
        return True, ""
    
    def check_retry(self, query: str) -> tuple[bool, str]:
        """재시도 횟수 및 중복 쿼리 체크"""
        self.retry_count += 1
        
        if self.retry_count > self.MAX_RETRIES:
            return False, f"최대 재시도 횟수 초과 ({self.MAX_RETRIES}회)"
        
        # 동일 쿼리 반복 감지
        if query in self.previous_queries:
            return False, "동일한 쿼리가 반복되고 있습니다"
        
        self.previous_queries.append(query)
        return True, ""
