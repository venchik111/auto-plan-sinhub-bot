from __future__ import annotations

from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo


def local_today(timezone_name: str = "Europe/Moscow") -> date:
    return datetime.now(ZoneInfo(timezone_name)).date()


def week_start(day: date, offset_weeks: int = 0) -> date:
    monday = day - timedelta(days=day.weekday())
    return monday + timedelta(weeks=offset_weeks)


def week_label(day: date, offset_weeks: int = 0) -> str:
    start = week_start(day, offset_weeks)
    end = start + timedelta(days=6)
    return f"{start.day}.{start.month:02d}-{end.day}.{end.month:02d}"


def is_week_label(value: str) -> bool:
    parts = value.strip().split("-")
    if len(parts) != 2:
        return False
    try:
        start_day, start_month = (int(part) for part in parts[0].split("."))
        end_day, end_month = (int(part) for part in parts[1].split("."))
    except (TypeError, ValueError):
        return False
    return all(
        [
            1 <= start_day <= 31,
            1 <= end_day <= 31,
            1 <= start_month <= 12,
            1 <= end_month <= 12,
        ]
    )
