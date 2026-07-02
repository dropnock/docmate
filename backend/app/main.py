import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

logger = logging.getLogger(__name__)

from app.routers import (
    analytics,
    aql,
    auth,
    batches,
    cabinets,
    lots,
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


@app.exception_handler(RequestValidationError)
async def validation_error_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
    logger.warning("422 validation error on %s %s: %s", request.method, request.url.path, exc.errors())
    return JSONResponse(status_code=422, content={"detail": exc.errors()})


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
app.include_router(cabinets.router)
app.include_router(lots.router)
app.include_router(records.router)
app.include_router(tasks.router)
app.include_router(aql.router)
app.include_router(analytics.router)
app.include_router(users.router)


@app.get("/health")
async def health():
    return {"status": "ok"}
