"""This month's LLM spend against the global cap, readable by the API and the worker."""

from typing import Any

from psycopg import Connection

from app.config import get_llm_config
from app.db import query


def llm_month_spend(conn: Connection[Any]) -> float:
    """Return every user's recorded LLM cost this calendar month, in USD."""
    row = conn.execute(query("llm", "month_spend")).fetchone()
    return float(row["spent"]) if row else 0.0


def llm_budget_reached(conn: Connection[Any]) -> bool:
    """Return whether this month's LLM spend has reached the cap. No cap means never."""
    cap = get_llm_config()["monthly_spend_cap_usd"]
    return cap is not None and llm_month_spend(conn) >= cap
