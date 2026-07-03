import logging
from datetime import datetime, timedelta, timezone
from typing import Optional

import httpx
import jwt as pyjwt
from fastapi import Depends, Header, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from jwt import PyJWTError
from jwt.algorithms import RSAAlgorithm
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.database import get_db

logger = logging.getLogger(__name__)

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/auth/token", auto_error=False)

# JWKS cache: realm_slug -> (jwks_dict, expires_at)
_jwks_cache: dict[str, tuple[dict, datetime]] = {}
_JWKS_TTL = timedelta(minutes=10)


async def _get_jwks(realm_slug: str) -> dict:
    now = datetime.now(timezone.utc)
    if realm_slug in _jwks_cache:
        cached_jwks, expires_at = _jwks_cache[realm_slug]
        if now < expires_at:
            return cached_jwks

    url = (
        f"{settings.keycloak_internal_url.rstrip('/')}"
        f"/realms/{realm_slug}/protocol/openid-connect/certs"
    )
    async with httpx.AsyncClient(timeout=5.0, verify=False) as client:
        resp = await client.get(url)
        resp.raise_for_status()
        jwks = resp.json()

    _jwks_cache[realm_slug] = (jwks, now + _JWKS_TTL)
    return jwks


def _extract_realm_slug(token: str) -> str:
    try:
        unverified = pyjwt.decode(
            token,
            options={"verify_signature": False},
            algorithms=["RS256"],
        )
        iss: str = unverified.get("iss", "")
        # iss format: http[s]://host[:port]/realms/<slug>
        parts = iss.split("/realms/")
        if len(parts) != 2:
            raise ValueError("Cannot parse realm from iss")
        return parts[1].split("/")[0]
    except Exception:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Could not parse token issuer",
        )


async def _verify_token(token: str, realm_slug: str) -> dict:
    try:
        jwks = await _get_jwks(realm_slug)
    except Exception as exc:
        logger.warning("JWKS fetch failed for realm %s: %s", realm_slug, exc)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Auth service unavailable",
        )

    header = pyjwt.get_unverified_header(token)
    kid = header.get("kid")

    matching_keys = [
        k for k in jwks.get("keys", [])
        if not kid or k.get("kid") == kid
    ]
    if not matching_keys:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token signing key not found",
        )

    last_exc: Optional[PyJWTError] = None
    for key_data in matching_keys:
        try:
            public_key = RSAAlgorithm.from_jwk(key_data)
            return pyjwt.decode(
                token,
                public_key,
                algorithms=["RS256"],
                options={"verify_aud": False},
            )
        except PyJWTError as exc:
            last_exc = exc

    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail=f"Token validation failed: {last_exc}",
    )


async def get_current_user(
    token: Optional[str] = Depends(oauth2_scheme),
    x_portal: Optional[str] = Header(None, alias="X-Portal"),
    db: AsyncSession = Depends(get_db),
):
    from app.models.user import User

    if not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated",
            headers={"WWW-Authenticate": "Bearer"},
        )

    realm_slug = _extract_realm_slug(token)
    claims = await _verify_token(token, realm_slug)

    keycloak_sub: str = claims.get("sub", "")
    if not keycloak_sub:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing sub claim",
        )

    # Enforce portal based on realm: doc → digitizing, all others → customer
    token_portal = "digitizing" if realm_slug == "doc" else "customer"
    if x_portal and x_portal != token_portal:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Portal mismatch",
        )

    result = await db.execute(select(User).where(User.keycloak_sub == keycloak_sub))
    user = result.scalar_one_or_none()
    if user is None or not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User not found or inactive",
        )

    user._tenant_id = user.tenant_id
    return user


def require_roles(*roles: str):
    async def _check(current_user=Depends(get_current_user)):
        if current_user.role.value not in roles:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Insufficient role",
            )
        return current_user

    return _check
