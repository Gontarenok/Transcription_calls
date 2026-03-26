from sqlalchemy import (
    String,
    ForeignKey,
    Text,
    DateTime,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from db.base import Base
from db.mixins import TimestampMixin

class User(Base, TimestampMixin):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    full_name: Mapped[str] = mapped_column(String(255), nullable=False)
    domain: Mapped[str] = mapped_column(String(100), nullable=False)
    department: Mapped[str | None] = mapped_column(String(255))

    calls: Mapped[list["Call"]] = relationship(
        back_populates="manager",
        lazy="selectin",
    )

class CallType(Base, TimestampMixin):
    __tablename__ = "call_types"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(50), unique=True, nullable=False)
    description: Mapped[str | None] = mapped_column(String(255))

    calls: Mapped[list["Call"]] = relationship(
        back_populates="call_type",
        lazy="selectin",
    )

class Call(Base, TimestampMixin):
    __tablename__ = "calls"

    id: Mapped[int] = mapped_column(primary_key=True)

    # ID звонка из Octell (уникальный)
    octell_call_id: Mapped[str] = mapped_column(
        String(100),
        unique=True,
        index=True,
        nullable=False,
    )

    file_name: Mapped[str] = mapped_column(String(255), nullable=False)

    call_started_at: Mapped[DateTime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )

    status: Mapped[str] = mapped_column(
        String(30),
        default="NEW",  # NEW / TRANSCRIBING / TRANSCRIBED / FAILED
        nullable=False,
    )

    duration_seconds: Mapped[int | None] = mapped_column()

    # Whisper transcript
    # transcript: Mapped[str | None] = mapped_column(Text)

    # Связи
    manager_id: Mapped[int] = mapped_column(
        ForeignKey("users.id"),
        nullable=False,
    )

    call_type_id: Mapped[int] = mapped_column(
        ForeignKey("call_types.id"),
        nullable=False,
    )

    pipeline_run_id: Mapped[int | None] = mapped_column(
        ForeignKey("pipeline_runs.id"),
    )

    manager: Mapped["User"] = relationship(
        back_populates="calls",
        lazy="selectin",
    )

    call_type: Mapped["CallType"] = relationship(
        back_populates="calls",
        lazy="selectin",
    )

    pipeline_run: Mapped["PipelineRun"] = relationship(
        back_populates="calls",
        lazy="selectin",
    )

    transcriptions: Mapped[list["Transcription"]] = relationship(
        back_populates="call",
        lazy="selectin",
    )

    summarizations: Mapped[list["Summarization"]] = relationship(
        back_populates="call",
        lazy="selectin",
    )


class Transcription(Base, TimestampMixin):
    __tablename__ = "transcriptions"

    id: Mapped[int] = mapped_column(primary_key=True)

    call_id: Mapped[int] = mapped_column(
        ForeignKey("calls.id"),
        nullable=False,
    )

    model_name: Mapped[str] = mapped_column(
        String(100),
        nullable=False,
    )


    text: Mapped[str] = mapped_column(
        Text,
        nullable=False,
    )

    call: Mapped["Call"] = relationship(
        back_populates="transcriptions",
        lazy="selectin",
    )

class Summarization(Base, TimestampMixin):
    __tablename__ = "summarizations"

    id: Mapped[int] = mapped_column(primary_key=True)

    call_id: Mapped[int] = mapped_column(
        ForeignKey("calls.id"),
        nullable=False,
    )

    # Метаданные модели
    model_name: Mapped[str] = mapped_column(
        String(100),
        nullable=False,
    )
    prompt_version: Mapped[str | None] = mapped_column(
        String(50)
    )
    temperature: Mapped[float | None] = mapped_column()

    # Результаты

    participants: Mapped[str | None] = mapped_column(Text)
    platform: Mapped[str | None] = mapped_column(String(255))
    topic: Mapped[str | None] = mapped_column(String(255))
    essence: Mapped[str | None] = mapped_column(Text)
    action_result: Mapped[str | None] = mapped_column(Text)
    outcome: Mapped[str | None] = mapped_column(Text)
    short_summary: Mapped[str | None] = mapped_column(Text)

    call: Mapped["Call"] = relationship(
        back_populates="summarizations",
        lazy="selectin",
    )

class PipelineRun(Base, TimestampMixin):
    __tablename__ = "pipeline_runs"

    id: Mapped[int] = mapped_column(primary_key=True)

    started_at: Mapped[DateTime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )

    finished_at: Mapped[DateTime | None] = mapped_column(
        DateTime(timezone=True),
    )

    duration_seconds: Mapped[int | None] = mapped_column()

    status: Mapped[str] = mapped_column(
        String(30),  # running / success / failed
        nullable=False,
    )

    processed_calls: Mapped[int] = mapped_column(
        Integer,
        default=0,
        nullable=False,
    )

    error_message: Mapped[str | None] = mapped_column(Text)

    calls: Mapped[list["Call"]] = relationship(
        back_populates="pipeline_run",
        lazy="selectin",
    )