from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.security import create_access_token, verify_password
from app.models.user import User
from app.schemas.auth import LoginRequest, TokenResponse

router = APIRouter(prefix="/api/auth", tags=["auth"])


@router.post("/login", response_model=TokenResponse)
async def login(body: LoginRequest, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(User).where(User.email == body.email))
    user = result.scalar_one_or_none()

    if not user or not verify_password(body.password, user.hashed_password):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid credentials"
        )

    if not user.is_active:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Account disabled")

    # Validate the user belongs to the requested portal
    if user.portal.value != body.portal:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"This account does not have access to the '{body.portal}' portal",
        )

    token = create_access_token(
        user_id=user.id,
        tenant_id=user.tenant_id,
        portal=user.portal.value,
        role=user.role.value,
    )
    return TokenResponse(
        access_token=token,
        user_id=user.id,
        role=user.role.value,
        portal=user.portal.value,
        full_name=user.full_name,
    )
