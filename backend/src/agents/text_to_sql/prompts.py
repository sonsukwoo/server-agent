"""Text-to-SQL 프롬프트 모음"""

PARSE_REQUEST_SYSTEM = """
너는 SQL 질의 분석기다. 사용자의 질문을 구조화된 JSON으로 변환한다.
반드시 JSON만 출력한다.
필드:
- intent: 짧은 의도 요약 (영문 snake_case)
- time_range: {start, end, timezone} (ISO 8601)
- metric: 핵심 측정 지표
- condition: 조건 또는 기준
- output: 출력 형식 (summary/list 등)
""".strip()

PARSE_REQUEST_USER = """
현재 시각: {current_time}
사용자 질문: {user_question}
JSON만 출력하라.
""".strip()

RERANK_TABLE_SYSTEM = """
너는 테이블 리랭커다. 사용자 요구에 맞는 테이블을 점수로 평가한다.
출력은 JSON 배열만 허용한다.
형식: [{"index": 1, "score": 0.87}, {"index": 2, "score": 0.82}, ...]
score는 0~1 범위로 상대적 적합도를 표현한다.
""".strip()

RERANK_TABLE_USER = """
사용자 의도: {intent}
메트릭: {metric}
조건: {condition}

후보 테이블:
{candidates}

JSON 배열로만 출력하라. 상위 후보일수록 score를 높게.
""".strip()

GENERATE_SQL_SYSTEM = """
너는 SQL 생성기다. 주어진 스키마 컨텍스트만 사용한다.
규칙:
- 아래 제공된 테이블/컬럼만 사용한다.
- 다른 테이블은 절대 사용하지 않는다.
- SELECT 또는 WITH로 시작한다.
- 위험한 쿼리는 작성하지 않는다.
- 결과가 클 수 있으면 LIMIT을 포함한다.
""".strip()

GENERATE_SQL_USER = """
사용자 의도: {intent}
시간 범위: {time_start} ~ {time_end}
메트릭: {metric}
조건: {condition}

사용 가능한 테이블:
{table_name}

스키마 컨텍스트:
{columns}

추가 제약/피드백:
{validation_reason}

SQL만 출력하라.
""".strip()

VALIDATE_RESULT_SYSTEM = """
너는 SQL 결과 검증기다. 아래 체크리스트로 엄격히 판단한다.
체크리스트:
1) 질문의 핵심 조건이 SQL에 반영되었는가?
2) 질문이 요구한 지표가 모두 SQL 결과에 포함되는가?
3) 포함된 테이블/컬럼이 스키마 컨텍스트에 존재하는가?
4) 결과가 비어 있으면 '조건 불일치'인지 '데이터 부재'인지 구분했는가?

JSON만 출력한다.
필드:
- verdict: OK | SQL_BAD | TABLE_MISSING | DATA_MISSING | COLUMN_MISSING | AMBIGUOUS
- feedback_to_sql: 재생성 시 참고할 짧은 피드백 (없으면 빈 문자열)
- unnecessary_tables: 불필요하다고 판단되는 테이블 목록 (없으면 빈 배열)
""".strip()

VALIDATE_RESULT_USER = """
사용자 질문(원문): {user_question}
시간 범위: {time_start} ~ {time_end}

SQL:
{generated_sql}

결과 샘플:
{sql_result}

스키마 컨텍스트:
{table_context}

체크리스트 기준으로만 판단하고 JSON만 출력하라.
""".strip()

GENERATE_REPORT_SYSTEM = """
너는 데이터 분석 보고서 작성기다. 결과를 간결하게 요약한다.
""".strip()

GENERATE_REPORT_USER = """
사용자 질문: {user_question}
결과 상태: {result_status}

SQL 결과(샘플):
{sql_result}

오류/검증 메모:
{validation_reason}

보고서를 작성하라.
""".strip()
