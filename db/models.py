from __future__ import annotations

from datetime import date

from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from db.base import Base
from db.mixins import TimestampMixin


class User(Base, TimestampMixin):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    manager_folder: Mapped[str] = mapped_column(String(255), unique=True, index=True, nullable=False)
    full_name: Mapped[str | None] = mapped_column(String(255))
    domain: Mapped[str | None] = mapped_column(String(100), unique=True, index=True)
    department: Mapped[str | None] = mapped_column(String(255))

    calls: Mapped[list["Call"]] = relationship(back_populates="manager", lazy="selectin")


class CallType(Base, TimestampMixin):
    __tablename__ = "call_types"

    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(20), unique=True, nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    description: Mapped[str | None] = mapped_column(String(255))

    calls: Mapped[list["Call"]] = relationship(back_populates="call_type", lazy="selectin")


class CallStatus(Base, TimestampMixin):
    """Справочник статусов звонка (нормализация; расширение модели — новыми строками здесь)."""

    __tablename__ = "call_statuses"

    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(30), unique=True, nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(100), nullable=False)

    calls: Mapped[list["Call"]] = relationship(back_populates="call_status", lazy="selectin")


class Call(Base, TimestampMixin):
    __tablename__ = "calls"

    __table_args__ = (
        UniqueConstraint("manager_id", "call_type_id", "octell_call_id", name="uq_calls_manager_type_octell"),
        Index("ix_calls_manager_started_at", "manager_id", "call_started_at"),
        Index("ix_calls_started_at", "call_started_at"),
        Index("ix_calls_status_id", "status_id"),
        Index("ix_calls_call_type_status_started", "call_type_id", "status_id", "call_started_at"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)

    # ID звонка Octell: общий для всех частей одного звонка
    octell_call_id: Mapped[str] = mapped_column(String(100), index=True, nullable=False)

    # Для удобства храним имя/путь первой части
    file_name: Mapped[str] = mapped_column(String(255), nullable=False)
    source_file_path: Mapped[str] = mapped_column(String(2048), nullable=False)

    # Дата/время совершения звонка (date modified первой части)
    call_started_at: Mapped[DateTime] = mapped_column(DateTime(timezone=True), nullable=False)
    parts_count: Mapped[int] = mapped_column(Integer, default=1, nullable=False)

    status_id: Mapped[int] = mapped_column(ForeignKey("call_statuses.id"), nullable=False)
    duration_seconds: Mapped[float | None] = mapped_column(Float)
    error_message: Mapped[str | None] = mapped_column(Text)

    manager_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    call_type_id: Mapped[int] = mapped_column(ForeignKey("call_types.id"), nullable=False)
    pipeline_run_id: Mapped[int | None] = mapped_column(ForeignKey("pipeline_runs.id"))

    manager: Mapped["User"] = relationship(back_populates="calls", lazy="selectin")
    call_type: Mapped["CallType"] = relationship(back_populates="calls", lazy="selectin")
    call_status: Mapped["CallStatus"] = relationship(back_populates="calls", lazy="selectin")
    pipeline_run: Mapped["PipelineRun"] = relationship(back_populates="calls", lazy="selectin")

    call_parts: Mapped[list["CallPart"]] = relationship(
        back_populates="call",
        lazy="selectin",
        cascade="all, delete-orphan",
    )

    transcriptions: Mapped[list["Transcription"]] = relationship(
        back_populates="call",
        lazy="selectin",
        cascade="all, delete-orphan",
    )
    summarizations: Mapped[list["Summarization"]] = relationship(
        back_populates="call",
        lazy="selectin",
        cascade="all, delete-orphan",
    )
    classifications: Mapped[list["CallClassification"]] = relationship(
        back_populates="call",
        lazy="selectin",
        cascade="all, delete-orphan",
    )


class CallPart(Base, TimestampMixin):
    __tablename__ = "call_parts"

    __table_args__ = (
        UniqueConstraint("source_file_path", name="uq_call_parts_source_file_path"),
        Index("ix_call_parts_call_id_part_number", "call_id", "part_number"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    call_id: Mapped[int] = mapped_column(ForeignKey("calls.id"), nullable=False, index=True)

    part_number: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    file_name: Mapped[str] = mapped_column(String(255), nullable=False)
    source_file_path: Mapped[str] = mapped_column(String(2048), nullable=False)
    call_started_at: Mapped[DateTime] = mapped_column(DateTime(timezone=True), nullable=False)
    duration_seconds: Mapped[float | None] = mapped_column(Float)

    call: Mapped["Call"] = relationship(back_populates="call_parts", lazy="selectin")


class CallClassification(Base, TimestampMixin):
    __tablename__ = "call_classifications"

    __table_args__ = (
        Index("ix_call_classifications_call_active", "call_id", "is_active"),
        Index("ix_call_classifications_subtopic", "subtopic_name"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    call_id: Mapped[int] = mapped_column(ForeignKey("calls.id"), nullable=False, index=True)
    transcription_id: Mapped[int | None] = mapped_column(ForeignKey("transcriptions.id"), index=True)
    catalog_entry_id: Mapped[int | None] = mapped_column(ForeignKey("topic_catalog_entries.id"), index=True)
    pipeline_run_id: Mapped[int | None] = mapped_column(ForeignKey("pipeline_runs.id"), index=True)

    model_name: Mapped[str] = mapped_column(String(100), nullable=False)
    embedding_model_name: Mapped[str | None] = mapped_column(String(255))
    prompt_version: Mapped[str | None] = mapped_column(String(50))
    classifier_version: Mapped[str | None] = mapped_column(String(50))
    spravochnik_version: Mapped[str | None] = mapped_column(String(50))
    decision_mode: Mapped[str | None] = mapped_column(String(50))

    topic_name: Mapped[str] = mapped_column(String(255), nullable=False)
    subtopic_name: Mapped[str] = mapped_column(String(255), nullable=False)
    confidence: Mapped[float | None] = mapped_column(Float)
    lexical_score: Mapped[float | None] = mapped_column(Float)
    semantic_score: Mapped[float | None] = mapped_column(Float)
    rerank_score: Mapped[float | None] = mapped_column(Float)

    reasoning: Mapped[str | None] = mapped_column(Text)
    evidence_json: Mapped[str | None] = mapped_column(Text)
    candidates_json: Mapped[str | None] = mapped_column(Text)
    raw_llm_output: Mapped[str | None] = mapped_column(Text)

    call: Mapped["Call"] = relationship(back_populates="classifications", lazy="selectin")
    transcription: Mapped["Transcription | None"] = relationship(back_populates="classifications", lazy="selectin")
    catalog_entry: Mapped["TopicCatalogEntry | None"] = relationship(back_populates="classifications", lazy="selectin")
    pipeline_run: Mapped["PipelineRun | None"] = relationship(back_populates="classifications", lazy="selectin")


class TopicCatalogEntry(Base, TimestampMixin):
    __tablename__ = "topic_catalog_entries"

    __table_args__ = (
        UniqueConstraint("topic_name", "subtopic_name", name="uq_topic_catalog_topic_subtopic"),
        Index("ix_topic_catalog_is_active", "is_active"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    topic_name: Mapped[str] = mapped_column(String(255), nullable=False)
    subtopic_name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    keywords_text: Mapped[str] = mapped_column(Text, nullable=False)
    synonyms_text: Mapped[str | None] = mapped_column(Text)
    negative_keywords_text: Mapped[str | None] = mapped_column(Text)
    doc_text: Mapped[str] = mapped_column(Text, nullable=False)
    source_name: Mapped[str | None] = mapped_column(String(100), default="reference_topics.txt")
    source_hash: Mapped[str | None] = mapped_column(String(64))
    qdrant_point_id: Mapped[str | None] = mapped_column(String(100), unique=True)

    classifications: Mapped[list["CallClassification"]] = relationship(back_populates="catalog_entry", lazy="selectin")



class Transcription(Base, TimestampMixin):
    __tablename__ = "transcriptions"

    id: Mapped[int] = mapped_column(primary_key=True)
    call_id: Mapped[int] = mapped_column(ForeignKey("calls.id"), nullable=False, index=True)
    model_name: Mapped[str] = mapped_column(String(100), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    text: Mapped[str] = mapped_column(Text, nullable=False)

    call: Mapped["Call"] = relationship(back_populates="transcriptions", lazy="selectin")
    classifications: Mapped[list["CallClassification"]] = relationship(back_populates="transcription", lazy="selectin")


class Summarization(Base, TimestampMixin):
    __tablename__ = "summarizations"

    id: Mapped[int] = mapped_column(primary_key=True)
    call_id: Mapped[int] = mapped_column(ForeignKey("calls.id"), nullable=False, index=True)

    model_name: Mapped[str] = mapped_column(String(100), nullable=False)
    prompt_version: Mapped[str | None] = mapped_column(String(50))
    temperature: Mapped[float | None] = mapped_column()

    participants: Mapped[str | None] = mapped_column(Text)
    platform: Mapped[str | None] = mapped_column(String(255))
    topic: Mapped[str | None] = mapped_column(String(255))
    essence: Mapped[str | None] = mapped_column(Text)
    action_result: Mapped[str | None] = mapped_column(Text)
    outcome: Mapped[str | None] = mapped_column(Text)
    short_summary: Mapped[str | None] = mapped_column(Text)
    raw_text: Mapped[str | None] = mapped_column(Text)

    call: Mapped["Call"] = relationship(back_populates="summarizations", lazy="selectin")


class PipelineRun(Base, TimestampMixin):
    __tablename__ = "pipeline_runs"

    id: Mapped[int] = mapped_column(primary_key=True)
    pipeline_code: Mapped[str] = mapped_column(String(30), nullable=False, default="UNKNOWN", index=True)
    started_at: Mapped[DateTime] = mapped_column(DateTime(timezone=True), nullable=False)
    finished_at: Mapped[DateTime | None] = mapped_column(DateTime(timezone=True))

    duration_seconds: Mapped[int | None] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(30), nullable=False)
    processed_calls: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    total_audio_seconds: Mapped[float | None] = mapped_column(Float)
    avg_rtf: Mapped[float | None] = mapped_column(Float)
    error_message: Mapped[str | None] = mapped_column(Text)

    calls: Mapped[list["Call"]] = relationship(back_populates="pipeline_run", lazy="selectin")
    classifications: Mapped[list["CallClassification"]] = relationship(back_populates="pipeline_run", lazy="selectin")
    weekly_911_reports: Mapped[list["Weekly911Report"]] = relationship(back_populates="pipeline_run", lazy="selectin")


class Weekly911Report(Base, TimestampMixin):
    """Еженедельный агрегат по 911: счётчики, текст задачи Work, путь к Excel (история для аналитики)."""

    __tablename__ = "weekly_911_reports"

    __table_args__ = (Index("ix_weekly_911_reports_period", "period_start", "period_end"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    period_start: Mapped[date] = mapped_column(Date, nullable=False)
    period_end: Mapped[date] = mapped_column(Date, nullable=False)

    pipeline_run_id: Mapped[int | None] = mapped_column(ForeignKey("pipeline_runs.id"), index=True)
    status: Mapped[str] = mapped_column(String(30), nullable=False, default="RUNNING", index=True)

    calls_in_period: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    calls_summarized_in_period: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    outcome_helped: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    outcome_not_helped: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    outcome_in_progress: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    outcome_unknown: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    task_text: Mapped[str | None] = mapped_column(Text)
    work_task_id: Mapped[int | None] = mapped_column(Integer)
    excel_file_path: Mapped[str | None] = mapped_column(String(2048))
    error_message: Mapped[str | None] = mapped_column(Text)

    pipeline_run: Mapped["PipelineRun | None"] = relationship(back_populates="weekly_911_reports", lazy="selectin")

