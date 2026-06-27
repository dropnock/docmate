from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.security import get_current_user, get_password_hash, require_roles
from app.models.user import Portal, User, UserRole
from app.schemas.user import UserCreate, UserOut, UserUpdate

router = APIRouter(prefix="/api/users", tags=["users"])


@router.post("", response_model=UserOut, status_code=status.HTTP_201_CREATED)
async def create_user(
    body: UserCreate,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(require_roles("admin")),
):
    existing = await db.execute(select(User).where(User.email == body.email))
    if existing.scalar_one_or_none():
        raise HTTPException(status_code=409, detail="Email already registered")

    user = User(
        tenant_id=current_user._tenant_id,
        email=body.email,
        hashed_password=get_password_hash(body.password),
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
    for field, value in body.model_dump(exclude_none=True).items():
        if field == "role":
            setattr(user, field, UserRole(value))
        else:
            setattr(user, field, value)
    return user
