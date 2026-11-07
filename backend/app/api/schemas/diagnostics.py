"""Temporary (M0A): schema for the latency measurement."""

from pydantic import BaseModel, Field


class DbLatencyResponse(BaseModel):
    """Time taken to reach the database from this API instance."""

    connect_ms: float = Field(..., description="Time to open a connection, including any wake-up.")
    query_median_ms: float = Field(..., description="Median round trip of a trivial query.")
    query_min_ms: float = Field(..., description="Fastest round trip of a trivial query.")
    query_max_ms: float = Field(..., description="Slowest round trip of a trivial query.")
    samples: int = Field(..., description="Number of queries measured.")
