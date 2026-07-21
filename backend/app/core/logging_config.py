"""Structured JSON logging with per-request correlation.

Every log record emitted while a request is in flight automatically carries
a `request_id` field (see main.py's request_context_middleware, which sets
request_id_var for the duration of the request) — this is what lets a user
hand support an ID from an error toast and have it be directly grep/query-
able against backend logs, without threading an id through every function
signature by hand.
"""
import logging
import sys
from contextvars import ContextVar

from pythonjsonlogger import jsonlogger

request_id_var: ContextVar[str | None] = ContextVar("request_id", default=None)


class RequestIdFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.request_id = request_id_var.get()
        return True


def configure_logging(log_level: str = "INFO") -> None:
    handler = logging.StreamHandler(sys.stdout)
    handler.addFilter(RequestIdFilter())
    handler.setFormatter(
        jsonlogger.JsonFormatter(
            "%(asctime)s %(levelname)s %(name)s %(message)s %(request_id)s",
            rename_fields={"asctime": "timestamp", "levelname": "level", "name": "logger"},
        )
    )

    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(log_level.upper())

    # uvicorn configures "uvicorn"/"uvicorn.error"/"uvicorn.access" with their
    # own handlers and propagate=False by default, so attaching a handler to
    # root alone would leave them printing plain, non-JSON, non-correlated
    # lines regardless of the above. Route the error/startup logger through
    # our own formatting instead; "uvicorn.access" duplicates the structured
    # per-request line request_context_middleware already logs (with the
    # request_id it lacks), so it's silenced rather than kept as a second,
    # inconsistent copy of the same information.
    for name in ("uvicorn", "uvicorn.error"):
        uv_logger = logging.getLogger(name)
        uv_logger.handlers = []
        uv_logger.propagate = True

    access_logger = logging.getLogger("uvicorn.access")
    access_logger.handlers = []
    access_logger.propagate = False
    access_logger.disabled = True

    if log_level.upper() != "DEBUG":
        # aioboto3/botocore and httpx are extremely chatty at INFO — not
        # worth drowning out application logs for libraries we don't own.
        logging.getLogger("botocore").setLevel(logging.WARNING)
        logging.getLogger("aiobotocore").setLevel(logging.WARNING)
        logging.getLogger("httpx").setLevel(logging.WARNING)
