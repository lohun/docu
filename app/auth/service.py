import hashlib
import re
import secrets
from datetime import datetime, timedelta, timezone
from typing import Literal

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import joinedload, selectinload

from app.config import get_settings
from app.email import send_password_reset_email, send_verification_email
from app.models.org_membership import OrgMembership
from app.models.organization import Organization
from app.models.refresh_token import RefreshToken
from app.models.user import User
from app.security import hash_password, verify_password


class EmailAlreadyRegisteredError(Exception):
    pass


class InvalidCredentialsError(Exception):
    pass


class EmailNotVerifiedError(Exception):
    pass


class AccountInactiveError(Exception):
    pass


class OrgAccessError(Exception):
    pass


class InsufficientRoleError(Exception):
    pass


Role = Literal["owner", "admin", "member", "viewer"]

ROLE_HIERARCHY: dict[Role, int] = {
    "owner": 4,
    "admin": 3,
    "member": 2,
    "viewer": 1,
}


_DUMMY_HASH = hash_password("dummy-password-for-timing")


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _slug_base(email: str) -> str:
    local_part = email.split("@", 1)[0].lower()
    slug = re.sub(r"[^a-z0-9]+", "-", local_part).strip("-")
    return (slug or "org")[:40]


async def _unique_slug(session: AsyncSession, email: str) -> str:
    base = _slug_base(email)
    candidate = base
    for _ in range(10):
        existing = await session.scalar(select(Organization.id).where(Organization.slug == candidate))
        if existing is None:
            return candidate
        candidate = f"{base}-{secrets.token_hex(3)}"
    raise RuntimeError("could not allocate unique org slug")


async def register_user(session: AsyncSession, email: str, password: str) -> User:
    normalized_email = email.lower()
    existing = await session.scalar(select(User).where(User.email == normalized_email))
    if existing is not None:
        raise EmailAlreadyRegisteredError(normalized_email)

    user = User(email=normalized_email, password_hash=hash_password(password), is_active=True)
    session.add(user)
    await session.flush()

    org = Organization(
        name=normalized_email.split("@", 1)[0],
        slug=await _unique_slug(session, normalized_email),
    )
    session.add(org)
    await session.flush()

    session.add(
        OrgMembership(
            user_id=user.id,
            org_id=org.id,
            role="owner",
            accepted_at=_now(),
        )
    )
    await session.commit()
    await session.refresh(user)
    return user


def generate_verification_token() -> str:
    return secrets.token_urlsafe(32)


def generate_opaque_token() -> str:
    return secrets.token_urlsafe(32)


async def issue_verification(session: AsyncSession, user: User) -> str:
    token = generate_verification_token()
    user.email_verification_token_hash = hashlib.sha256(token.encode()).hexdigest()
    user.email_verification_token_expires_at = _now() + timedelta(hours=24)
    await session.commit()
    link = f"{get_settings().frontend_url}/verify-email?token={token}"
    send_verification_email(user.email, link)
    return token


async def verify_email_token(session: AsyncSession, token: str) -> User | None:
    token_hash = hashlib.sha256(token.encode()).hexdigest()
    user = await session.scalar(
        select(User).where(
            User.email_verification_token_hash == token_hash,
            User.email_verified_at.is_(None),
            User.email_verification_token_expires_at > _now(),
        )
    )
    if user is None:
        return None
    user.email_verified_at = _now()
    user.email_verification_token_hash = None
    user.email_verification_token_expires_at = None
    await session.commit()
    return user


async def authenticate(session: AsyncSession, email: str, password: str) -> User:
    user = await session.scalar(select(User).join(OrgMembership).join(Organization).where(User.email == email.lower()).options(joinedload(User.memberships).joinedload(OrgMembership.organization)))
    if user is None:
        verify_password(password, _DUMMY_HASH)
        raise InvalidCredentialsError()
    if not verify_password(password, user.password_hash):
        raise InvalidCredentialsError()
    if not user.is_active:
        raise AccountInactiveError()
    if user.email_verified_at is None:
        raise EmailNotVerifiedError()
    return user


async def request_password_reset(session: AsyncSession, email: str) -> None:
    user = await session.scalar(select(User).where(User.email == email.lower()))
    if user is None:
        return
    token = generate_opaque_token()
    user.password_reset_token_hash = hashlib.sha256(token.encode()).hexdigest()
    user.password_reset_token_expires_at = _now() + timedelta(hours=1)
    await session.commit()
    link = f"{get_settings().frontend_url}/reset-password?token={token}"
    send_password_reset_email(user.email, link)


async def reset_password(session: AsyncSession, token: str, new_password: str) -> User | None:
    token_hash = hashlib.sha256(token.encode()).hexdigest()
    user = await session.scalar(
        select(User).where(
            User.password_reset_token_hash == token_hash,
            User.password_reset_token_expires_at > _now(),
        )
    )
    if user is None:
        return None
    user.password_hash = hash_password(new_password)
    user.password_reset_token_hash = None
    user.password_reset_token_expires_at = None
    # Invalidate every outstanding access token and refresh session.
    user.token_version = user.token_version + 1
    await session.execute(delete(RefreshToken).where(RefreshToken.user_id == user.id))
    await session.commit()
    return user


async def get_user_role_in_org(session: AsyncSession, user_id: int, org_id: int) -> Role | None:
    result = await session.scalar(
        select(OrgMembership.role).where(
            OrgMembership.user_id == user_id,
            OrgMembership.org_id == org_id,
        )
    )
    return result


