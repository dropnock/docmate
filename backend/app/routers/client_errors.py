import logging

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from app.core.security import get_current_user

logger = logging.getLogger("docmate.frontend")

router = APIRouter(prefix="/api/client-errors", tags=["client-errors"])


class ClientErrorReport(BaseModel):
    message: str
    stack: str | None = None
    component_stack: str | None = None
    url: str | None = None


@router.post("", status_code=204)
async def report_client_error(
    body: ClientErrorReport,
    current_user=Depends(get_current_user),
):
    """Sink for frontend crash reports (see WorkspaceErrorBoundary.tsx) — logs
    them server-side, structured the same as backend errors, so a React
    crash shows up in the same log/dashboard view instead of vanishing the
    moment the user closes the tab. Auth-protected like every other
    endpoint; a crash caused by a broken auth token won't get reported here,
    an accepted gap rather than opening an unauthenticated endpoint."""
    logger.error(
        "frontend error: %s",
        body.message,
        extra={
            "source": "frontend",
            "user_id": current_user.id,
            "url": body.url,
            "stack": body.stack,
            "component_stack": body.component_stack,
        },
    )
