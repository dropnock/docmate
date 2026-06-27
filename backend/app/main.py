from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.routers import (
    analytics,
    aql,
    auth,
    batches,
    batches_workflow,
    records,
    shifts,
    tasks,
    tenants,
    users,
)


@asynccontextmanager
async def lifespan(app: FastAPI):
    from app.background.stale_checker import start_scheduler
    scheduler = start_scheduler()
    yield
    scheduler.shutdown(wait=False)


app = FastAPI(title="DocMate API", version="0.1.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # tightened per-portal in nginx for production
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth.router)
app.include_router(tenants.router)
app.include_router(shifts.router)
app.include_router(batches.router)
app.include_router(batches_workflow.router)
app.include_router(records.router)
app.include_router(tasks.router)
app.include_router(aql.router)
app.include_router(analytics.router)
app.include_router(users.router)


@app.get("/health")
async def health():
    return {"status": "ok"}
