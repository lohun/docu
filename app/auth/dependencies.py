from typing import Literal

from fastapi import Depends, HTTPException, Security
from fastapi.security import APIKeyCookie
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.cookies import access_cookie_name
from app.auth.service import OrgAccessError, check_role_sufficient, verify_org_access
from app.auth.tokens import decode_access_token
from app.db import get_session
from app.models.organization import Organization
from app.models.user import User

cookie_scheme = APIKeyCookie(name=access_cookie_name(), auto_error=False)

Role = Literal["owner", "admin", "member", "viewer"]


async def get_current_user(
    token: str | None = Security(cookie_scheme),
    session: AsyncSession = Depends(get_session),
) -> User:
    """Decode JWT access token from httpOnly cookie and resolve authenticated user."""
    if token is None:
        raise HTTPException(status_code=401, detail="missing authentication token")
    try:
        payload = decode_access_token(token)
        user_id = int(payload["sub"])
    except Exception as e:
        raise HTTPException(status_code=401, detail="invalid authentication token") from e
    
    user = await session.get(User, user_id)
    if user is None:
        raise HTTPException(status_code=401, detail="user not found")

    # token_version bounds the access token to the latest auth epoch. Any bump
    # (password change/disable) immediately invalidates previously issued JWTs.
    if payload.get("ver", 1) != user.token_version:
        raise HTTPException(status_code=401, detail="session token is stale; please log in again")

    if not user.is_active:
        raise HTTPException(status_code=403, detail="account is disabled")
    
    return user


async def get_current_org(
    org_id: int,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> tuple[Organization, Role]:
    """Resolve organization and verify user's membership and role."""
    try:
        org, role = await verify_org_access(session, user.id, org_id, "viewer")
        return org, role
    except OrgAccessError as e:
        raise HTTPException(status_code=403, detail=str(e)) from e


def require_role(required_role: Role, user_role: Role) -> None:
    """Check if user's role is sufficient for the required role."""
    if not check_role_sufficient(user_role, required_role):
        raise HTTPException(
            status_code=403,
            detail=f"role '{user_role}' is insufficient for '{required_role}'"
        )
