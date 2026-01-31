FROM python:3.11-slim

WORKDIR /app

# 시스템 도구 설치 (top, free, df 등)
RUN apt-get update && apt-get install -y \
    procps \
    && rm -rf /var/lib/apt/lists/*

# 의존성 설치
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 코드 복사
COPY server.py .

# 포트 노출
EXPOSE 8002

# 실행
CMD ["python", "server.py"]
