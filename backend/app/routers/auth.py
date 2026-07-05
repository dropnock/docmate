from fastapi import APIRouter, Depends, HTTPException, Query
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


@router.get("/realm-by-subdomain")
async def get_realm_by_subdomain(
    subdomain: str = Query(...),
    db: AsyncSession = Depends(get_db),
):
    """Public endpoint — resolves the Keycloak realm for a customer subdomain."""
    result = await db.execute(
        select(Organization).where(
            Organization.type == OrgType.customer,
            Organization.realm_slug == subdomain,
        )
    )
    org = result.scalar_one_or_none()
    if not org:
        raise HTTPException(status_code=404, detail="No customer found for this subdomain")
    return {"realm_slug": org.realm_slug, "name": org.name}


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
