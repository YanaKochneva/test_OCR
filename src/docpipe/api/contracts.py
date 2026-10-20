"""Public asynchronous API contracts for platform integrations."""

from typing import Literal

from pydantic import BaseModel

from docpipe.ir import Document


class JobStatus(BaseModel):
    job_id: str
    request_id: str | None
    status: Literal["queued", "running", "completed", "failed", "cancelled"]
    progress: int
    pages_total: int
    review_required: bool
    error: str | None
    created_at: float
    started_at: float | None
    finished_at: float | None
    expires_at: float | None
    links: dict[str, str]


class AgentDocument(BaseModel):
    job_id: str
    request_id: str | None
    review_required: bool
    text: str
    document: Document
