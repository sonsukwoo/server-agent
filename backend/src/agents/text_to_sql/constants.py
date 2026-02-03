"""Text-to-SQL 에이전트 상수 및 설정"""
from config.settings import settings

# ─────────────────────────────────────────
# 기본 설정 (루프 가드 포함)
# - RETRIEVE_K: 벡터 검색에서 확보할 후보 테이블 수
# - TOP_K: LLM에게 제공할 초기 테이블 수 (rerank 실패 시 fallback에도 사용)
# - EXPAND_STEP: TABLE_MISSING 발생 시 추가로 확장할 개수 (한 번에 추가되는 후보 수)
# - MAX_TABLE_EXPAND: 테이블 확장(툴 호출) 최대 횟수
# - MAX_SQL_RETRY: SQL 생성/가드 실패 시 재시도 최대 횟수
# - MAX_VALIDATION_RETRY: 검증 단계 재시도 최대 횟수
# - MAX_TOTAL_LOOPS: 전체 그래프 루프 상한 (무한 루프 방지)
# - ELBOW_THRESHOLD: rerank 점수 엘보우 컷 기준
# - MIN_KEEP / MAX_KEEP: rerank 결과에서 최소/최대 유지 테이블 수
# ─────────────────────────────────────────
RETRIEVE_K = 15
TOP_K = 5
EXPAND_STEP = 5
MAX_TABLE_EXPAND = 2
MAX_SQL_RETRY = 2
MAX_VALIDATION_RETRY = 1
MAX_TOTAL_LOOPS = 10
ELBOW_THRESHOLD = 0.15
MIN_KEEP = 3
MAX_KEEP = 5

TIMEZONE = settings.tz
