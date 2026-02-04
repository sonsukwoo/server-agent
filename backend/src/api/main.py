"""
FastAPI 앱 진입점 (Refactored)
"""
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from src.api.lifespan import lifespan
from src.api.query import router as query_router
from src.api.resource import router as resource_router
from src.api.chat import router as chat_router

app = FastAPI(title="Server Agent API", lifespan=lifespan)

# CORS 미들웨어 등록
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 라우터 등록
app.include_router(chat_router)
app.include_router(query_router)
app.include_router(resource_router)

# 고급 설정 (알림) 라우터
from src.advanced_settings.router import router as advanced_router
app.include_router(advanced_router)

@app.get("/")
async def root():
    return {"message": "Server Agent API is running"}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
