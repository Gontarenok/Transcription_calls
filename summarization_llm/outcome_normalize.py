"""Нормализация поля «Итог» саммари 911 к фиксированным категориям для отчётов и аналитики."""

from __future__ import annotations

import re

CANONICAL_OUTCOMES = ("Помогли", "Не помогли", "В работе", "Не указано")


def normalize_outcome_label(raw: str | None) -> str:
    """Приводит произвольный текст итога к одной из четырёх категорий (+ «Не указано»)."""
    if not raw or not isinstance(raw, str):
        return "Не указано"

    s = raw.strip().lower()
    # «не помогли» содержит подстроку «помогли» — сначала отрицательные варианты.
    if "не помогли" in s or "не помогло" in s or re.search(r"не\s+помог", s):
        return "Не помогли"
    if "помогли" in s or "помогло" in s or "решили" in s:
        return "Помогли"
    if "работ" in s or "в процессе" in s:
        return "В работе"
    return "Не указано"


def outcome_counts_template() -> dict[str, int]:
    return {k: 0 for k in CANONICAL_OUTCOMES}
