from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.routers import auth

app = FastAPI(title="DocMate API", version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # tightened per-portal in nginx for production
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth.router)


@app.get("/health")
async def health():
    return {"status": "ok"}
