"""Builds the FastAPI application."""

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.routes import health
from app.config import get_api_config
from app.middleware import RequestContextMiddleware
from app.middleware.request_context import REQUEST_ID_HEADER
from app.utils.logger import configure_logging

configure_logging()

app = FastAPI(title="AI Catalog Intelligence API", version="0.1.0")

api_config = get_api_config()
# Sign-in uses Bearer tokens rather than cookies, so credentials stay off.
app.add_middleware(
    CORSMiddleware,
    allow_origins=api_config["cors_origins"],
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
    allow_headers=["Authorization", "Content-Type", REQUEST_ID_HEADER],
    expose_headers=[REQUEST_ID_HEADER],
    max_age=api_config["cors_max_age_seconds"],
)
# Added last so it runs first, covering everything else in the logs.
app.add_middleware(RequestContextMiddleware)

app.include_router(health.router)
