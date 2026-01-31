"""Text-to-SQL 에이전트 State 정의"""
from typing import TypedDict, Optional


class ParsedRequest(TypedDict, total=False):
    """구조화된 요구사항"""
    intent: str                  # 의도 (예: "find_processes_when_ram_high")
    time_range: dict             # {"start": str, "end": str, "timezone": str}
    metric: Optional[str]        # 메트릭 (예: "ram_usage", "cpu_usage")
    condition: Optional[str]     # 조건 (예: "> 80%")
    output: Optional[str]        # 출력 형태 (예: "process_list", "summary")


class TextToSQLState(TypedDict, total=False):
    """Text-to-SQL 에이전트 상태"""
    
    # ═══════════════════════════════════════════
    # 입력
    # ═══════════════════════════════════════════
    user_question: str               # 원본 자연어 질문
    
    # ═══════════════════════════════════════════
    # Step 1: 구조화 (parse_request)
    # ═══════════════════════════════════════════
    parsed_request: ParsedRequest    # LLM이 구조화한 요구사항
    
    # ═══════════════════════════════════════════
    # Step 2: 검증 (validate_request)
    # ═══════════════════════════════════════════
    is_request_valid: bool           # 요구사항 검증 통과 여부
    request_error: str               # 검증 실패 시 에러 메시지
    
    # ═══════════════════════════════════════════
    # Step 3-4: 테이블/SQL
    # ═══════════════════════════════════════════
    table_list: list[dict]           # 전체 테이블 목록
    selected_table: str              # 선택된 테이블명
    is_table_valid: bool             # 테이블 선택 성공 여부
    table_error: str                 # 테이블 선택 실패 시 에러 메시지
    table_schema: dict               # 선택된 테이블 스키마
    generated_sql: str               # 생성된 SQL 쿼리
    
    # ═══════════════════════════════════════════
    # Step 5: 실행 (execute_sql)
    # ═══════════════════════════════════════════
    sql_result: list[dict]           # SQL 실행 결과
    sql_error: str                   # SQL 에러 메시지
    
    # ═══════════════════════════════════════════
    # Step 6: 검증/재시도 (validate_result)
    # ═══════════════════════════════════════════
    is_valid: bool                   # 결과 검증 통과 여부
    validation_reason: str           # 검증 실패 사유
    retry_count: int                 # 재시도 횟수 (최대 3)
    
    # ═══════════════════════════════════════════
    # Step 7: 보고서 (generate_report)
    # ═══════════════════════════════════════════
    report: str                      # 최종 보고서
    suggested_actions: list[str]     # 권장 액션 목록
