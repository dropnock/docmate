from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.security import get_current_user
from app.models.organization import Organization, OrgType
from app.schemas.auth import CustomerRealm, MeResponse

router = APIRouter(prefix="/api/auth", tags=["auth"])


@router.get("/me", response_model=MeResponse)
async def get_me(current_user=Depends(get_current_user)):
    return current_user


@router.get("/customer-realms", response_model=list[CustomerRealm])
async def list_customer_realms(db: AsyncSession = Depends(get_db)):
    """Public endpoint — returns customer orgs that have a Keycloak realm."""
    result = await db.execute(
        select(Organization).where(
            Organization.type == OrgType.customer,
            Organization.realm_slug.isnot(None),
        )
    )
    orgs = result.scalars().all()
    return [CustomerRealm(name=o.name, realm_slug=o.realm_slug) for o in orgs]
