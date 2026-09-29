"""Statutory filing deadlines, computed from the filing history.

Used when assessing *as of* a past date, where the profile's live `overdue` booleans
would leak the future. Simplified UK rules (Companies Act 2006 s.442, s.853A):
- accounts: private companies 9 months after period end, public companies 6 months;
  first accounts 21 / 18 months after incorporation
- confirmation statement: 12 months after the last made-up date (or incorporation),
  with 14 days to file

Not modelled: accounting reference date changes (AA01), 52/53-week periods (±7 days),
extensions. Live assessments use the register's own deadlines instead.
"""

import calendar
import datetime as dt

PUBLIC_COMPANY_TYPES = {"plc", "old-public-company"}


def add_months(d: dt.date, months: int) -> dt.date:
    """Calendar months, keeping month-ends as month-ends (31 Jan + 1 → 28/29 Feb)."""
    y, m = divmod(d.month - 1 + months, 12)
    year, month = d.year + y, m + 1
    last = calendar.monthrange(year, month)[1]
    is_month_end = d.day == calendar.monthrange(d.year, d.month)[1]
    return dt.date(year, month, last if is_month_end else min(d.day, last))


def accounts_due(
    company_type: str | None, incorporated: dt.date, last_made_up_to: dt.date | None
) -> tuple[dt.date | None, dt.date]:
    """(period end of the next accounts, date they're due)."""
    public = company_type in PUBLIC_COMPANY_TYPES
    if last_made_up_to is None:
        return None, add_months(incorporated, 18 if public else 21)
    period_end = add_months(last_made_up_to, 12)
    return period_end, add_months(period_end, 6 if public else 9)


def confirmation_statement_due(
    incorporated: dt.date, last_made_up_to: dt.date | None
) -> tuple[dt.date, dt.date]:
    """(next made-up date, date it's due)."""
    made_up = add_months(last_made_up_to or incorporated, 12)
    return made_up, made_up + dt.timedelta(days=14)
