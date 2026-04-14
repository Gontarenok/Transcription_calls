"""Календарный отчётный период для еженедельного пайплайна 911 (пн–вс предыдущей ISO-недели)."""

from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

MSK = ZoneInfo("Europe/Moscow")


def previous_iso_week_mon_sun(*, today: date | None = None) -> tuple[date, date]:
    """
    Предыдущая полная неделя пн–вс относительно «сегодня».
    Пример: запуск в вс вечером → отчёт за прошедшую пн–вс.
    """
    today = today or datetime.now(MSK).date()
    monday_this = today - timedelta(days=today.weekday())
    monday_prev = monday_this - timedelta(days=7)
    sunday_prev = monday_prev + timedelta(days=6)
    return monday_prev, sunday_prev


def period_to_utc_half_open(
    period_start: date,
    period_end: date,
    *,
    tz: ZoneInfo = MSK,
) -> tuple[datetime, datetime]:
    """Границы периода по календарным датам в tz; возвращает [start_utc, end_utc) для SQL."""
    start_local = datetime.combine(period_start, time.min, tzinfo=tz)
    end_local_excl = datetime.combine(period_end + timedelta(days=1), time.min, tzinfo=tz)
    return start_local.astimezone(timezone.utc), end_local_excl.astimezone(timezone.utc)
