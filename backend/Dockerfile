FROM python:3.11-slim

WORKDIR /app

# 시스템 패키지 설치
RUN apt-get update && apt-get install -y \
    gcc \
    && rm -rf /var/lib/apt/lists/*

# Python 의존성 설치
COPY backend/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# MCP Server 의존성 설치
COPY mcp_servers/postgres/requirements.txt mcp-postgres-reqs.txt
RUN pip install --no-cache-dir -r mcp-postgres-reqs.txt

# Ubuntu MCP
COPY mcp_servers/ubuntu/requirements.txt mcp-ubuntu-reqs.txt
RUN pip install --no-cache-dir -r mcp-ubuntu-reqs.txt

# Qdrant Search MCP
COPY mcp_servers/qdrant/search/requirements.txt mcp-qdrant-search-reqs.txt
RUN pip install --no-cache-dir -r mcp-qdrant-search-reqs.txt

# Qdrant Embeddings MCP
COPY mcp_servers/qdrant/embeddings/requirements.txt mcp-qdrant-embeddings-reqs.txt
RUN pip install --no-cache-dir -r mcp-qdrant-embeddings-reqs.txt

# 애플리케이션 코드 복사 (backend 디렉토리 내용만)
COPY backend/ .

# 포트 노출
EXPOSE 8000

# 실행
CMD ["python", "-m", "src.api.main"]
