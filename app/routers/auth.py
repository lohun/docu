from fastapi import APIRouter, Depends, HTTPException, Request, Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import joinedload

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
    create_access_token,
    issue_refresh_token,
    revoke_refresh_token,
    rotate_refresh_token,
)
from app.config import get_settings
from app.db import get_session
from app.models.org_membership import OrgMembership
from app.models.organization import Organization
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

REFRESH_COOKIE = "refresh_token"
ACCESS_TOKEN_COOKIE = "access_token"


def _set_refresh_cookie(response: Response, token: str) -> None:
    settings = get_settings()
    response.set_cookie(
        key=REFRESH_COOKIE,
        value=token,
        max_age=settings.refresh_token_expire_days * 86400,
        httponly=True,
        secure=settings.environment != "development",
        samesite="lax",
        path="/auth",
    )


def _clear_refresh_cookie(response: Response) -> None:
    response.delete_cookie(REFRESH_COOKIE, path="/auth")


def _set_access_cookie(response: Response, token: str) -> None:
    settings = get_settings()
    response.set_cookie(
        key=ACCESS_TOKEN_COOKIE,
        value=token,
        max_age=settings.access_token_expire_minutes * 60,
        httponly=True,
        secure=settings.environment != "development",
        samesite="lax",
        path="/",
    )


def _clear_access_cookie(response: Response) -> None:
    response.delete_cookie(ACCESS_TOKEN_COOKIE, path="/")


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

    access_token = create_access_token(user.id)
    refresh_token = await issue_refresh_token(session, user.id)
    _set_access_cookie(response, access_token)
    _set_refresh_cookie(response, refresh_token)
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
    token = request.cookies.get(REFRESH_COOKIE)
    if not token:
        raise HTTPException(status_code=401, detail="missing refresh token")
    result = await rotate_refresh_token(session, token)
    if result is None:
        _clear_refresh_cookie(response)
        raise HTTPException(status_code=401, detail="invalid or expired refresh token")
    user_id, new_token = result
    new_access_token = create_access_token(user_id)
    _set_access_cookie(response, new_access_token)
    _set_refresh_cookie(response, new_token)
    user = await session.scalar(
        select(User)
        .options(joinedload(User.memberships))
        .where(User.id == user_id)
    )

    user = await session.scalar(select(User).join(OrgMembership).join(Organization).where(User.id == user_id).options(joinedload(User.memberships).joinedload(OrgMembership.organization)))
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
    token = request.cookies.get(REFRESH_COOKIE)
    if token:
        await revoke_refresh_token(session, token)
    _clear_refresh_cookie(response)
    _clear_access_cookie(response)
    return {"status": "logged_out"}
