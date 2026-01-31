FROM python:3.11-slim

WORKDIR /app

# 의존성 설치
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 코드 복사
COPY server.py .

# 포트 노출
EXPOSE 8001

# 실행
CMD ["python", "server.py"]
