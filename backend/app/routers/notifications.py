from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.security import get_current_user
from app.services import notification_service

router = APIRouter(prefix="/api/notifications", tags=["notifications"])


class MarkRead(BaseModel):
    ids: list[int] | None = None  # omitted = all


@router.get("")
async def list_notifications(
    unread_only: bool = False,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """The signed-in user's latest notifications, newest first."""
    notes = await notification_service.list_for_user(
        db, user_id=current_user.id, tenant_id=current_user._tenant_id, unread_only=unread_only,
    )
    return [notification_service.notification_out(n) for n in notes]


@router.get("/unread-count")
async def unread_count(
    db: AsyncSession = Depends(get_db),
    current_user=Depends(get_current_user),
):
    return {"count": await notification_service.unread_count(
        db, user_id=current_user.id, tenant_id=current_user._tenant_id,
    )}


@router.post("/read")
async def mark_read(
    body: MarkRead,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(get_current_user),
):
    n = await notification_service.mark_read(
        db, user_id=current_user.id, tenant_id=current_user._tenant_id, ids=body.ids,
    )
    await db.commit()
    return {"marked": n}
