from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone
from io import BytesIO

from fastapi import Depends, FastAPI, Form, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse, StreamingResponse
from fastapi.templating import Jinja2Templates
from openpyxl import Workbook
from sqlalchemy import func, select
from sqlalchemy.orm import selectinload

from api_service.auth import ROLE_911, ROLE_ADMIN, ROLE_KC, allowed_call_types_for_role, get_current_role, resolve_role_by_key
from api_service.schemas import CallOut, CallsResponse, PipelineRunOut, PipelineRunsResponse, TopicCatalogEntriesResponse, TopicCatalogEntryOut, UserOut, UsersResponse
from db.base import SessionLocal
from db.crud import list_calls_with_active_classification, list_topic_catalog_entries, set_catalog_qdrant_point_id, upsert_topic_catalog_entry, update_topic_catalog_entry
from db.models import Call, CallClassification, CallType, PipelineRun, TopicCatalogEntry, User
from rag.catalog_service import entry_source_hash, sync_catalog_entries

app = FastAPI(title="Audio Calls API", version="0.6.0")
templates = Jinja2Templates(directory="api_service/templates")

STATUS_OPTIONS = ["NEW", "TRANSCRIBING", "TRANSCRIBED", "CLASSIFYING", "CLASSIFIED", "CLASSIFICATION_FAILED", "FAILED", "SUMMARIZED"]


def resolve_period(period: str | None) -> tuple[datetime | None, datetime | None]:
    if not period:
        return None, None
    today = date.today()
    if period == "today":
        return datetime.combine(today, time.min).replace(tzinfo=timezone.utc), datetime.combine(today, time.max).replace(tzinfo=timezone.utc)
    if period == "yesterday":
        d = today - timedelta(days=1)
        return datetime.combine(d, time.min).replace(tzinfo=timezone.utc), datetime.combine(d, time.max).replace(tzinfo=timezone.utc)
    if period == "week_current":
        start = today - timedelta(days=today.weekday())
        end = start + timedelta(days=6)
        return datetime.combine(start, time.min).replace(tzinfo=timezone.utc), datetime.combine(end, time.max).replace(tzinfo=timezone.utc)
    if period == "week_prev":
        end = today - timedelta(days=today.weekday() + 1)
        start = end - timedelta(days=6)
        return datetime.combine(start, time.min).replace(tzinfo=timezone.utc), datetime.combine(end, time.max).replace(tzinfo=timezone.utc)
    if period == "month_current":
        start = today.replace(day=1)
        next_month = start.replace(year=start.year + 1, month=1) if start.month == 12 else start.replace(month=start.month + 1)
        end = next_month - timedelta(days=1)
        return datetime.combine(start, time.min).replace(tzinfo=timezone.utc), datetime.combine(end, time.max).replace(tzinfo=timezone.utc)
    if period == "month_prev":
        cur_start = today.replace(day=1)
        prev_end = cur_start - timedelta(days=1)
        prev_start = prev_end.replace(day=1)
        return datetime.combine(prev_start, time.min).replace(tzinfo=timezone.utc), datetime.combine(prev_end, time.max).replace(tzinfo=timezone.utc)
    return None, None


def normalize_call_type_filter(value: str | None) -> str | None:
    if not value:
        return None
    v = value.upper()
    # Canonical code in system is "КЦ" (Cyrillic).
    # Backward-compat: accept "KЦ"/"KC" from older scripts/UI.
    if v in {"KЦ", "КЦ", "KC"}:
        return "КЦ"
    return v


def parse_date_only(d: str | None, end_of_day: bool = False) -> datetime | None:
    if not d:
        return None
    day = datetime.strptime(d, "%Y-%m-%d").date()
    return datetime.combine(day, time.max if end_of_day else time.min).replace(tzinfo=timezone.utc)


def choose_date_range(period: str | None, date_from: str | None, date_to: str | None) -> tuple[datetime | None, datetime | None, str | None]:
    if period:
        start, end = resolve_period(period)
        return start, end, None

    custom_from = parse_date_only(date_from, end_of_day=False)
    custom_to = parse_date_only(date_to, end_of_day=True)
    if custom_from and custom_to and custom_to < custom_from:
        return custom_from, custom_to, "Дата 'по' не может быть раньше даты 'с'"
    return custom_from, custom_to, None


