from pydantic import BaseModel
from typing import Literal


Role = Literal["owner", "admin", "member", "viewer"]


class OrgContext(BaseModel):
    org_id: int
    org_name: str
    org_slug: str
    role: Role


class UserContext(BaseModel):
    user_id: int
    email: str
    is_active: bool
