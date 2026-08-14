from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, field_validator


class SourceCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=255)
    type: Literal["openapi", "scrape"]
    target_url: str = Field(..., max_length=2048)
    fetch_interval_seconds: int = Field(300, ge=300)
    css_scope_selector: str | None = Field(None, max_length=255)

    @field_validator("target_url")
    @classmethod
    def validate_url(cls, v: str) -> str:
        v = v.strip()
        if not (v.startswith("http://") or v.startswith("https://")):
            raise ValueError("target_url must start with http:// or https://")
        return v


class SourceUpdate(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=255)
    target_url: str | None = Field(None, max_length=2048)
    fetch_interval_seconds: int | None = Field(None, ge=300)
    css_scope_selector: str | None = Field(None, max_length=255)
    is_active: bool | None = None

    @field_validator("target_url")
    @classmethod
    def validate_url(cls, v: str | None) -> str | None:
        if v is not None:
            v = v.strip()
            if not (v.startswith("http://") or v.startswith("https://")):
                raise ValueError("target_url must start with http:// or https://")
        return v


class SourceOut(BaseModel):
    id: int
    org_id: int
    name: str
    type: str
    target_url: str
    fetch_interval_seconds: int
    css_scope_selector: str | None = None
    is_active: bool
    created_at: datetime
