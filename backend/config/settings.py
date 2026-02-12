from pydantic_settings import BaseSettings, SettingsConfigDict

class Settings(BaseSettings):
    # =================================================================
    # 데이터베이스 (PostgreSQL) 설정
    # =================================================================
    db_host: str = "localhost"       # DB 호스트 주소
    db_port: int = 5432              # DB 포트 번호
    db_name: str = "server_agent_db" # DB 이름
    db_user: str                     # DB 사용자명 (필수, .env에서 로드)
    db_password: str                 # DB 비밀번호 (필수, .env에서 로드)
    db_pool_min: int = 1             # DB 풀 최소 연결 수
    db_pool_max: int = 5             # DB 풀 최대 연결 수
    
    # =================================================================
    # OpenAI LLM 설정
    # =================================================================
    openai_api_key: str              # OpenAI API 키 (필수)
    model_fast: str = "gpt-4o-mini"  # 빠르고 저렴한 모델 (단순 파싱, 리포팅용)
    model_smart: str = "gpt-4o"      # 고지능 모델 (복잡한 SQL 생성, 검증용)

    # =================================================================
    # Qdrant (벡터 DB) 설정
    # =================================================================
    qdrant_url: str = "http://localhost:6333" # Qdrant 서버 주소
    qdrant_api_key: str = ""                  # Qdrant 인증 키 (없으면 공란)
    qdrant_collection: str = "table_index"    # 테이블 정보를 저장할 컬렉션 이름

    # =================================================================
    # 임베딩 및 스키마 동기화 설정
    # =================================================================
    enable_schema_sync: bool = True           # 서버 시작 시 스키마 자동 동기화 여부
    embedding_model: str = "text-embedding-3-small" # 사용할 임베딩 모델
    schema_hash_file: str = "/app/.schema_hash"     # 스키마 변경 감지를 위한 해시 저장 경로
    schema_namespaces: str = ""               # 동기화할 대상 스키마 (콤마 구분, 공란 시 자동 감지)
    
    # 임베딩에서 제외할 스키마 (시스템 테이블 및 채팅 로그 등)
    # pg_catalog, information_schema: Postgres 시스템 스키마
    # chat: 대화 내역 저장용 스키마 (분석 대상 아님)
    schema_exclude_namespaces: str = "pg_catalog,information_schema,chat"
    
    schema_notify_channel: str = "table_change"     # 스키마 변경 알림 채널명 (LISTEN/NOTIFY)
    schema_trigger_name: str = "notify_schema_change" # 변경 감지 트리거 함수명
    
    # =================================================================
    # 시간대 설정
    # =================================================================
    tz: str = "Asia/Seoul"  # 기본 시간대 (한국 표준시)

    # =================================================================
    # 경로 설정
    # =================================================================
    mcp_servers_dir: str = "/app/mcp_servers" # MCP 서버 코드들이 위치한 디렉토리 경로

    # =================================================================
    # MCP (Model Context Protocol) 통신 설정
    # =================================================================
    mcp_transport: str = "http"  # 통신 방식: "stdio" (표준입출력) 또는 "http" (REST API)
    
    # 각 MCP 서버의 HTTP 주소 (docker-compose 서비스명 사용)
    mcp_postgres_url: str = "http://mcp-postgres:8000"
    mcp_qdrant_url: str = "http://mcp-qdrant:8000"
    
    model_config = SettingsConfigDict(
        env_file=".env",       # .env 파일에서 환경변수 로드
        case_sensitive=False,  # 대소문자 구분 안 함
        extra="allow",         # .env에 정의되지 않은 추가 변수도 허용
    )

settings = Settings()
