from datetime import datetime

from pydantic import BaseModel


class CallOut(BaseModel):
    id: int
    octell_call_id: str | None
    call_type: str
    manager_folder: str
    manager_full_name: str | None
    manager_domain: str | None
    status: str
    call_started_at: datetime
    duration_seconds: float | None
    parts_count: int
    transcription: str | None = None
    has_transcription: bool = False
    topic: str | None = None
    subtopic: str | None = None
    classification_confidence: float | None = None
    classification_reason: str | None = None
    summary_topic: str | None = None
    summary_outcome: str | None = None
    summary_short: str | None = None


class CallsResponse(BaseModel):
    items: list[CallOut]
    total: int


class UserOut(BaseModel):
    id: int
    full_name: str | None
    domain: str | None
    department: str | None


class UsersResponse(BaseModel):
    items: list[UserOut]
    total: int


class PipelineRunOut(BaseModel):
    id: int
    pipeline_code: str
    status: str
    started_at: datetime
    finished_at: datetime | None
    duration_seconds: int | None
    processed_calls: int
    total_audio_seconds: float | None = None
    avg_rtf: float | None = None
    error_message: str | None


class PipelineRunsResponse(BaseModel):
    items: list[PipelineRunOut]
    total: int


class TopicCatalogEntryOut(BaseModel):
    id: int
    topic_name: str
    subtopic_name: str
    description: str
    keywords_text: str
    synonyms_text: str | None
    negative_keywords_text: str | None
    is_active: bool
    qdrant_point_id: str | None
    updated_at: datetime


class TopicCatalogEntriesResponse(BaseModel):
    items: list[TopicCatalogEntryOut]
    total: int