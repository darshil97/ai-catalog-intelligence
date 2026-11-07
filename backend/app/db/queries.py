"""Loads named SQL queries from app/templates/sql.

A file holds several queries, each starting with a line like "-- name: claim". Values are
always passed as bound parameters and never rendered into the text.
"""

import re
from functools import cache
from pathlib import Path
from typing import LiteralString

_SQL_DIR = Path(__file__).resolve().parents[1] / "templates" / "sql"
_NAME_LINE = re.compile(r"^-- name: (\w+)\s*$", re.MULTILINE)


@cache
def _load_file(filename: str) -> dict[str, str]:
    parts = _NAME_LINE.split((_SQL_DIR / f"{filename}.sql").read_text(encoding="utf-8"))
    return dict(zip(parts[1::2], (text.strip() for text in parts[2::2]), strict=True))


def query(filename: str, name: str) -> LiteralString:
    """Return one named query.

    Raises:
        ValueError: if the query doesn't exist, naming the ones that do.
    """
    queries = _load_file(filename)
    if name not in queries:
        raise ValueError(
            f"Unknown query {name!r} in {filename}.sql. Available: {', '.join(sorted(queries))}"
        )
    # The text comes from files in this repository, never from user input.
    return queries[name]
