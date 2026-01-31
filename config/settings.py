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
    
    # MCP Servers
    mcp_postgres_url: str = "http://localhost:8001"
    mcp_ubuntu_url: str = "http://localhost:8002"
    
    # Timezone
    tz: str = "Asia/Seoul"
    
    # Paths (optional, for local development)
    schema_dir: str | None = None
    mcp_servers_dir: str | None = None
    
    class Config:
        env_file = ".env"
        case_sensitive = False
        extra = "allow"  # .env의 추가 필드 허용

settings = Settings()