def build_calls_query(*, role: str, date_from: datetime | None, date_to: datetime | None, manager: str | None, status: str | None, call_type: str | None):
    allowed = allowed_call_types_for_role(role)
    query = (
        select(Call)
        .join(CallType, Call.call_type_id == CallType.id)
        .join(User, Call.manager_id == User.id)
        .options(
            selectinload(Call.call_type),
            selectinload(Call.manager),
            selectinload(Call.transcriptions),
            selectinload(Call.classifications),
        )
        .where(CallType.code.in_(allowed))
    )

    ct = normalize_call_type_filter(call_type)
    if ct:
        query = query.where(CallType.code == ct)
    if date_from:
        query = query.where(Call.call_started_at >= date_from)
    if date_to:
        query = query.where(Call.call_started_at <= date_to)
    if manager:
        query = query.where(User.full_name == manager)
    if status:
        query = query.where(Call.status == status)
    return query


def users_department_for_role(role: str) -> str | None:
    if role == ROLE_911:
        return "911"
    if role == ROLE_KC:
        return "Contact Center"
    return None


def get_active_transcription(call: Call):
    return next((t for t in call.transcriptions if t.is_active), None)


def get_active_classification(call: Call):
    return next((c for c in call.classifications if c.is_active), None)


def safe_parts_count(call: Call) -> int:
    return getattr(call, "parts_count", 1)


def call_to_out(call: Call, include_text: bool) -> CallOut:
    active_trans = get_active_transcription(call)
    active_class = get_active_classification(call)
    return CallOut(
        id=call.id,
        octell_call_id=call.octell_call_id,
        call_type=call.call_type.code,
        manager_folder=call.manager.manager_folder,
        manager_full_name=call.manager.full_name,
        manager_domain=call.manager.domain,
        status=call.status,
        call_started_at=call.call_started_at,
        duration_seconds=call.duration_seconds,
        parts_count=safe_parts_count(call),
        transcription=active_trans.text if (active_trans and include_text) else None,
        topic=active_class.topic_name if active_class else None,
        subtopic=active_class.subtopic_name if active_class else None,
        classification_confidence=active_class.confidence if active_class else None,
        classification_reason=active_class.reasoning if active_class else None,
    )


def menu_context(api_key: str, role: str, active: str) -> dict:
    return {"api_key": api_key, "role": role, "active": active}


@app.get("/", response_class=HTMLResponse)
def login_page(request: Request, api_key: str | None = Query(default=None)):
    role = resolve_role_by_key(api_key)
    return templates.TemplateResponse("login.html", {"request": request, "api_key": api_key or "", "role": role})


@app.get("/calls", response_class=HTMLResponse)
def calls_ui(
    request: Request,
    api_key: str | None = Query(default=None),
    period: str | None = Query(default=None),
    date_from: str | None = Query(default=None),
    date_to: str | None = Query(default=None),
    manager: str | None = Query(default=None),
    status: str | None = Query(default=None),
    call_type: str | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=2000),
):
    role = resolve_role_by_key(api_key)
    if not role:
        return templates.TemplateResponse("login.html", {"request": request, "api_key": "", "role": None, "error": "Неверный API ключ"})

    final_from, final_to, date_error = choose_date_range(period, date_from, date_to)

    db = SessionLocal()
    try:
        query = build_calls_query(role=role, date_from=final_from, date_to=final_to, manager=manager, status=status, call_type=call_type)
        calls = list(db.scalars(query.order_by(Call.call_started_at.desc()).limit(limit)))
        rows = [call_to_out(c, include_text=True) for c in calls]

        manager_stmt = (
            select(User.full_name)
            .join(Call, Call.manager_id == User.id)
            .join(CallType, Call.call_type_id == CallType.id)
            .where(CallType.code.in_(allowed_call_types_for_role(role)), User.full_name.is_not(None))
            .distinct()
            .order_by(User.full_name.asc())
        )
        managers = [m for m in db.scalars(manager_stmt) if m]

        return templates.TemplateResponse(
            "calls.html",
            {
                "request": request,
                "rows": rows,
                "role": role,
                "managers": managers,
                "status_options": STATUS_OPTIONS,
                "date_error": date_error,
                "menu": menu_context(api_key or "", role, "calls"),
                "filters": {
                    "api_key": api_key,
                    "period": period or "",
                    "date_from": date_from or "",
                    "date_to": date_to or "",
                    "manager": manager or "",
                    "status": status or "",
                    "call_type": normalize_call_type_filter(call_type) or "",
                    "limit": limit,
                },
            },
        )
    finally:
        db.close()


