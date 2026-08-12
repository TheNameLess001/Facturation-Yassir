from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import PurePath

ADMIN_EARNINGS_FILENAME_PATTERN = re.compile(
    r"^data week (?P<week>[1-9]|[1-4][0-9]|5[0-3])_(?P<year>20\d{2})"
    r"(?P<extension>\.csv|\.xlsx)$",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class AdminEarningsFilename:
    filename: str
    week: int
    year: int
    extension: str


def parse_admin_earnings_filename(filename: str) -> AdminEarningsFilename | None:
    """Parse the source naming contract; the week is never treated as a month."""
    basename = PurePath(filename).name
    match = ADMIN_EARNINGS_FILENAME_PATTERN.fullmatch(basename)
    if match is None:
        return None
    return AdminEarningsFilename(
        filename=basename,
        week=int(match.group("week")),
        year=int(match.group("year")),
        extension=match.group("extension").lower(),
    )
