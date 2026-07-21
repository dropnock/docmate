import logging
import time
import uuid
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from prometheus_fastapi_instrumentator import Instrumentator
from sqlalchemy import text

from app.core.config import settings
from app.core.logging_config import configure_logging, request_id_var

configure_logging(settings.log_level)

logger = logging.getLogger(__name__)

from app.routers import (
    analytics,
    aql,
    auth,
    batches,
    cabinets,
    client_errors,
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
    from app.services.keycloak_service import sync_de_client

    try:
        await sync_de_client()
    except Exception:
        logger.exception("Failed to sync docmate-de Keycloak client redirect URIs at startup")

    scheduler = start_scheduler()
    yield
    scheduler.shutdown(wait=False)


app = FastAPI(title="DocMate API", version=settings.app_version, lifespan=lifespan)


@app.middleware("http")
async def request_context_middleware(request: Request, call_next):
    """Assigns/propagates a per-request correlation ID (X-Request-ID) and
    logs one structured access-log line per request. Placed as the outermost
    middleware so the ID is set before anything else — including Starlette's
    own exception-handling middleware further in — runs, letting every log
    line for this request (success or the exception_handler below) share it."""
    request_id = request.headers.get("X-Request-ID") or str(uuid.uuid4())
    # Also stashed on request.state: ServerErrorMiddleware (which wraps this
    # middleware — see build_middleware_stack in Starlette) invokes
    # unhandled_exception_handler below AFTER this middleware's `finally`
    # has already reset the contextvar, so that handler can't rely on it and
    # needs a value tied to the request itself instead.
    request.state.request_id = request_id
    token = request_id_var.set(request_id)
    start = time.perf_counter()
    try:
        response = await call_next(request)
        duration_ms = round((time.perf_counter() - start) * 1000, 2)
        logger.info(
            "request completed",
            extra={
                "method": request.method,
                "path": request.url.path,
                "status_code": response.status_code,
                "duration_ms": duration_ms,
            },
        )
        response.headers["X-Request-ID"] = request_id
        return response
    finally:
        request_id_var.reset(token)


@app.exception_handler(RequestValidationError)
async def validation_error_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
    request_id = getattr(request.state, "request_id", None)
    logger.warning("422 validation error on %s %s: %s", request.method, request.url.path, exc.errors())
    response = JSONResponse(status_code=422, content={"detail": exc.errors(), "request_id": request_id})
    if request_id:
        response.headers["X-Request-ID"] = request_id
    return response


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    """Catches anything a route didn't handle itself. Previously this fell
    through to Starlette's default 500 with no server-side trace captured
    anywhere searchable — now it's logged with a full traceback under the
    same request_id returned to the client, so a user-reported error can be
    traced straight back to the matching log line.

    Runs outside request_context_middleware (ServerErrorMiddleware wraps it —
    see build_middleware_stack in Starlette), so by this point that
    middleware's `finally` has already reset request_id_var, AND its own
    response — built here — is sent directly by ServerErrorMiddleware,
    bypassing that middleware's usual X-Request-ID header injection. Both
    need request.state's copy of the id instead."""
    request_id = getattr(request.state, "request_id", None)
    if request_id:
        request_id_var.set(request_id)
    logger.exception("unhandled exception on %s %s", request.method, request.url.path)
    response = JSONResponse(
        status_code=500,
        content={"detail": "Internal server error", "request_id": request_id},
    )
    if request_id:
        response.headers["X-Request-ID"] = request_id
    return response


app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # tightened per-portal in nginx for production
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

Instrumentator().instrument(app).expose(app, endpoint="/metrics", include_in_schema=False)

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
app.include_router(client_errors.router)


@app.get("/health")
async def health():
    from app.core.database import AsyncSessionLocal

    try:
        async with AsyncSessionLocal() as db:
            await db.execute(text("SELECT 1"))
    except Exception:
        logger.exception("health check failed: database unreachable")
        return JSONResponse(status_code=503, content={"status": "error", "detail": "database unreachable"})
    return {"status": "ok", "version": settings.app_version, "git_commit": settings.git_commit}


@app.get("/version")
async def version():
    return {"version": settings.app_version, "git_commit": settings.git_commit}
