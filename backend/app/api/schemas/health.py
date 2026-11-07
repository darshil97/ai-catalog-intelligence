"""Schema for the health check."""

from pydantic import BaseModel, Field


class HealthResponse(BaseModel):
    """The API's status and deployed version."""

    status: str = Field(..., description="Always 'ok' when the API can respond.")
    environment: str = Field(..., description="Deployment environment, such as local.")
    code_sha: str = Field(..., description="Git commit of the running code, or 'unknown'.")
