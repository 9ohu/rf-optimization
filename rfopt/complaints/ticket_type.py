"""NEW / REOPEN / UP OF SLEEP, read from the Daily Target's Reopen and User fields.

    Reopen holds a number (1, 2.0, "3", " 2 ")   ->  REOPEN, #n
    Reopen empty and User names someone          ->  UP OF SLEEP
    anything else                                ->  NEW

The Reopen number is the reopen count, never a ticket id, and it wins: a
reopened ticket that also has a User is REOPEN. A count of 0 means the ticket
was never reopened, so it reads like an empty cell.
"""

from __future__ import annotations

import math
import re

NEW = "NEW"
REOPEN = "REOPEN"
UP_OF_SLEEP = "UP OF SLEEP"
TYPES = (NEW, REOPEN, UP_OF_SLEEP)

UP_OF_SLEEP_TEXT = ("Ticket classified as UP OF SLEEP because the Reopen field is empty "
                    "and the User field contains a user/person value.")

_BLANK = {"", "nan", "none", "null", "nat", "<na>", "n/a", "na", "-", "--"}
_NUM = re.compile(r"\d+(?:\.\d+)?")


def _blank(value) -> bool:
    if value is None:
        return True
    if isinstance(value, float) and math.isnan(value):
        return True
    return str(value).strip().lower() in _BLANK


def _number(value) -> float | None:
    if _blank(value) or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        n = float(value)
    else:
        m = _NUM.search(str(value))
        if not m:
            return None
        n = float(m.group())
    return n if math.isfinite(n) else None


def reopen_number(value) -> int | None:
    """The reopen count in a Reopen cell, or None when it holds no count of 1 or more."""
    n = _number(value)
    return int(n) if n is not None and n >= 1 else None


def has_person(value) -> bool:
    """The User cell names someone."""
    return not _blank(value)


def ticket_type(reopen, user) -> tuple[str, int | None]:
    """(type, reopen number) for one ticket."""
    n = reopen_number(reopen)
    if n is not None:
        return REOPEN, n
    if (_blank(reopen) or _number(reopen) == 0) and has_person(user):
        return UP_OF_SLEEP, None
    return NEW, None


def type_label(kind: str, number: int | None = None) -> str:
    return f"{REOPEN} #{number}" if kind == REOPEN and number else kind
