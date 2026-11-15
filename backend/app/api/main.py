"""Builds the FastAPI application."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.routes import (
    approval,
    batches,
    enrichment,
    files,
    health,
    me,
    products,
    review,
    suppliers,
    test_runs,
    validation,
)
from app.compute.starter import request_worker_start
from app.config import get_api_config
from app.middleware import RequestContextMiddleware
from app.middleware.request_context import REQUEST_ID_HEADER
from app.utils.logger import configure_logging
from app.validation import check_settings

configure_logging()
# Refuse to start with checks that name rules that don't exist (D38).
check_settings()


@asynccontextmanager
async def _lifespan(_app: FastAPI) -> AsyncIterator[None]:
    # Jobs may have been waiting while this API restarted, and nothing else would notice.
    request_worker_start()
    yield


app = FastAPI(title="AI Catalog Intelligence API", version="0.1.0", lifespan=_lifespan)

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
app.include_router(me.router)
app.include_router(suppliers.router)
app.include_router(batches.router)
app.include_router(files.router)
app.include_router(products.router)
app.include_router(review.router)
app.include_router(enrichment.router)
app.include_router(validation.router)
app.include_router(approval.router)
app.include_router(test_runs.router)