@app.get("/classified-calls", response_class=HTMLResponse)
def classified_calls_ui(
    request: Request,
    api_key: str | None = Query(default=None),
    period: str | None = Query(default=None),
    date_from: str | None = Query(default=None),
    date_to: str | None = Query(default=None),
    manager: str | None = Query(default=None),
    topic: str | None = Query(default=None),
    subtopic: str | None = Query(default=None),
    limit: int = Query(default=200, ge=1, le=2000),
):
    role = resolve_role_by_key(api_key)
    if not role:
        return templates.TemplateResponse("login.html", {"request": request, "api_key": "", "role": None, "error": "Неверный API ключ"})
    if role != ROLE_KC:
        raise HTTPException(status_code=403, detail="Доступно только для КЦ")

    final_from, final_to, date_error = choose_date_range(period, date_from, date_to)
    db = SessionLocal()
    try:
        calls = list_calls_with_active_classification(
            db,
            role_call_types=allowed_call_types_for_role(role),
            limit=limit,
            date_from=final_from,
            date_to=final_to,
            manager=manager,
            topic=topic,
            subtopic=subtopic,
        )
        rows = [call_to_out(c, include_text=True) for c in calls]

        # Filters UI (catalog-driven options)
        catalog_entries = list_topic_catalog_entries(db, include_inactive=False)
        topic_options = sorted({e.topic_name for e in catalog_entries})
        if topic:
            subtopic_options = sorted({e.subtopic_name for e in catalog_entries if e.topic_name == topic})
        else:
            subtopic_options = sorted({e.subtopic_name for e in catalog_entries})

        # Managers list (based on current date range only)
        manager_stmt = (
            select(User.full_name)
            .join(Call, Call.manager_id == User.id)
            .join(CallType, Call.call_type_id == CallType.id)
            .join(CallClassification, (CallClassification.call_id == Call.id) & (CallClassification.is_active.is_(True)))
            .where(
                Call.status == "CLASSIFIED",
                CallType.code.in_(allowed_call_types_for_role(role)),
            )
        )
        if final_from:
            manager_stmt = manager_stmt.where(Call.call_started_at >= final_from)
        if final_to:
            manager_stmt = manager_stmt.where(Call.call_started_at <= final_to)
        if topic:
            manager_stmt = manager_stmt.where(CallClassification.topic_name == topic)
        if subtopic:
            manager_stmt = manager_stmt.where(CallClassification.subtopic_name == subtopic)
        manager_stmt = manager_stmt.distinct().order_by(User.full_name.asc())
        managers = [m for m in db.scalars(manager_stmt) if m]
        return templates.TemplateResponse(
            "classified_calls.html",
            {
                "request": request,
                "rows": rows,
                "role": role,
                "api_key": api_key or "",
                "date_error": date_error,
                "limit": limit,
                "menu": menu_context(api_key or "", role, "classified_calls"),
                "filters": {
                    "api_key": api_key,
                    "period": period or "",
                    "date_from": date_from or "",
                    "date_to": date_to or "",
                    "manager": manager or "",
                    "topic": topic or "",
                    "subtopic": subtopic or "",
                    "limit": limit,
                },
                "managers": managers,
                "topic_options": topic_options,
                "subtopic_options": subtopic_options,
            },
        )
    finally:
        db.close()


