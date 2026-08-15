import logging

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import joinedload

from app.auth.cookies import access_cookie_name, refresh_cookie_name
from app.models.org_membership import OrgMembership
from app.auth.service import (
    AccountInactiveError,
    EmailAlreadyRegisteredError,
    EmailNotVerifiedError,
    InvalidCredentialsError,
    authenticate,
    issue_verification,
    register_user,
    request_password_reset,
    reset_password,
    verify_email_token,
)
from app.auth.tokens import (
    RefreshTokenReuseError,
    create_access_token,
    issue_refresh_token,
    revoke_refresh_token,
    rotate_refresh_token,
)
from app.config import get_settings
from app.csrf import set_csrf_cookie
from app.db import get_session
from app.models.user import User
from app.rate_limit import limiter
from app.schemas.auth import (
    LoginRequest,
    LoginResponse,
    PasswordResetConfirm,
    PasswordResetRequest,
    RegisterRequest,
    RegisterResponse,
    UserOut,
    VerifyEmailRequest,
)

router = APIRouter(prefix="/auth", tags=["auth"])

logger = logging.getLogger(__name__)


def _set_refresh_cookie(response: Response, token: str) -> None:
    settings = get_settings()
    response.set_cookie(
        key=refresh_cookie_name(),
        value=token,
        max_age=settings.refresh_token_expire_days * 86400,
        httponly=True,
        secure=settings.cookie_secure_enabled,
        samesite=settings.cookie_samesite_value,
        path="/auth",
    )


def _clear_refresh_cookie(response: Response) -> None:
    settings = get_settings()
    response.delete_cookie(
        refresh_cookie_name(),
        path="/auth",
        secure=settings.cookie_secure_enabled,
        samesite=settings.cookie_samesite_value,
    )


def _set_access_cookie(response: Response, token: str) -> None:
    settings = get_settings()
    response.set_cookie(
        key=access_cookie_name(),
        value=token,
        max_age=settings.access_token_expire_minutes * 60,
        httponly=True,
        secure=settings.cookie_secure_enabled,
        samesite=settings.cookie_samesite_value,
        path="/",
    )


def _clear_access_cookie(response: Response) -> None:
    settings = get_settings()
    response.delete_cookie(
        access_cookie_name(),
        path="/",
        secure=settings.cookie_secure_enabled,
        samesite=settings.cookie_samesite_value,
    )


@router.get("/csrf")
async def csrf_token(response: Response) -> dict[str, str]:
    """Bootstrap endpoint for the signed double-submit CSRF cookie.

    The SPA must call this (e.g. on load) before issuing any state-changing
    request and echo the cookie value in the ``X-CSRF-Token`` header.
    """
    set_csrf_cookie(response)
    return {"status": "csrf_token_issued"}


@router.post("/register", status_code=201, response_model=RegisterResponse)
async def register(
    payload: RegisterRequest,
    session: AsyncSession = Depends(get_session),
) -> RegisterResponse:
    try:
        user = await register_user(session, payload.email, payload.password)
    except EmailAlreadyRegisteredError:
        raise HTTPException(status_code=409, detail="email already registered")
    try:
        await issue_verification(session, user)
    except Exception as e:
        logger.error(f"Failed to send verification email: {e}")
        raise HTTPException(status_code=500, detail="failed to send verification email")
    return RegisterResponse(
        user=UserOut(id=user.id, email=user.email),
        message="verification email sent",
    )


@router.post("/verify-email")
async def verify_email(
    payload: VerifyEmailRequest,
    session: AsyncSession = Depends(get_session),
) -> dict[str, str]:
    user = await verify_email_token(session, payload.token)
    if user is None:
        raise HTTPException(status_code=400, detail="invalid or expired verification token")
    return {"status": "verified"}


@router.post("/login", response_model=LoginResponse)
@limiter.limit("5/minute")
async def login(
    request: Request,
    payload: LoginRequest,
    response: Response,
    session: AsyncSession = Depends(get_session),
) -> LoginResponse:
    try:
        user = await authenticate(session, payload.email, payload.password)
        organizations = [{"id": org.organization.id, "name": org.organization.name, "slug": org.organization.slug} for org in user.memberships]
    except InvalidCredentialsError:
        raise HTTPException(status_code=401, detail="invalid credentials")
    except EmailNotVerifiedError:
        raise HTTPException(status_code=403, detail="email not verified")
    except AccountInactiveError:
        raise HTTPException(status_code=403, detail="account disabled")

    access_token = create_access_token(user.id, user.token_version)
    refresh_token = await issue_refresh_token(session, user.id)
    _set_access_cookie(response, access_token)
    _set_refresh_cookie(response, refresh_token)
    set_csrf_cookie(response)
    return LoginResponse(
        user=UserOut(id=user.id, email=user.email),
        organizations=organizations,
    )


@router.post("/refresh", response_model=LoginResponse)
async def refresh(
    request: Request,
    response: Response,
    session: AsyncSession = Depends(get_session),
) -> LoginResponse:
    token = request.cookies.get(refresh_cookie_name())
    if not token:
        raise HTTPException(status_code=401, detail="missing refresh token")
    try:
        result = await rotate_refresh_token(session, token)
    except RefreshTokenReuseError:
        _clear_refresh_cookie(response)
        _clear_access_cookie(response)
        raise HTTPException(status_code=401, detail="refresh token reuse detected; please log in again")
    if result is None:
        _clear_refresh_cookie(response)
        raise HTTPException(status_code=401, detail="invalid or expired refresh token")
    user_id, new_token = result
    user = await session.scalar(
        select(User)
        .join(User.memberships)
        .where(User.id == user_id)
        .options(joinedload(User.memberships).joinedload(OrgMembership.organization))
    )
    if user is None or not user.is_active:
        _clear_refresh_cookie(response)
        _clear_access_cookie(response)
        raise HTTPException(status_code=401, detail="user not found or disabled")
    new_access_token = create_access_token(user.id, user.token_version)
    _set_access_cookie(response, new_access_token)
    _set_refresh_cookie(response, new_token)
    set_csrf_cookie(response)
    organizations = [{"id": org.organization.id, "name": org.organization.name, "slug": org.organization.slug} for org in user.memberships]
    return LoginResponse(
        user=UserOut(id=user.id, email=user.email),
        organizations=organizations,
    )


@router.post("/password-reset/request")
@limiter.limit("5/hour")
async def password_reset_request(
    request: Request,
    payload: PasswordResetRequest,
    session: AsyncSession = Depends(get_session),
) -> dict[str, str]:
    await request_password_reset(session, payload.email)
    return {"status": "password_reset_email_sent"}


@router.post("/password-reset/confirm")
async def password_reset_confirm(
    payload: PasswordResetConfirm,
    session: AsyncSession = Depends(get_session),
) -> dict[str, str]:
    user = await reset_password(session, payload.token, payload.new_password)
    if user is None:
        raise HTTPException(status_code=400, detail="invalid or expired reset token")
    return {"status": "password_updated"}


@router.post("/logout")
async def logout(
    request: Request,
    response: Response,
    session: AsyncSession = Depends(get_session),
) -> dict[str, str]:
    token = request.cookies.get(refresh_cookie_name())
    if token:
        await revoke_refresh_token(session, token)
    _clear_refresh_cookie(response)
    _clear_access_cookie(response)
    return {"status": "logged_out"}