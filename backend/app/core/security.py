from datetime import datetime, timedelta
from typing import Optional

from fastapi import Depends, Header, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from jose import JWTError, jwt
from passlib.context import CryptContext
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.database import get_db

ALGORITHM = "HS256"

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/auth/login")


def verify_password(plain: str, hashed: str) -> bool:
    return pwd_context.verify(plain, hashed)


def get_password_hash(password: str) -> str:
    return pwd_context.hash(password)


def create_access_token(
    user_id: int,
    tenant_id: int,
    portal: str,
    role: str,
    expires_delta: Optional[timedelta] = None,
) -> str:
    expire = datetime.utcnow() + (
        expires_delta or timedelta(minutes=settings.access_token_expire_minutes)
    )
    payload = {
        "sub": str(user_id),
        "tenant_id": tenant_id,
        "portal": portal,
        "role": role,
        "exp": expire,
    }
    return jwt.encode(payload, settings.secret_key, algorithm=ALGORITHM)


async def get_current_user(
    token: str = Depends(oauth2_scheme),
    x_portal: Optional[str] = Header(None, alias="X-Portal"),
    db: AsyncSession = Depends(get_db),
):
    from app.models.user import User

    credentials_exc = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )
    try:
        payload = jwt.decode(token, settings.secret_key, algorithms=[ALGORITHM])
        user_id = int(payload["sub"])
        token_portal: str = payload["portal"]
        tenant_id: int = payload["tenant_id"]
    except (JWTError, KeyError, ValueError):
        raise credentials_exc

    # Enforce portal: JWT claim must match the X-Portal header sent by nginx
    if x_portal and x_portal != token_portal:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Portal mismatch")

    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if user is None or not user.is_active:
        raise credentials_exc

    # Attach resolved tenant_id so services don't need to re-decode
    user._tenant_id = tenant_id
    return user


def require_roles(*roles: str):
    """FastAPI dependency that enforces one of the given roles."""
    async def _check(current_user=Depends(get_current_user)):
        if current_user.role.value not in roles:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Insufficient role")
        return current_user
    return _check
