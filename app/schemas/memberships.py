from datetime import datetime
from typing import Literal

from pydantic import BaseModel, EmailStr

Role = Literal["owner", "admin", "member", "viewer"]


class InviteRequest(BaseModel):
    email: EmailStr
    role: Role = "member"


class AcceptInviteRequest(BaseModel):
    token: str


class UpdateRoleRequest(BaseModel):
    role: Role


class MembershipOut(BaseModel):
    user_id: int
    org_id: int
    role: str
    email: str
    invited_at: datetime
    accepted_at: datetime | None = None
