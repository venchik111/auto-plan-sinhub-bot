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


def canonical_week_label(value: str) -> str | None:
    """Normalize a sheet week label: ' 07.09 - 13.09' -> '7.09-13.09'."""
    text = "".join(str(value or "").split())
    if not is_week_label(text):
        return None
    start, end = text.split("-")
    start_day, start_month = (int(part) for part in start.split("."))
    end_day, end_month = (int(part) for part in end.split("."))
    return f"{start_day}.{start_month:02d}-{end_day}.{end_month:02d}"


def week_end_from_label(value: str, today: date) -> date | None:
    """The label has no year, so pick the start date closest to today."""
    label = canonical_week_label(value)
    if label is None:
        return None
    day, month = (int(part) for part in label.split("-")[0].split("."))
    candidates: list[date] = []
    for year in (today.year - 1, today.year, today.year + 1):
        try:
            candidates.append(date(year, month, day))
        except ValueError:
            continue
    if not candidates:
        return None
    start = min(candidates, key=lambda candidate: abs((candidate - today).days))
    return start + timedelta(days=6)


def week_has_ended(value: str, today: date) -> bool:
    end = week_end_from_label(value, today)
    return end is not None and today > end
