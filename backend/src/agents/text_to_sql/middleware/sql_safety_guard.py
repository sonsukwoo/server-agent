"""SQL 안전성 검사 미들웨어."""

import re

class SqlOutputGuard:
    """
    LLM이 생성한 SQL이 안전하고 유효한지 검증하는 가드 클래스.
    DML/DDL을 차단하고 SELECT 쿼리만 허용합니다.
    """

    FORBIDDEN_KEYWORDS = [
        "DROP", "DELETE", "UPDATE", "INSERT", "ALTER", "TRUNCATE",
        "GRANT", "REVOKE", "CREATE", "REPLACE"
    ]

    def validate_sql(self, sql: str) -> tuple[bool, str]:
        """
        SQL 검증 및 정규화
        :return: (is_valid, error_message - if valid is false, normalized_sql - if valid is true)
        Note: The return signature in the plan was (bool, str). 
              If valid: returns (True, normalized_sql)
              If invalid: returns (False, error_message)
        """
        if not sql:
            return False, "SQL이 비어있습니다."

        # 0. Markdown Code Block 제거
        # ```sql ... ``` 또는 ``` ... ``` 패턴이 있으면 내부 내용만 추출
        match = re.search(r"```(?:sql)?\s*(.*?)```", sql, re.DOTALL | re.IGNORECASE)
        if match:
            sql = match.group(1)

        # 1. 정규화 (세미콜론, 백틱 등 제거)
        normalized = sql.strip().strip(';').strip()
        normalized_check = normalized.upper()

        # 2. SELECT로 시작하는지 확인 (WITH ... SELECT도 허용 가능하지만 일단 엄격하게 체크)
        #    간단한 구현을 위해 시작 단어 체크. (공백 무시)
        if not normalized_check.startswith("SELECT") and not normalized_check.startswith("WITH"):
            return False, "허용되지 않는 쿼리 형식입니다. (SELECT 또는 WITH로 시작해야 함)"

        # 3. 금지어 포함 여부 확인 (단어 경계 체크)
        for kw in self.FORBIDDEN_KEYWORDS:
            # 단순 포함이 아니라 단어 단위로 체크해야 함 (예: SELECT ... FROM ... WHERE id='INSERT_ID' 는 허용)
            # \b 키워드 \b 패턴 사용
            pattern = re.compile(rf"\b{kw}\b", re.IGNORECASE)
            if pattern.search(normalized):
                return False, f"실행할 수 없는 위험한 키워드가 포함되어 있습니다: {kw}"

        return True, normalized
