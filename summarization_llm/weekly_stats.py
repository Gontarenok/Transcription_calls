"""Агрегаты по саммари 911 за период (для еженедельного отчёта)."""

from __future__ import annotations

from db.models import Call, Summarization

from summarization_llm.outcome_normalize import normalize_outcome_label, outcome_counts_template


def latest_summarization(call: Call) -> Summarization | None:
    if not call.summarizations:
        return None
    return max(call.summarizations, key=lambda s: s.id)


def aggregate_outcomes_for_calls(calls: list[Call]) -> dict[str, int]:
    counts = outcome_counts_template()
    for call in calls:
        s = latest_summarization(call)
        label = normalize_outcome_label(s.outcome if s else None)
        if label not in counts:
            label = "Не указано"
        counts[label] += 1
    return counts


def build_weekly_task_text(
    *,
    period_start,
    period_end,
    calls_summarized: int,
    outcome_counts: dict[str, int],
) -> str:
    lines = [
        f"Период отчёта: {period_start} — {period_end}",
        f"Звонков 911 с саммари за период: {calls_summarized}",
    ]
    for status in ("Помогли", "Не помогли", "В работе", "Не указано"):
        lines.append(f"{status}: {outcome_counts.get(status, 0)}")
    return "\n".join(lines)
