"""Job handlers. Only the worker imports this package, so heavy packages stay out of the API."""

import app.jobs.handlers.associate  # noqa: F401
import app.jobs.handlers.documents  # noqa: F401
import app.jobs.handlers.enrich  # noqa: F401
import app.jobs.handlers.extract  # noqa: F401
import app.jobs.handlers.images  # noqa: F401
import app.jobs.handlers.ingest  # noqa: F401
import app.jobs.handlers.noop  # noqa: F401
import app.jobs.handlers.normalize  # noqa: F401
