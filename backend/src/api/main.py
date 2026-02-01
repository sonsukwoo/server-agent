from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from src.agents.text_to_sql import run_text_to_sql
from src.agents.middleware.input_guard import InputGuard
from src.embeddings.schema_sync import sync_schema_embeddings
from config.settings import settings
import asyncio

class QueryRequest(BaseModel):
    agent: str  # "sql" 또는 "ubuntu"
    question: str

class QueryResponse(BaseModel):
    ok: bool
    agent: str
    data: dict | None = None
    error: str | None = None

app = FastAPI(title="Server Agent API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.get("/")
async def root():
    return {"message": "Server Agent API is running"}

@app.on_event("startup")
async def startup_event():
    """서버 시작 시 스키마 임베딩 동기화"""
    if settings.enable_schema_sync:
        await asyncio.to_thread(sync_schema_embeddings)

@app.post("/query", response_model=QueryResponse)
async def query(body: QueryRequest):
    """자연어 질문을 받아서 처리"""
    agent = body.agent.lower().strip()
    question = body.question.strip()

    # 1. 입력 검증
    if not question:
        raise HTTPException(status_code=400, detail="질문이 비어있습니다")
    
    is_valid, error = InputGuard.validate(question)
    if not is_valid:
        raise HTTPException(status_code=400, detail=error)

    # 2. 에이전트 분기
    if agent == "sql":
        try:
            result = await run_text_to_sql(question)
            
            # 최종 보고서 및 데이터 추출
            report = result.get("report", "")
            suggested_actions = result.get("suggested_actions", [])
            
            return QueryResponse(
                ok=True,
                agent="sql",
                data={
                    "report": report,
                    "suggested_actions": suggested_actions,
                    "raw": result  # 전체 로우 데이터 (디버깅용)
                }
            )
        except Exception as e:
            # 에이전트 실행 중 예외
            raise HTTPException(status_code=500, detail=str(e))

    elif agent == "ubuntu":
        # 아직 미구현
        raise HTTPException(status_code=501, detail="Ubuntu agent not implemented yet")

    else:
        raise HTTPException(status_code=400, detail="Invalid agent. Use 'sql' or 'ubuntu'.")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