def check_role_sufficient(user_role: Role, required_role: Role) -> bool:
    user_level = ROLE_HIERARCHY.get(user_role, 0)
    required_level = ROLE_HIERARCHY.get(required_role, 0)
    return user_level >= required_level


async def verify_org_access(
    session: AsyncSession, user_id: int, org_id: int, required_role: Role
) -> tuple[Organization, Role]:
    user_role = await get_user_role_in_org(session, user_id, org_id)
    if user_role is None:
        raise OrgAccessError("user is not a member of this organization")
    if not check_role_sufficient(user_role, required_role):
        raise OrgAccessError(f"user role '{user_role}' is insufficient for '{required_role}'")
    
    org = await session.get(Organization, org_id)
    if org is None:
        raise OrgAccessError("organization not found")
    
    return org, user_role


async def resolve_org_by_domain(session: AsyncSession, host: str) -> Organization | None:
    """Resolve an org by its verified custom domain (Host header value).

    Only organizations with a verified custom domain are resolvable; an
    unverified domain must never be used to route or serve content.
    """
    hostname = host.split(":", 1)[0].strip().lower()
    if not hostname:
        return None
    return await session.scalar(
        select(Organization).where(
            Organization.custom_domain == hostname,
            Organization.custom_domain_verified_at.is_not(None),
        )
    )


async def invite_user_to_org(
    session: AsyncSession, org_id: int, inviter_user_id: int, email: str, role: Role
) -> None:
    inviter_role = await get_user_role_in_org(session, inviter_user_id, org_id)
    if inviter_role is None or not check_role_sufficient(inviter_role, "admin"):
        raise InsufficientRoleError("only owners and admins can invite users")
    
    normalized_email = email.lower()
    user = await session.scalar(select(User).where(User.email == normalized_email))
    
    if user is None:
        user = User(email=normalized_email, password_hash="", is_active=False)
        session.add(user)
        await session.flush()
    
    existing_membership = await session.scalar(
        select(OrgMembership).where(
            OrgMembership.user_id == user.id,
            OrgMembership.org_id == org_id,
        )
    )
    if existing_membership is not None:
        return  # Already invited or member
    
    membership = OrgMembership(user_id=user.id, org_id=org_id, role=role)
    session.add(membership)
    
    token = generate_opaque_token()
    user.invite_token_hash = hashlib.sha256(token.encode()).hexdigest()
    user.invite_token_expires_at = _now() + timedelta(hours=24)
    user.invited_to_org_id = org_id
    
    await session.commit()
    return token


async def accept_invite(session: AsyncSession, user_id: int, org_id: int) -> None:
    membership = await session.scalar(
        select(OrgMembership).where(
            OrgMembership.user_id == user_id,
            OrgMembership.org_id == org_id,
        )
    )
    if membership is None:
        raise OrgAccessError("no pending invite found")
    
    membership.accepted_at = _now()
    
    user = await session.get(User, user_id)
    if user:
        user.invite_token_hash = None
        user.invite_token_expires_at = None
        user.invited_to_org_id = None
        if not user.password_hash:  # New user, set active
            user.is_active = True
    
    await session.commit()


async def accept_invite_by_token(session: AsyncSession, token: str) -> OrgMembership:
    token_hash = hashlib.sha256(token.encode()).hexdigest()
    user = await session.scalar(
        select(User).where(
            User.invite_token_hash == token_hash,
            User.invite_token_expires_at > _now(),
        )
    )
    if user is None:
        raise OrgAccessError("invalid or expired invite token")
    
    membership = await session.scalar(
        select(OrgMembership).where(
            OrgMembership.user_id == user.id,
            OrgMembership.org_id == user.invited_to_org_id,
        )
    )
    if membership is None:
        raise OrgAccessError("no pending invite found for user")
    
    membership.accepted_at = _now()
    user.invite_token_hash = None
    user.invite_token_expires_at = None
    user.invited_to_org_id = None
    if not user.password_hash:
        user.is_active = True
    
    await session.commit()
    return membership


async def update_membership_role(
    session: AsyncSession, org_id: int, target_user_id: int, new_role: Role, updater_user_id: int
) -> None:
    updater_role = await get_user_role_in_org(session, updater_user_id, org_id)
    if updater_role is None or not check_role_sufficient(updater_role, "admin"):
        raise InsufficientRoleError("only owners and admins can update roles")
    
    membership = await session.scalar(
        select(OrgMembership).where(
            OrgMembership.user_id == target_user_id,
            OrgMembership.org_id == org_id,
        )
    )
    if membership is None:
        raise OrgAccessError("membership not found")
    
    membership.role = new_role
    await session.commit()


async def remove_membership(
    session: AsyncSession, org_id: int, target_user_id: int, remover_user_id: int
) -> None:
    remover_role = await get_user_role_in_org(session, remover_user_id, org_id)
    if remover_role is None or not check_role_sufficient(remover_role, "admin"):
        raise InsufficientRoleError("only owners and admins can remove members")
    
    # Check if this is the last owner
    if remover_role == "owner":
        owner_count = await session.scalar(
            select(func.count()).where(
                OrgMembership.org_id == org_id,
                OrgMembership.role == "owner",
            )
        )
        target_role = await get_user_role_in_org(session, target_user_id, org_id)
        if target_role == "owner" and owner_count == 1:
            raise ValueError("cannot remove the last owner")
    
    await session.execute(
        delete(OrgMembership).where(
            OrgMembership.user_id == target_user_id,
            OrgMembership.org_id == org_id,
        )
    )
    await session.commit()
