"""Time helpers shared by the daily usage limits."""

import datetime as dt


def utc_midnight() -> dt.datetime:
    """Start of the current UTC day: daily limits reset here."""
    return dt.datetime.now(dt.UTC).replace(hour=0, minute=0, second=0, microsecond=0)
