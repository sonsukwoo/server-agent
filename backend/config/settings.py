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
    
    # Timezone
    tz: str = "Asia/Seoul"

    # Paths
    schema_dir: str = "/app/schema"
    mcp_servers_dir: str = "/app/mcp_servers"
    
    class Config:
        env_file = ".env"
        case_sensitive = False
        extra = "allow"  # .env의 추가 필드 허용

settings = Settings()
