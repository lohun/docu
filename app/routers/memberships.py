from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import get_current_org, get_current_user, require_role
from app.auth.service import (
    InsufficientRoleError,
    OrgAccessError,
    accept_invite_by_token,
    invite_user_to_org,
    remove_membership,
    update_membership_role,
)
from app.db import get_session
from app.models.org_membership import OrgMembership
from app.models.organization import Organization
from app.models.user import User
from app.schemas.memberships import (
    AcceptInviteRequest,
    InviteRequest,
    MembershipOut,
    UpdateRoleRequest,
)

router = APIRouter(prefix="/orgs/{org_id}", tags=["memberships"])


@router.get("/memberships", response_model=list[MembershipOut])
async def list_memberships(
    org_and_role: tuple[Organization, str] = Depends(get_current_org),
    session: AsyncSession = Depends(get_session),
) -> list[MembershipOut]:
    org, role = org_and_role
    require_role("viewer", role)

    query = (
        select(OrgMembership, User.email)
        .join(User, OrgMembership.user_id == User.id)
        .where(OrgMembership.org_id == org.id)
    )
    results = await session.execute(query)
    
    output = []
    for mem, email in results:
        output.append(
            MembershipOut(
                user_id=mem.user_id,
                org_id=mem.org_id,
                role=mem.role,
                email=email,
                invited_at=mem.invited_at,
                accepted_at=mem.accepted_at,
            )
        )
    return output


@router.post("/invites", status_code=201)
async def invite_member(
    payload: InviteRequest,
    org_and_role: tuple[Organization, str] = Depends(get_current_org),
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> dict[str, str]:
    org, role = org_and_role
    require_role("admin", role)

    try:
        token = await invite_user_to_org(
            session=session,
            org_id=org.id,
            inviter_user_id=user.id,
            email=payload.email,
            role=payload.role,
        )
        return {"status": "invited", "token": token or ""}
    except InsufficientRoleError as e:
        raise HTTPException(status_code=403, detail=str(e)) from e


@router.post("/invites/accept")
async def accept_member_invite(
    payload: AcceptInviteRequest,
    session: AsyncSession = Depends(get_session),
) -> dict[str, str]:
    try:
        await accept_invite_by_token(session, payload.token)
        return {"status": "invite_accepted"}
    except OrgAccessError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e


@router.patch("/memberships/{target_user_id}", response_model=MembershipOut)
async def update_member_role(
    target_user_id: int,
    payload: UpdateRoleRequest,
    org_and_role: tuple[Organization, str] = Depends(get_current_org),
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> MembershipOut:
    org, role = org_and_role
    require_role("admin", role)

    try:
        await update_membership_role(
            session=session,
            org_id=org.id,
            target_user_id=target_user_id,
            new_role=payload.role,
            updater_user_id=user.id,
        )
    except InsufficientRoleError as e:
        raise HTTPException(status_code=403, detail=str(e)) from e
    except OrgAccessError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e

    query = (
        select(OrgMembership, User.email)
        .join(User, OrgMembership.user_id == User.id)
        .where(
            OrgMembership.org_id == org.id,
            OrgMembership.user_id == target_user_id,
        )
    )
    result = (await session.execute(query)).first()
    if not result:
        raise HTTPException(status_code=404, detail="membership not found")

    mem, email = result
    return MembershipOut(
        user_id=mem.user_id,
        org_id=mem.org_id,
        role=mem.role,
        email=email,
        invited_at=mem.invited_at,
        accepted_at=mem.accepted_at,
    )


@router.delete("/memberships/{target_user_id}")
async def remove_member(
    target_user_id: int,
    org_and_role: tuple[Organization, str] = Depends(get_current_org),
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> dict[str, str]:
    org, role = org_and_role
    require_role("admin", role)

    try:
        await remove_membership(
            session=session,
            org_id=org.id,
            target_user_id=target_user_id,
            remover_user_id=user.id,
        )
        return {"status": "membership_removed"}
    except InsufficientRoleError as e:
        raise HTTPException(status_code=403, detail=str(e)) from e
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
