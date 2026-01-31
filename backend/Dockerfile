FROM python:3.11-slim

WORKDIR /app

# 시스템 패키지 설치
RUN apt-get update && apt-get install -y \
    gcc \
    && rm -rf /var/lib/apt/lists/*

# Python 의존성 설치
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# MCP Server 의존성 설치
COPY mcp_servers/postgres/requirements.txt mcp-postgres-reqs.txt
RUN pip install --no-cache-dir -r mcp-postgres-reqs.txt

COPY mcp_servers/ubuntu/requirements.txt mcp-ubuntu-reqs.txt
RUN pip install --no-cache-dir -r mcp-ubuntu-reqs.txt

# 애플리케이션 코드 복사
COPY . .

# 포트 노출
EXPOSE 8000

# 실행
CMD ["python", "-m", "src.api.main"]
