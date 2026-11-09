"""Job handlers. Only the worker imports this package, so heavy packages stay out of the API."""

import app.jobs.handlers.ingest  # noqa: F401
import app.jobs.handlers.noop  # noqa: F401
