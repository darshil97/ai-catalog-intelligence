"""Gives every request an ID, available to all its logs and echoed in the response."""

import re
import time
import uuid

from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import Response

from app.utils.logger import get_logger
from app.utils.request_id import reset_request_id, set_request_id

logger = get_logger(__name__)

REQUEST_ID_HEADER = "X-Request-ID"
_VALID_REQUEST_ID = re.compile(r"^[A-Za-z0-9._-]{1,128}$")


class RequestContextMiddleware(BaseHTTPMiddleware):
    """Use the caller's request ID if it is well formed, otherwise generate one."""

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        """Set the request ID for this request, and always restore it afterwards."""
        supplied = request.headers.get(REQUEST_ID_HEADER, "")
        # The ID goes into every log line, so callers can't choose arbitrary content.
        request_id = supplied if _VALID_REQUEST_ID.match(supplied) else str(uuid.uuid4())
        token = set_request_id(request_id)
        started = time.perf_counter()
        try:
            response = await call_next(request)
            response.headers[REQUEST_ID_HEADER] = request_id
            # The path only: query strings can carry sensitive values.
            logger.info(
                "Request finished: method=%s path=%s status=%d duration_ms=%.1f",
                request.method,
                request.url.path,
                response.status_code,
                (time.perf_counter() - started) * 1000,
            )
            return response
        finally:
            reset_request_id(token)