@app.get("/users", response_class=HTMLResponse)
def users_ui(request: Request, api_key: str | None = Query(default=None), limit: int = Query(default=1000, ge=1, le=5000)):
    role = resolve_role_by_key(api_key)
    if not role:
        return templates.TemplateResponse("login.html", {"request": request, "api_key": "", "role": None, "error": "Неверный API ключ"})

    db = SessionLocal()
    try:
        query = select(User).order_by(User.full_name.asc(), User.id.asc())
        dept = users_department_for_role(role)
        if dept:
            query = query.where(User.department == dept)
        rows = list(db.scalars(query.limit(limit)))
        return templates.TemplateResponse("users.html", {"request": request, "rows": rows, "role": role, "api_key": api_key, "menu": menu_context(api_key or "", role, "users")})
    finally:
        db.close()


@app.get("/pipeline-runs", response_class=HTMLResponse)
def pipeline_runs_ui(request: Request, api_key: str | None = Query(default=None), limit: int = Query(default=500, ge=1, le=2000)):
    role = resolve_role_by_key(api_key)
    if role != ROLE_ADMIN:
        raise HTTPException(status_code=403, detail="Доступно только ADMIN")

    db = SessionLocal()
    try:
        runs = list(db.scalars(select(PipelineRun).order_by(PipelineRun.started_at.desc()).limit(limit)))
        return templates.TemplateResponse("pipeline_runs.html", {"request": request, "rows": runs, "role": role, "api_key": api_key, "menu": menu_context(api_key or "", role, "pipeline_runs")})
    finally:
        db.close()


@app.get("/catalog", response_class=HTMLResponse)
def topic_catalog_ui(request: Request, api_key: str | None = Query(default=None), include_inactive: bool = Query(default=True)):
    role = resolve_role_by_key(api_key)
    if role != ROLE_ADMIN:
        raise HTTPException(status_code=403, detail="Доступно только ADMIN")

    db = SessionLocal()
    try:
        rows = list_topic_catalog_entries(db, include_inactive=include_inactive)
        return templates.TemplateResponse(
            "catalog.html",
            {
                "request": request,
                "rows": rows,
                "role": role,
                "api_key": api_key or "",
                "include_inactive": include_inactive,
                "menu": menu_context(api_key or "", role, "catalog"),
            },
        )
    finally:
        db.close()


@app.post("/catalog/save")
def save_catalog_entry(
    api_key: str = Form(...),
    entry_id: int | None = Form(default=None),
    topic_name: str = Form(...),
    subtopic_name: str = Form(...),
    description: str = Form(...),
    keywords_text: str = Form(...),
    synonyms_text: str | None = Form(default=None),
    negative_keywords_text: str | None = Form(default=None),
    is_active: str | None = Form(default="true"),
):
    role = resolve_role_by_key(api_key)
    if role != ROLE_ADMIN:
        raise HTTPException(status_code=403, detail="Доступно только ADMIN")

    topic_name = topic_name.strip()
    subtopic_name = subtopic_name.strip()
    description = description.strip()
    keywords_text = keywords_text.strip()
    if not topic_name or not subtopic_name or not description or not keywords_text:
        raise HTTPException(status_code=400, detail="Поля тема/подтема/описание/ключевые слова обязательны")

    db = SessionLocal()
    try:
        active_flag = str(is_active).lower() in {"true", "1", "on", "yes"}
        if entry_id:
            entry = update_topic_catalog_entry(
                db,
                entry_id=entry_id,
                topic_name=topic_name,
                subtopic_name=subtopic_name,
                description=description,
                keywords_text=keywords_text,
                synonyms_text=synonyms_text,
                negative_keywords_text=negative_keywords_text,
                is_active=active_flag,
            )
        else:
            entry = upsert_topic_catalog_entry(
                db,
                topic_name=topic_name,
                subtopic_name=subtopic_name,
                description=description,
                keywords_text=keywords_text,
                synonyms_text=synonyms_text,
                negative_keywords_text=negative_keywords_text,
                source_name="ui_admin",
                source_hash=entry_source_hash(topic_name, subtopic_name, description, keywords_text, synonyms_text),
                is_active=active_flag,
            )
        point_ids = sync_catalog_entries([entry])
        if point_ids:
            set_catalog_qdrant_point_id(db, entry.id, point_ids[0])
    finally:
        db.close()

    return RedirectResponse(url=f"/catalog?api_key={api_key}", status_code=303)


