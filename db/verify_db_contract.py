from __future__ import annotations

"""
Проверка консистентности кода и реальной БД:
1) В db/models.py нет дублирующихся class-имен ORM.
2) SQLAlchemy mappers успешно конфигурируются.
3) Таблицы/колонки из ORM присутствуют в БД.
4) Базовые связи/атрибуты, которые использует CRUD, присутствуют в моделях.
5) Ключевые CRUD-select paths выполняются без ошибок.

Запуск:
    python db/verify_db_contract.py
"""

import ast
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass
class CheckResult:
    errors: list[str]
    warnings: list[str]


def check_duplicate_class_names_in_models(result: CheckResult) -> None:
    models_path = Path(__file__).resolve().parent / "models.py"
    source = models_path.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(models_path))

    seen: dict[str, int] = {}
    duplicates: list[tuple[str, int, int]] = []
    for node in tree.body:
        if isinstance(node, ast.ClassDef):
            if node.name in seen:
                duplicates.append((node.name, seen[node.name], node.lineno))
            else:
                seen[node.name] = node.lineno

    for class_name, first_line, second_line in duplicates:
        result.errors.append(
            f"Duplicate class '{class_name}' in db/models.py at lines {first_line} and {second_line}"
        )


def load_runtime_objects(result: CheckResult) -> dict[str, Any] | None:
    try:
        from sqlalchemy import inspect, select
        from sqlalchemy.orm import configure_mappers

        from db.base import SessionLocal, engine
        from db.models import Base, Call, CallClassification, TopicCatalogEntry, Transcription
    except Exception as exc:
        result.errors.append(f"Import/runtime bootstrap failed: {exc}")
        return None

    return {
        "inspect": inspect,
        "select": select,
        "configure_mappers": configure_mappers,
        "SessionLocal": SessionLocal,
        "engine": engine,
        "Base": Base,
        "Call": Call,
        "CallClassification": CallClassification,
        "TopicCatalogEntry": TopicCatalogEntry,
        "Transcription": Transcription,
    }


def check_mapper_configuration(result: CheckResult, rt: dict[str, Any]) -> None:
    try:
        rt["configure_mappers"]()
    except Exception as exc:
        result.errors.append(f"Mapper configuration failed: {exc}")


def check_model_attributes_used_by_crud(result: CheckResult, rt: dict[str, Any]) -> None:
    Call = rt["Call"]
    Transcription = rt["Transcription"]
    TopicCatalogEntry = rt["TopicCatalogEntry"]
    CallClassification = rt["CallClassification"]

    required_attrs = {
        "Call": ["call_parts", "transcriptions", "summarizations", "classifications", "parts_count"],
        "Transcription": ["classifications"],
        "TopicCatalogEntry": ["classifications"],
        "CallClassification": ["call", "transcription", "catalog_entry", "pipeline_run"],
    }
    model_map = {
        "Call": Call,
        "Transcription": Transcription,
        "TopicCatalogEntry": TopicCatalogEntry,
        "CallClassification": CallClassification,
    }

    for model_name, attrs in required_attrs.items():
        model = model_map[model_name]
        for attr in attrs:
            if not hasattr(model, attr):
                result.errors.append(f"Model {model_name} has no attribute '{attr}'")


def check_schema_vs_db(result: CheckResult, rt: dict[str, Any]) -> None:
    inspector = rt["inspect"](rt["engine"])
    Base = rt["Base"]

    db_tables = set(inspector.get_table_names())
    orm_tables = set(Base.metadata.tables.keys())

    missing_tables = sorted(orm_tables - db_tables)
    if missing_tables:
        result.errors.append(f"Missing tables in DB: {', '.join(missing_tables)}")

    extra_tables = sorted(db_tables - orm_tables)
    if extra_tables:
        result.warnings.append(f"Tables in DB but not in ORM: {', '.join(extra_tables[:15])}")

    for table_name, table in Base.metadata.tables.items():
        if table_name not in db_tables:
            continue
        db_columns = {col["name"] for col in inspector.get_columns(table_name)}
        orm_columns = set(table.columns.keys())

        missing_columns = sorted(orm_columns - db_columns)
        extra_columns = sorted(db_columns - orm_columns)

        if missing_columns:
            result.errors.append(f"Table '{table_name}' missing columns in DB: {', '.join(missing_columns)}")
        if extra_columns:
            result.warnings.append(f"Table '{table_name}' has extra DB columns: {', '.join(extra_columns)}")


def check_crud_select_paths(result: CheckResult, rt: dict[str, Any]) -> None:
    select = rt["select"]
    SessionLocal = rt["SessionLocal"]
    Call = rt["Call"]
    Transcription = rt["Transcription"]
    TopicCatalogEntry = rt["TopicCatalogEntry"]
    CallClassification = rt["CallClassification"]

    session = SessionLocal()
    try:
        _ = list(session.scalars(select(Call).limit(1)))
        _ = list(session.scalars(select(Transcription).limit(1)))
        _ = list(session.scalars(select(TopicCatalogEntry).limit(1)))
        _ = list(session.scalars(select(CallClassification).limit(1)))
    except Exception as exc:
        result.errors.append(f"CRUD/select smoke failed: {exc}")
    finally:
        session.close()


def main() -> int:
    result = CheckResult(errors=[], warnings=[])

    check_duplicate_class_names_in_models(result)
    rt = load_runtime_objects(result)
    if rt is not None:
        check_mapper_configuration(result, rt)
        check_model_attributes_used_by_crud(result, rt)
        check_schema_vs_db(result, rt)
        check_crud_select_paths(result, rt)

    print("\n=== DB CONTRACT CHECK REPORT ===")
    if result.errors:
        print("Errors:")
        for err in result.errors:
            print(f"  - {err}")
    else:
        print("Errors: none")

    if result.warnings:
        print("Warnings:")
        for warn in result.warnings:
            print(f"  - {warn}")
    else:
        print("Warnings: none")

    return 1 if result.errors else 0


if __name__ == "__main__":
    raise SystemExit(main())