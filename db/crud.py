from __future__ import annotations

import json
from datetime import datetime
from threading import Lock

from sqlalchemy import func, select
from sqlalchemy.orm import Session, load_only, selectinload

from .models import (
    Call,
    CallClassification,
    CallPart,
    CallStatus,
    CallType,
    PipelineRun,
    Summarization,
    TopicCatalogEntry,
    Transcription,
    User,
)


def json_dumps(value) -> str | None:
    if value is None:
        return None
    return json.dumps(value, ensure_ascii=False, indent=2)


def parse_multiline_text(value: str | None) -> list[str]:
    if not value:
        return []
    items: list[str] = []
    for raw in value.replace(";", "\n").splitlines():
        item = raw.strip()
        if item:
            items.append(item)
    return items


def compose_catalog_doc_text(topic_name: str, subtopic_name: str, description: str, keywords_text: str, synonyms_text: str | None = None) -> str:
    parts = [topic_name.strip(), subtopic_name.strip(), description.strip(), keywords_text.strip()]
    if synonyms_text:
        parts.append(synonyms_text.strip())
    return " ".join(part for part in parts if part).strip()


def get_or_create_user(
    db: Session,
    manager_folder: str,
    full_name: str | None = None,
    domain: str | None = None,
    department: str | None = None,
) -> User:
    stmt = select(User).where(User.manager_folder == manager_folder)
    user = db.scalar(stmt)

    if user:
        changed = False
        if full_name and user.full_name != full_name:
            user.full_name = full_name
            changed = True
        if domain and user.domain != domain:
            user.domain = domain
            changed = True
        if department and user.department != department:
            user.department = department
            changed = True
        if changed:
            db.commit()
            db.refresh(user)
        return user

    user = User(
        manager_folder=manager_folder,
        full_name=full_name,
        domain=domain,
        department=department,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


_status_id_by_code: dict[str, int] = {}
_status_cache_lock = Lock()


def get_call_status_by_code(db: Session, code: str) -> CallStatus:
    """Кэш id по коду — после нормализации схемы статусы не меняются; снимает лишние SELECT на каждый звонок."""
    with _status_cache_lock:
        sid = _status_id_by_code.get(code)
    if sid is not None:
        row = db.get(CallStatus, sid)
        if row is not None:
            return row
        with _status_cache_lock:
            _status_id_by_code.pop(code, None)

    row = db.scalar(select(CallStatus).where(CallStatus.code == code))
    if row is None:
        raise ValueError(f"Unknown call status code: {code!r}")
    with _status_cache_lock:
        _status_id_by_code[code] = row.id
    return row


def get_or_create_call_type(db: Session, code: str, name: str, description: str | None = None) -> CallType:
    stmt = select(CallType).where(CallType.code == code)
    call_type = db.scalar(stmt)
    if call_type:
        return call_type

    call_type = CallType(code=code, name=name, description=description)
    db.add(call_type)
    db.commit()
    db.refresh(call_type)
    return call_type


def get_call_by_manager_type_octell(db: Session, *, manager_id: int, call_type_id: int, octell_call_id: str) -> Call | None:
    stmt = select(Call).where(
        Call.manager_id == manager_id,
        Call.call_type_id == call_type_id,
        Call.octell_call_id == octell_call_id,
    )
    return db.scalar(stmt)


def get_call_by_source_path(db: Session, source_file_path: str) -> Call | None:
    stmt = select(Call).where(Call.source_file_path == source_file_path)
    call = db.scalar(stmt)
    if call:
        return call

    stmt_part = (
        select(Call)
        .join(CallPart, CallPart.call_id == Call.id)
        .where(CallPart.source_file_path == source_file_path)
    )
    return db.scalar(stmt_part)


def create_or_get_call(
    db: Session,
    *,
    manager_id: int,
    call_type_id: int,
    file_name: str,
    source_file_path: str,
    call_started_at: datetime,
    octell_call_id: str | None,
    duration_seconds: float | None,
    status: str = "NEW",
) -> Call:
    octell_value = (octell_call_id or "").strip() or f"NO_OCTELL::{file_name}"

    existing = get_call_by_manager_type_octell(
        db,
        manager_id=manager_id,
        call_type_id=call_type_id,
        octell_call_id=octell_value,
    )
    if existing:
        return existing

    status_row = get_call_status_by_code(db, status)
    call = Call(
        manager_id=manager_id,
        call_type_id=call_type_id,
        file_name=file_name,
        source_file_path=source_file_path,
        call_started_at=call_started_at,
        octell_call_id=octell_value,
        parts_count=1,
        duration_seconds=duration_seconds,
        status_id=status_row.id,
    )
    db.add(call)
    db.commit()
    db.refresh(call)
    return call


def add_call_part(
    db: Session,
    *,
    call_id: int,
    part_number: int | None,
    file_name: str,
    source_file_path: str,
    call_started_at: datetime,
    duration_seconds: float | None,
) -> CallPart:
    existing = db.scalar(select(CallPart).where(CallPart.source_file_path == source_file_path))
    if existing:
        return existing

    final_part_number = part_number
    if final_part_number is None:
        max_part = db.scalar(select(func.max(CallPart.part_number)).where(CallPart.call_id == call_id))
        final_part_number = int(max_part or 0) + 1

    part = CallPart(
        call_id=call_id,
        part_number=final_part_number,
        file_name=file_name,
        source_file_path=source_file_path,
        call_started_at=call_started_at,
        duration_seconds=duration_seconds,
    )
    db.add(part)
    db.commit()
    db.refresh(part)
    return part


def refresh_call_rollups(db: Session, call_id: int) -> Call | None:
    """Пересчёт агрегатов по частям без загрузки всех строк CallPart в память (быстрее при большом числе частей)."""
    call = db.get(Call, call_id)
    if not call:
        return None

    n = db.scalar(select(func.count(CallPart.id)).where(CallPart.call_id == call_id)) or 0
    total_dur = db.scalar(
        select(func.coalesce(func.sum(CallPart.duration_seconds), 0.0)).where(CallPart.call_id == call_id)
    )
    total_float = float(total_dur) if total_dur is not None else 0.0

    if int(n) > 0:
        call.parts_count = int(n)
        call.duration_seconds = total_float if total_float > 0 else None
        first_part = db.scalar(
            select(CallPart)
            .where(CallPart.call_id == call_id)
            .order_by(CallPart.call_started_at.asc(), CallPart.part_number.asc())
            .limit(1)
        )
        if first_part:
            call.call_started_at = first_part.call_started_at
            call.file_name = first_part.file_name
            call.source_file_path = first_part.source_file_path
    else:
        call.parts_count = 1

    db.commit()
    db.refresh(call)
    return call


def set_call_status(db: Session, call_id: int, status: str, error_message: str | None = None) -> Call | None:
    call = db.get(Call, call_id)
    if not call:
        return None

    call.status_id = get_call_status_by_code(db, status).id
    call.error_message = error_message
    db.commit()
    db.refresh(call)
    return call


def get_calls_for_transcription(
    db: Session,
    *,
    call_type_code: str,
    manager_id: int | None = None,
    statuses: tuple[str, ...] = ("NEW", "FAILED", "TRANSCRIPTION_FAILED"),
    limit: int = 10000,
) -> list[Call]:
    stmt = (
        select(Call)
        .join(CallType, Call.call_type_id == CallType.id)
        .join(CallStatus, Call.status_id == CallStatus.id)
        .options(selectinload(Call.manager), selectinload(Call.call_type), selectinload(Call.call_parts))
        .where(CallType.code == call_type_code, CallStatus.code.in_(statuses))
        .order_by(Call.call_started_at.asc())
        .limit(limit)
    )
    if manager_id is not None:
        stmt = stmt.where(Call.manager_id == manager_id)
    return list(db.scalars(stmt))


def get_calls_for_summarization(
    db: Session,
    *,
    call_type_code: str,
    statuses: tuple[str, ...] = ("TRANSCRIBED", "SUMMARIZATION_FAILED"),
    limit: int = 200,
) -> list[Call]:
    stmt = (
        select(Call)
        .join(CallType, Call.call_type_id == CallType.id)
        .join(CallStatus, Call.status_id == CallStatus.id)
        .options(
            selectinload(Call.manager),
            selectinload(Call.call_type),
            selectinload(Call.call_parts),
            selectinload(Call.transcriptions),
            selectinload(Call.summarizations),
        )
        .where(CallType.code == call_type_code, CallStatus.code.in_(statuses))
        .order_by(Call.call_started_at.asc())
        .limit(limit)
    )
    return list(db.scalars(stmt))


def get_calls_for_classification(
    db: Session,
    *,
    call_type_code: str | None = None,
    statuses: tuple[str, ...] = ("TRANSCRIBED", "CLASSIFICATION_FAILED"),
    limit: int = 1000,
) -> list[Call]:
    stmt = (
        select(Call)
        .options(
            selectinload(Call.manager),
            selectinload(Call.call_type),
            selectinload(Call.call_parts),
            selectinload(Call.transcriptions),
            selectinload(Call.classifications),
        )
        .order_by(Call.call_started_at.asc())
        .limit(limit)
    )
    if statuses:
        stmt = stmt.join(CallStatus, Call.status_id == CallStatus.id).where(CallStatus.code.in_(statuses))
    if call_type_code:
        stmt = stmt.join(CallType, Call.call_type_id == CallType.id).where(CallType.code == call_type_code)
    return list(db.scalars(stmt))


def add_transcription(db: Session, *, call_id: int, model_name: str, text: str, deactivate_previous: bool = True) -> Transcription:
    if deactivate_previous:
        stmt = select(Transcription).where(Transcription.call_id == call_id, Transcription.is_active.is_(True))
        for row in db.scalars(stmt):
            row.is_active = False

    transcription = Transcription(call_id=call_id, model_name=model_name, text=text, is_active=True)
    db.add(transcription)
    db.commit()
    db.refresh(transcription)
    return transcription


def add_summarization(
    db: Session,
    *,
    call_id: int,
    model_name: str,
    prompt_version: str | None,
    temperature: float | None,
    participants: str | None,
    platform: str | None,
    topic: str | None,
    essence: str | None,
    action_result: str | None,
    outcome: str | None,
    short_summary: str | None,
    raw_text: str | None,
) -> Summarization:
    summarization = Summarization(
        call_id=call_id,
        model_name=model_name,
        prompt_version=prompt_version,
        temperature=temperature,
        participants=participants,
        platform=platform,
        topic=topic,
        essence=essence,
        action_result=action_result,
        outcome=outcome,
        short_summary=short_summary,
        raw_text=raw_text,
    )
    db.add(summarization)
    db.commit()
    db.refresh(summarization)
    return summarization


def list_topic_catalog_entries(db: Session, *, include_inactive: bool = False) -> list[TopicCatalogEntry]:
    stmt = select(TopicCatalogEntry).order_by(TopicCatalogEntry.topic_name.asc(), TopicCatalogEntry.subtopic_name.asc())
    if not include_inactive:
        stmt = stmt.where(TopicCatalogEntry.is_active.is_(True))
    return list(db.scalars(stmt))


def get_topic_catalog_entry(db: Session, entry_id: int) -> TopicCatalogEntry | None:
    return db.get(TopicCatalogEntry, entry_id)


def get_topic_catalog_entry_by_names(db: Session, topic_name: str, subtopic_name: str) -> TopicCatalogEntry | None:
    stmt = select(TopicCatalogEntry).where(
        TopicCatalogEntry.topic_name == topic_name,
        TopicCatalogEntry.subtopic_name == subtopic_name,
    )
    return db.scalar(stmt)


def upsert_topic_catalog_entry(
    db: Session,
    *,
    topic_name: str,
    subtopic_name: str,
    description: str,
    keywords_text: str,
    synonyms_text: str | None = None,
    negative_keywords_text: str | None = None,
    source_name: str | None = "reference_topics.txt",
    source_hash: str | None = None,
    is_active: bool = True,
) -> TopicCatalogEntry:
    entry = get_topic_catalog_entry_by_names(db, topic_name=topic_name, subtopic_name=subtopic_name)
    doc_text = compose_catalog_doc_text(topic_name, subtopic_name, description, keywords_text, synonyms_text)

    if entry:
        entry.description = description
        entry.keywords_text = keywords_text
        entry.synonyms_text = synonyms_text
        entry.negative_keywords_text = negative_keywords_text
        entry.doc_text = doc_text
        entry.source_name = source_name
        entry.source_hash = source_hash
        entry.is_active = is_active
        db.commit()
        db.refresh(entry)
        return entry

    entry = TopicCatalogEntry(
        topic_name=topic_name,
        subtopic_name=subtopic_name,
        description=description,
        keywords_text=keywords_text,
        synonyms_text=synonyms_text,
        negative_keywords_text=negative_keywords_text,
        doc_text=doc_text,
        source_name=source_name,
        source_hash=source_hash,
        is_active=is_active,
    )
    db.add(entry)
    db.commit()
    db.refresh(entry)
    return entry


def update_topic_catalog_entry(
    db: Session,
    *,
    entry_id: int,
    topic_name: str,
    subtopic_name: str,
    description: str,
    keywords_text: str,
    synonyms_text: str | None,
    negative_keywords_text: str | None,
    is_active: bool,
) -> TopicCatalogEntry:
    entry = db.get(TopicCatalogEntry, entry_id)
    if not entry:
        raise ValueError(f"Catalog entry not found: {entry_id}")

    entry.topic_name = topic_name
    entry.subtopic_name = subtopic_name
    entry.description = description
    entry.keywords_text = keywords_text
    entry.synonyms_text = synonyms_text
    entry.negative_keywords_text = negative_keywords_text
    entry.doc_text = compose_catalog_doc_text(topic_name, subtopic_name, description, keywords_text, synonyms_text)
    entry.is_active = is_active
    db.commit()
    db.refresh(entry)
    return entry


def mark_missing_catalog_entries_inactive(db: Session, active_pairs: set[tuple[str, str]], source_name: str = "reference_topics.txt") -> int:
    stmt = select(TopicCatalogEntry).where(TopicCatalogEntry.source_name == source_name)
    changed = 0
    for entry in db.scalars(stmt):
        key = (entry.topic_name, entry.subtopic_name)
        should_be_active = key in active_pairs
        if entry.is_active != should_be_active:
            entry.is_active = should_be_active
            changed += 1
    if changed:
        db.commit()
    return changed


def mark_missing_catalog_entries_inactive_entries(
    db: Session,
    *,
    active_pairs: set[tuple[str, str]],
    source_name: str = "reference_topics.txt",
) -> list[TopicCatalogEntry]:
    """
    Marks entries as active/inactive based on active_pairs and returns only changed rows.

    Use this when caller needs to propagate is_active to external systems (e.g. Qdrant).
    """
    stmt = select(TopicCatalogEntry).where(TopicCatalogEntry.source_name == source_name)
    changed: list[TopicCatalogEntry] = []
    for entry in db.scalars(stmt):
        key = (entry.topic_name, entry.subtopic_name)
        should_be_active = key in active_pairs
        if entry.is_active != should_be_active:
            entry.is_active = should_be_active
            changed.append(entry)
    if changed:
        db.commit()
        for entry in changed:
            db.refresh(entry)
    return changed


def set_catalog_qdrant_point_id(db: Session, entry_id: int, point_id: str) -> TopicCatalogEntry | None:
    entry = db.get(TopicCatalogEntry, entry_id)
    if not entry:
        return None
    entry.qdrant_point_id = point_id
    db.commit()
    db.refresh(entry)
    return entry


def get_active_catalog_entries(db: Session) -> list[TopicCatalogEntry]:
    return list_topic_catalog_entries(db, include_inactive=False)


def add_call_classification(
    db: Session,
    *,
    call_id: int,
    transcription_id: int | None,
    catalog_entry_id: int | None,
    pipeline_run_id: int | None,
    model_name: str,
    embedding_model_name: str | None,
    prompt_version: str | None,
    classifier_version: str | None,
    spravochnik_version: str | None,
    decision_mode: str | None,
    topic_name: str,
    subtopic_name: str,
    confidence: float | None,
    lexical_score: float | None,
    semantic_score: float | None,
    rerank_score: float | None,
    reasoning: str | None,
    evidence: list | dict | None,
    candidates: list | dict | None,
    raw_llm_output: str | None,
    deactivate_previous: bool = True,
) -> CallClassification:
    if deactivate_previous:
        stmt = select(CallClassification).where(
            CallClassification.call_id == call_id,
            CallClassification.is_active.is_(True),
        )
        for row in db.scalars(stmt):
            row.is_active = False

    item = CallClassification(
        call_id=call_id,
        transcription_id=transcription_id,
        catalog_entry_id=catalog_entry_id,
        pipeline_run_id=pipeline_run_id,
        model_name=model_name,
        embedding_model_name=embedding_model_name,
        prompt_version=prompt_version,
        classifier_version=classifier_version,
        spravochnik_version=spravochnik_version,
        decision_mode=decision_mode,
        topic_name=topic_name,
        subtopic_name=subtopic_name,
        confidence=confidence,
        lexical_score=lexical_score,
        semantic_score=semantic_score,
        rerank_score=rerank_score,
        reasoning=reasoning,
        evidence_json=json_dumps(evidence),
        candidates_json=json_dumps(candidates),
        raw_llm_output=raw_llm_output,
        is_active=True,
    )
    db.add(item)
    db.commit()
    db.refresh(item)
    return item


def get_active_classification_for_call(db: Session, call_id: int) -> CallClassification | None:
    stmt = (
        select(CallClassification)
        .where(CallClassification.call_id == call_id, CallClassification.is_active.is_(True))
        .order_by(CallClassification.created_at.desc())
    )
    return db.scalar(stmt)


def _classified_calls_base_stmt(
    *,
    role_call_types: set[str],
    date_from: datetime | None = None,
    date_to: datetime | None = None,
    manager: str | None = None,
    topic: str | None = None,
    subtopic: str | None = None,
):
    stmt = (
        select(Call)
        .join(CallType, Call.call_type_id == CallType.id)
        .join(User, Call.manager_id == User.id)
        .join(CallStatus, Call.status_id == CallStatus.id)
        .join(
            CallClassification,
            (CallClassification.call_id == Call.id) & (CallClassification.is_active.is_(True)),
        )
        .where(CallType.code.in_(role_call_types), CallStatus.code == "CLASSIFIED")
    )
    if date_from:
        stmt = stmt.where(Call.call_started_at >= date_from)
    if date_to:
        stmt = stmt.where(Call.call_started_at <= date_to)
    if manager:
        stmt = stmt.where(User.full_name == manager)
    if topic:
        stmt = stmt.where(CallClassification.topic_name == topic)
    if subtopic:
        stmt = stmt.where(CallClassification.subtopic_name == subtopic)
    return stmt


def count_calls_with_active_classification(
    db: Session,
    *,
    role_call_types: set[str],
    date_from: datetime | None = None,
    date_to: datetime | None = None,
    manager: str | None = None,
    topic: str | None = None,
    subtopic: str | None = None,
) -> int:
    """Число звонков с активной классификацией по тем же фильтрам, что и list_calls_with_active_classification."""
    stmt = (
        select(func.count(func.distinct(Call.id)))
        .select_from(Call)
        .join(CallType, Call.call_type_id == CallType.id)
        .join(User, Call.manager_id == User.id)
        .join(
            CallClassification,
            (CallClassification.call_id == Call.id) & (CallClassification.is_active.is_(True)),
        )
        .join(CallStatus, Call.status_id == CallStatus.id)
        .where(CallType.code.in_(role_call_types), CallStatus.code == "CLASSIFIED")
    )
    if date_from:
        stmt = stmt.where(Call.call_started_at >= date_from)
    if date_to:
        stmt = stmt.where(Call.call_started_at <= date_to)
    if manager:
        stmt = stmt.where(User.full_name == manager)
    if topic:
        stmt = stmt.where(CallClassification.topic_name == topic)
    if subtopic:
        stmt = stmt.where(CallClassification.subtopic_name == subtopic)
    return int(db.scalar(stmt) or 0)


def list_calls_with_active_classification(
    db: Session,
    *,
    role_call_types: set[str],
    limit: int = 200,
    offset: int = 0,
    date_from: datetime | None = None,
    date_to: datetime | None = None,
    manager: str | None = None,
    topic: str | None = None,
    subtopic: str | None = None,
    load_transcription_text: bool = False,
) -> list[Call]:
    stmt = _classified_calls_base_stmt(
        role_call_types=role_call_types,
        date_from=date_from,
        date_to=date_to,
        manager=manager,
        topic=topic,
        subtopic=subtopic,
    )
    if load_transcription_text:
        load_opts = (
            selectinload(Call.call_type),
            selectinload(Call.manager),
            selectinload(Call.call_parts),
            selectinload(Call.call_status),
            selectinload(Call.transcriptions),
            selectinload(Call.classifications).selectinload(CallClassification.catalog_entry),
        )
    else:
        load_opts = (
            selectinload(Call.call_type),
            selectinload(Call.manager),
            selectinload(Call.call_parts),
            selectinload(Call.call_status),
            selectinload(Call.transcriptions).load_only(
                Transcription.id,
                Transcription.call_id,
                Transcription.is_active,
                Transcription.model_name,
            ),
            selectinload(Call.classifications).load_only(
                CallClassification.id,
                CallClassification.call_id,
                CallClassification.is_active,
                CallClassification.topic_name,
                CallClassification.subtopic_name,
                CallClassification.confidence,
                CallClassification.reasoning,
            ),
        )
    stmt = (
        stmt.options(*load_opts)
        .order_by(Call.call_started_at.desc())
        .offset(max(0, offset))
        .limit(limit)
    )
    # join with classifications can duplicate rows; unique() keeps one Call entity per id
    return list(db.scalars(stmt).unique())


def create_pipeline_run(db: Session, *, started_at: datetime, status: str, pipeline_code: str) -> PipelineRun:
    run = PipelineRun(started_at=started_at, status=status, pipeline_code=pipeline_code, processed_calls=0)
    db.add(run)
    db.commit()
    db.refresh(run)
    return run


def finish_pipeline_run(
    db: Session,
    *,
    pipeline_run_id: int,
    status: str,
    finished_at: datetime,
    processed_calls: int,
    duration_seconds: int | None,
    error_message: str | None = None,
    total_audio_seconds: float | None = None,
    avg_rtf: float | None = None,
) -> PipelineRun | None:
    run = db.get(PipelineRun, pipeline_run_id)
    if not run:
        return None
    # Уже завершён (в т.ч. по INTERRUPTED из обработчика сигнала) — не перезаписываем
    if run.finished_at is not None:
        return run
    run.status = status
    run.finished_at = finished_at
    run.processed_calls = processed_calls
    run.duration_seconds = duration_seconds
    run.error_message = error_message
    if total_audio_seconds is not None and hasattr(run, "total_audio_seconds"):
        run.total_audio_seconds = total_audio_seconds
    if avg_rtf is not None and hasattr(run, "avg_rtf"):
        run.avg_rtf = avg_rtf
    db.commit()
    db.refresh(run)
    return run


def get_calls_for_day(db: Session, day_start: datetime, day_end: datetime, limit: int = 5000) -> list[Call]:
    stmt = (
        select(Call)
        .where(Call.call_started_at >= day_start, Call.call_started_at < day_end)
        .order_by(Call.call_started_at.asc())
        .limit(limit)
    )
    return list(db.scalars(stmt))