@app.get("/api/calls/export.xlsx")
def export_calls_excel(
    role: str = Depends(get_current_role),
    period: str | None = Query(default=None),
    date_from: str | None = Query(default=None),
    date_to: str | None = Query(default=None),
    manager: str | None = Query(default=None),
    status: str | None = Query(default=None),
    call_type: str | None = Query(default=None),
):
    final_from, final_to, date_error = choose_date_range(period, date_from, date_to)
    if date_error:
        raise HTTPException(status_code=400, detail=date_error)

    db = SessionLocal()
    try:
        query = build_calls_query(role=role, date_from=final_from, date_to=final_to, manager=manager, status=status, call_type=call_type).order_by(Call.call_started_at.desc())
        calls = list(db.scalars(query))

        wb = Workbook()
        ws = wb.active
        ws.title = "Calls"
        ws.append(["ID", "Octell ID", "Type", "Manager", "Manager Folder", "Status", "Started", "Duration", "Parts", "Topic", "Subtopic", "Transcription"])
        for c in calls:
            active_trans = get_active_transcription(c)
            active_class = get_active_classification(c)
            ws.append([
                c.id,
                c.octell_call_id,
                c.call_type.code,
                c.manager.full_name,
                c.manager.manager_folder,
                c.status,
                c.call_started_at.isoformat() if c.call_started_at else None,
                c.duration_seconds,
                safe_parts_count(c),
                active_class.topic_name if active_class else None,
                active_class.subtopic_name if active_class else None,
                active_trans.text if active_trans else None,
            ])

        bio = BytesIO()
        wb.save(bio)
        bio.seek(0)
        return StreamingResponse(
            bio,
            media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            headers={"Content-Disposition": 'attachment; filename="calls.xlsx"'},
        )
    finally:
        db.close()


@app.get("/api/classified-calls/export.xlsx")
def export_classified_calls_excel(
    role: str = Depends(get_current_role),
    period: str | None = Query(default=None),
    date_from: str | None = Query(default=None),
    date_to: str | None = Query(default=None),
    manager: str | None = Query(default=None),
    topic: str | None = Query(default=None),
    subtopic: str | None = Query(default=None),
):
    if role != ROLE_KC:
        raise HTTPException(status_code=403, detail="Доступно только КЦ")

    final_from, final_to, date_error = choose_date_range(period, date_from, date_to)
    if date_error:
        raise HTTPException(status_code=400, detail=date_error)

    db = SessionLocal()
    try:
        # Экспортируем все строки по фильтрам (ограничение для стабильности достаточно большое)
        calls = list_calls_with_active_classification(
            db,
            role_call_types=allowed_call_types_for_role(role),
            limit=200000,
            date_from=final_from,
            date_to=final_to,
            manager=manager,
            topic=topic,
            subtopic=subtopic,
        )

        wb = Workbook()
        ws = wb.active
        ws.title = "Classified Calls"
        ws.append(["ID", "Octell ID", "Manager", "Started", "Duration", "Parts", "Topic", "Subtopic", "Confidence", "Reason", "Transcription"])

        for c in calls:
            active_trans = get_active_transcription(c)
            active_class = get_active_classification(c)
            ws.append([
                c.id,
                c.octell_call_id,
                c.manager.full_name if c.manager else None,
                c.call_started_at.isoformat() if c.call_started_at else None,
                c.duration_seconds,
                safe_parts_count(c),
                active_class.topic_name if active_class else None,
                active_class.subtopic_name if active_class else None,
                active_class.confidence if active_class else None,
                active_class.reasoning if active_class else None,
                active_trans.text if active_trans else None,
            ])

        bio = BytesIO()
        wb.save(bio)
        bio.seek(0)
        return StreamingResponse(
            bio,
            media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            headers={"Content-Disposition": 'attachment; filename="classified_calls.xlsx"'},
        )
    finally:
        db.close()


