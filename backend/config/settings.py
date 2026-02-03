from pydantic_settings import BaseSettings

class Settings(BaseSettings):
    # Database
    db_host: str = "localhost"
    db_port: int = 5432
    db_name: str = "server_agent_db"
    db_user: str
    db_password: str
    
    # OpenAI
    openai_api_key: str
    model_fast: str = "gpt-4o-mini"  # 빠르고 저렴한 모델 (파싱, 리포팅)
    model_smart: str = "gpt-4o"      # 똑똑한 모델 (SQL 생성, 검증)

    
    # Qdrant
    qdrant_url: str = "http://localhost:6333"
    qdrant_api_key: str = ""
    qdrant_collection: str = "table_index"

    # Embeddings / Schema sync
    enable_schema_sync: bool = True
    embedding_model: str = "text-embedding-3-small"
    schema_hash_file: str = "/app/.schema_hash"
    schema_namespaces: str = ""  # comma-separated; empty means auto-detect user schemas
    schema_notify_channel: str = "table_change"
    schema_trigger_name: str = "notify_schema_change"
    
    # Timezone
    tz: str = "Asia/Seoul"

    # Paths
    mcp_servers_dir: str = "/app/mcp_servers"

    # MCP Transport
    mcp_transport: str = "http"  # "stdio" or "http"
    mcp_postgres_url: str = "http://mcp-postgres:8000"
    mcp_ubuntu_url: str = "http://mcp-ubuntu:8000"
    mcp_qdrant_url: str = "http://mcp-qdrant:8000"
    
    class Config:
        env_file = ".env"
        case_sensitive = False
        extra = "allow"  # .env의 추가 필드 허용

settings = Settings()
