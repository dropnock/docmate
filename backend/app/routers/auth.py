from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.security import get_current_user
from app.models.organization import Organization, OrgType
from app.models.user import User
from app.schemas.auth import MeResponse, RealmLookupResponse

router = APIRouter(prefix="/api/auth", tags=["auth"])


@router.get("/me", response_model=MeResponse)
async def get_me(current_user=Depends(get_current_user)):
    return current_user


@router.get("/realm-by-domain", response_model=RealmLookupResponse)
async def get_realm_by_domain(
    email: str = Query(..., description="User email address"),
    db: AsyncSession = Depends(get_db),
):
    """Public endpoint — resolves the Keycloak realm for a user's email domain.
    Returns only the realm slug; never reveals org names or user existence."""
    domain = email.strip().lower().split("@")[-1]
    if not domain or "@" not in email:
        raise HTTPException(status_code=400, detail="Invalid email address")

    result = await db.execute(
        select(Organization)
        .join(User, User.organization_id == Organization.id)
        .where(
            Organization.type == OrgType.customer,
            Organization.realm_slug.isnot(None),
            User.email.ilike(f"%@{domain}"),
        )
        .limit(1)
    )
    org = result.scalar_one_or_none()
    if not org:
        raise HTTPException(status_code=404, detail="No organisation found for this email domain")
    return RealmLookupResponse(realm_slug=org.realm_slug)