@app.get("/api/calls", response_model=CallsResponse)
def calls_api(
    role: str = Depends(get_current_role),
    period: str | None = Query(default=None),
    date_from: str | None = Query(default=None),
    date_to: str | None = Query(default=None),
    manager: str | None = Query(default=None),
    status: str | None = Query(default=None),
    call_type: str | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=2000),
    offset: int = Query(default=0, ge=0),
    include_text: bool = Query(default=False),
):
    final_from, final_to, date_error = choose_date_range(period, date_from, date_to)
    if date_error:
        raise HTTPException(status_code=400, detail=date_error)

    db = SessionLocal()
    try:
        query = build_calls_query(role=role, date_from=final_from, date_to=final_to, manager=manager, status=status, call_type=call_type)
        total = db.scalar(select(func.count()).select_from(query.subquery())) or 0
        calls = list(db.scalars(query.order_by(Call.call_started_at.desc()).offset(offset).limit(limit)))
        return CallsResponse(items=[call_to_out(c, include_text=include_text) for c in calls], total=int(total))
    finally:
        db.close()


@app.get("/api/users", response_model=UsersResponse)
def users_api(role: str = Depends(get_current_role), limit: int = Query(default=1000, ge=1, le=5000), offset: int = Query(default=0, ge=0)):
    db = SessionLocal()
    try:
        query = select(User)
        dept = users_department_for_role(role)
        if dept:
            query = query.where(User.department == dept)
        total = db.scalar(select(func.count()).select_from(query.subquery())) or 0
        rows = list(db.scalars(query.order_by(User.full_name.asc(), User.id.asc()).offset(offset).limit(limit)))
        return UsersResponse(items=[UserOut(id=r.id, full_name=r.full_name, domain=r.domain, department=r.department) for r in rows], total=int(total))
    finally:
        db.close()


@app.get("/api/pipeline-runs", response_model=PipelineRunsResponse)
def pipeline_runs_api(role: str = Depends(get_current_role), limit: int = Query(default=500, ge=1, le=2000), offset: int = Query(default=0, ge=0)):
    if role != ROLE_ADMIN:
        raise HTTPException(status_code=403, detail="Доступно только ADMIN")

    db = SessionLocal()
    try:
        total = db.scalar(select(func.count()).select_from(PipelineRun)) or 0
        rows = list(db.scalars(select(PipelineRun).order_by(PipelineRun.started_at.desc()).offset(offset).limit(limit)))
        return PipelineRunsResponse(
            items=[
                PipelineRunOut(
                    id=r.id,
                    pipeline_code=r.pipeline_code,
                    status=r.status,
                    started_at=r.started_at,
                    finished_at=r.finished_at,
                    duration_seconds=r.duration_seconds,
                    processed_calls=r.processed_calls,
                    error_message=r.error_message,
                )
                for r in rows
            ],
            total=int(total),
        )
    finally:
        db.close()


@app.get("/api/catalog", response_model=TopicCatalogEntriesResponse)
def catalog_api(role: str = Depends(get_current_role), include_inactive: bool = Query(default=True)):
    if role != ROLE_ADMIN:
        raise HTTPException(status_code=403, detail="Доступно только ADMIN")
    db = SessionLocal()
    try:
        rows = list_topic_catalog_entries(db, include_inactive=include_inactive)
        return TopicCatalogEntriesResponse(
            items=[
                TopicCatalogEntryOut(
                    id=row.id,
                    topic_name=row.topic_name,
                    subtopic_name=row.subtopic_name,
                    description=row.description,
                    keywords_text=row.keywords_text,
                    synonyms_text=row.synonyms_text,
                    negative_keywords_text=row.negative_keywords_text,
                    is_active=row.is_active,
                    qdrant_point_id=row.qdrant_point_id,
                    updated_at=row.updated_at,
                )
                for row in rows
            ],
            total=len(rows),
        )
    finally:
        db.close()