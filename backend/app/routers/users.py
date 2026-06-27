import logging

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.security import get_current_user, require_roles
from app.models.organization import Organization, OrgType
from app.models.user import Portal, User, UserRole
from app.schemas.user import UserCreate, UserOut, UserUpdate

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/users", tags=["users"])


async def _realm_for_user(portal: str, organization_id: int | None, tenant_id: int, db: AsyncSession) -> str:
    if portal == "digitizing":
        return "doc"
    if organization_id is None:
        raise HTTPException(status_code=400, detail="Customer users must belong to an organisation")
    org = await db.get(Organization, organization_id)
    if not org or org.tenant_id != tenant_id or org.type != OrgType.customer:
        raise HTTPException(status_code=400, detail="Invalid customer organisation")
    if not org.realm_slug:
        raise HTTPException(
            status_code=400,
            detail="Customer organisation does not have a Keycloak realm yet",
        )
    return org.realm_slug


@router.post("", response_model=UserOut, status_code=status.HTTP_201_CREATED)
async def create_user(
    body: UserCreate,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(require_roles("admin")),
):
    existing = await db.execute(select(User).where(User.email == body.email))
    if existing.scalar_one_or_none():
        raise HTTPException(status_code=409, detail="Email already registered")

    tenant_id = current_user._tenant_id
    realm_slug = await _realm_for_user(body.portal, body.organization_id, tenant_id, db)

    from app.services.keycloak_service import create_user_in_realm
    try:
        keycloak_sub = create_user_in_realm(realm_slug, body.email, body.full_name, body.temp_password)
    except Exception as exc:
        logger.error("Keycloak user creation failed: %s", exc)
        raise HTTPException(
            status_code=503,
            detail=f"Could not create user in Keycloak: {exc}",
        )

    user = User(
        tenant_id=tenant_id,
        email=body.email,
        keycloak_sub=keycloak_sub,
        full_name=body.full_name,
        role=UserRole(body.role),
        portal=Portal(body.portal),
        organization_id=body.organization_id,
        is_active=True,
    )
    db.add(user)
    await db.flush()
    return user


@router.get("", response_model=list[UserOut])
async def list_users(
    db: AsyncSession = Depends(get_db),
    current_user=Depends(require_roles("admin", "de_supervisor", "customer_supervisor")),
):
    result = await db.execute(
        select(User).where(User.tenant_id == current_user._tenant_id)
    )
    return list(result.scalars().all())


@router.get("/me", response_model=UserOut)
async def get_me(current_user=Depends(get_current_user)):
    return current_user


@router.get("/{user_id}", response_model=UserOut)
async def get_user(
    user_id: int,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(require_roles("admin", "de_supervisor", "customer_supervisor")),
):
    user = await db.get(User, user_id)
    if not user or user.tenant_id != current_user._tenant_id:
        raise HTTPException(status_code=404, detail="User not found")
    return user


@router.patch("/{user_id}", response_model=UserOut)
async def update_user(
    user_id: int,
    body: UserUpdate,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(require_roles("admin")),
):
    user = await db.get(User, user_id)
    if not user or user.tenant_id != current_user._tenant_id:
        raise HTTPException(status_code=404, detail="User not found")

    if body.is_active is not None and body.is_active != user.is_active and user.keycloak_sub:
        # Mirror active/inactive state in Keycloak
        realm_slug = "doc" if user.portal.value == "digitizing" else None
        if realm_slug is None and user.organization_id:
            org = await db.get(Organization, user.organization_id)
            realm_slug = org.realm_slug if org else None
        if realm_slug:
            try:
                from app.services.keycloak_service import set_user_enabled
                set_user_enabled(realm_slug, user.keycloak_sub, body.is_active)
            except Exception as exc:
                logger.warning("Keycloak enable/disable failed: %s", exc)

    for field, value in body.model_dump(exclude_none=True).items():
        if field == "role":
            setattr(user, field, UserRole(value))
        else:
            setattr(user, field, value)
    return user
