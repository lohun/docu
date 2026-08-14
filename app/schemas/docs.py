from datetime import datetime

from pydantic import BaseModel


class DocOut(BaseModel):
    id: int
    org_id: int
    source_id: int
    title: str
    slug: str
    current_content_md: str
    version: int
    updated_at: datetime
    git_export_enabled: bool


class DocUpdateOut(BaseModel):
    id: int
    source_id: int
    diff_id: int | None = None
    doc_id: int
    section_key: str
    previous_content: str | None = None
    new_content: str
    llm_model_used: str | None = None
    token_usage: int | None = None
    status: str
    created_at: datetime


class DiffViewOut(BaseModel):
    id: int
    source_id: int
    from_snapshot_id: int | None = None
    to_snapshot_id: int
    diff_type: str
    diff_payload: dict | None = None
    is_trivial: bool
    created_at: datetime
    resulting_update: DocUpdateOut | None = None
