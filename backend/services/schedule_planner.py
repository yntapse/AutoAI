"""Schedule arithmetic for pipeline runs.

PURE. No database, no network, no LLM, no clock of its own - the caller passes
`now`, so every next-run calculation is reproducible and testable.

There is no cron dependency on purpose: the platform supports five named
frequencies plus a restricted 5-field cron, and a 60-line evaluator covers that
exactly without adding a package to the install. The evaluator steps forward a
minute at a time from a known start and stops at the first match, which is
simple enough to be obviously correct and bounded by _MAX_SEARCH_MINUTES.
"""

from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Sequence

FREQUENCIES = ("once", "hourly", "daily", "weekly", "monthly", "cron")

CRON_FOR_FREQUENCY = {
    "hourly": "0 * * * *",
    "daily": "0 2 * * *",
    "weekly": "0 2 * * 1",
    "monthly": "0 2 1 * *",
}

# A year of minutes. A schedule that matches nothing inside a year is a
# schedule that never fires, and saying so beats looping.
_MAX_SEARCH_MINUTES = 366 * 24 * 60

_FIELD_BOUNDS = (
    (0, 59),  # minute
    (0, 23),  # hour
    (1, 31),  # day of month
    (1, 12),  # month
    (0, 6),   # day of week, standard cron: 0 = Sunday .. 6 = Saturday
)

_FIELD_NAMES = ("minute", "hour", "day-of-month", "month", "day-of-week")


class ScheduleError(ValueError):
    """Raised when a schedule cannot be interpreted.

    The message is shown to the user as an HTTP 422 detail, so it must be
    readable and must never contain a credential.
    """


def _parse_field(spec: str, index: int) -> List[int]:
    """Expand one cron field into the explicit values it matches."""
    low, high = _FIELD_BOUNDS[index]
    name = _FIELD_NAMES[index]
    values: set = set()

    for part in str(spec).split(","):
        part = part.strip()
        if not part:
            raise ScheduleError(f"The {name} field is empty.")

        step = 1
        if "/" in part:
            part, _, step_text = part.partition("/")
            if not step_text.isdigit() or int(step_text) < 1:
                raise ScheduleError(
                    f"The {name} step '{step_text}' must be a positive number."
                )
            step = int(step_text)
            part = part or "*"

        if part == "*":
            start, end = low, high
        elif "-" in part.lstrip("-"):
            start_text, _, end_text = part.partition("-")
            if not (start_text.isdigit() and end_text.isdigit()):
                raise ScheduleError(f"The {name} range '{part}' is not numeric.")
            start, end = int(start_text), int(end_text)
        elif part.isdigit():
            start = end = int(part)
        else:
            raise ScheduleError(f"The {name} value '{part}' is not a number.")

        if start < low or end > high or start > end:
            raise ScheduleError(
                f"The {name} value '{part}' is outside {low}-{high}."
            )
        values.update(range(start, end + 1, step))

    if not values:
        raise ScheduleError(f"The {name} field matches nothing.")
    return sorted(values)


def parse_cron(expression: str) -> List[List[int]]:
    """Validate a 5-field cron expression and expand every field.

    Day-of-week follows STANDARD cron: 0 = Sunday through 6 = Saturday. That
    is what "1-5" means to everyone who has written a crontab (Mon-Fri), so it
    is what it means here. datetime.weekday() counts from Monday, so _matches
    converts rather than letting the two conventions drift.
    """
    parts = str(expression or "").split()
    if len(parts) != 5:
        raise ScheduleError(
            "A cron expression needs exactly 5 fields: "
            "minute hour day-of-month month day-of-week."
        )
    return [_parse_field(part, index) for index, part in enumerate(parts)]


def _matches(moment: datetime, fields: Sequence[Sequence[int]]) -> bool:
    minutes, hours, days, months, weekdays = fields
    if moment.minute not in minutes or moment.hour not in hours:
        return False
    if moment.month not in months:
        return False

    # datetime.weekday() is 0 = Monday; cron is 0 = Sunday.
    cron_weekday = (moment.weekday() + 1) % 7

    # Standard cron semantics: when both day-of-month and day-of-week are
    # restricted, either matching is enough.
    dom_restricted = len(days) != 31
    dow_restricted = len(weekdays) != 7
    dom_hit = moment.day in days
    dow_hit = cron_weekday in weekdays

    if dom_restricted and dow_restricted:
        return dom_hit or dow_hit
    if dom_restricted:
        return dom_hit
    if dow_restricted:
        return dow_hit
    return True


def next_cron_run(expression: str, now: datetime) -> Optional[datetime]:
    """First minute strictly after `now` that the expression matches."""
    fields = parse_cron(expression)
    candidate = now.replace(second=0, microsecond=0) + timedelta(minutes=1)
    for _ in range(_MAX_SEARCH_MINUTES):
        if _matches(candidate, fields):
            return candidate
        candidate += timedelta(minutes=1)
    return None


def resolve(
    frequency: str,
    now: datetime,
    cron_expression: Optional[str] = None,
    run_at: Optional[datetime] = None,
) -> Dict[str, Any]:
    """Normalise a schedule request into the cron and next-run it implies.

    Returns {"frequency", "cron_expression", "run_at", "next_run_at"}.
    Raises ScheduleError with a readable message on anything invalid.
    """
    normalised = str(frequency or "").strip().lower()
    if normalised not in FREQUENCIES:
        raise ScheduleError(
            f"Unknown frequency '{frequency}'. "
            f"Use one of: {', '.join(FREQUENCIES)}."
        )

    if normalised == "once":
        if run_at is None:
            raise ScheduleError("A one-off schedule needs run_at.")
        if run_at <= now:
            raise ScheduleError("run_at must be in the future.")
        return {
            "frequency": normalised,
            "cron_expression": None,
            "run_at": run_at,
            "next_run_at": run_at,
        }

    if normalised == "cron":
        if not cron_expression:
            raise ScheduleError("A cron schedule needs cron_expression.")
        resolved_cron = cron_expression.strip()
    else:
        resolved_cron = CRON_FOR_FREQUENCY[normalised]

    next_run = next_cron_run(resolved_cron, now)
    if next_run is None:
        raise ScheduleError(
            "That cron expression does not fire within a year. "
            "Check the day-of-month and month fields."
        )

    return {
        "frequency": normalised,
        "cron_expression": resolved_cron,
        "run_at": None,
        "next_run_at": next_run,
    }


def advance(
    frequency: str,
    cron_expression: Optional[str],
    now: datetime,
) -> Optional[datetime]:
    """The next run after a firing. A one-off schedule has no next run."""
    if str(frequency).lower() == "once":
        return None
    expression = cron_expression or CRON_FOR_FREQUENCY.get(
        str(frequency).lower()
    )
    if not expression:
        return None
    return next_cron_run(expression, now)


def describe(frequency: str, cron_expression: Optional[str], run_at=None) -> str:
    """One readable line for the schedule, for the UI and for audit logs."""
    normalised = str(frequency or "").lower()
    if normalised == "once":
        return f"Once at {run_at.isoformat()}" if run_at else "Once (time not set)"
    if normalised == "hourly":
        return "Every hour, on the hour"
    if normalised == "daily":
        return "Every day at 02:00"
    if normalised == "weekly":
        return "Every Monday at 02:00"
    if normalised == "monthly":
        return "On the 1st of each month at 02:00"
    return f"Custom cron: {cron_expression}"
