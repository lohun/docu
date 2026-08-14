from pydantic import BaseModel, EmailStr, Field


class RegisterRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8, max_length=128)


class UserOut(BaseModel):
    id: int
    email: EmailStr

class OrganizationOut(BaseModel):
    id: int
    name: str
    slug: str


class RegisterResponse(BaseModel):
    user: UserOut
    message: str


class VerifyEmailRequest(BaseModel):
    token: str


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class LoginResponse(BaseModel):
    user: UserOut
    organizations: list[OrganizationOut] = []


class PasswordResetRequest(BaseModel):
    email: EmailStr


class PasswordResetConfirm(BaseModel):
    token: str
    new_password: str = Field(min_length=8, max_length=128)
