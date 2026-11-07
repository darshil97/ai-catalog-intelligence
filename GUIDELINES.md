# Engineering Guidelines

House style for Python services. Follow these patterns unless there's a reason not to.
Sections marked *(optional)* apply only when the project has that concern — skip them otherwise.

## Principles

1. **Config is data.** Tunables in `config.yaml`, secrets in `.env`. Code reads both only via `app/config.py`.
2. **Pluggable things go in a registry.** Protocol + registry + one module per impl. Adding one never edits existing code.
3. **Validate at the edge.** Pydantic on inbound payloads; `TypedDict`/frozen `dataclass` internally.
4. **Every request carries a `request_id`** through a ContextVar, into every log line and every outbound call.
5. **Thin routes, fat service, pure domain.** Domain packages never import `fastapi`.
6. **No magic strings.** Valid values live in config/registry and are validated against them.

## Layout

```
app/api/{main,routes,service,schemas}.py   # wiring | endpoints | orchestration | pydantic
app/config.py                              # cached config.yaml + one accessor per section
app/middleware/request_context.py          # ContextVar + middleware
app/<domain>/base.py                       # Protocol + registry
app/<domain>/models.py                     # TypedDict / dataclass contracts
app/<domain>/<plugins>/{__init__,impl}.py  # __init__ imports each impl to register it
app/utils/logger/                          # configure_logging, get_logger, RequestIdFilter
config.yaml · .env · server.py · tests/
```

Packages are named for concepts (`billing/`, `ingest/`), not layers (`helpers/`, `managers/`).
Each `__init__.py` defines the public API via explicit imports + `__all__`. Dependencies point inward.

## Config

- `get_config()` loads `config.yaml` once into a module-level cache; `APP_CONFIG` env var overrides the path.
- One accessor per section — `get_api_config()`, `get_retry_config()` — with a docstring listing its keys. Callers never index deep into the raw dict, and never mutate what they get back (`dict(...)` to copy).
- Enumerations get helpers (`get_valid_<thing>()`) so schemas validate against config, not hardcoded lists.
- Secrets stay in `.env` (gitignored, with a committed `.env.example`). Config references them with `${VAR}` / `${VAR:-default}`, resolved at load:

```python
_ENV_PATTERN = re.compile(r"\$\{([A-Za-z_]\w*)(?::-([^}]*))?\}")

def _resolve_env(node):
    """Recursively expand ${VAR} / ${VAR:-default}. Raises if a required var is unset."""
    load_dotenv()
    if isinstance(node, dict):  return {k: _resolve_env(v) for k, v in node.items()}
    if isinstance(node, list):  return [_resolve_env(v) for v in node]
    if not isinstance(node, str): return node

    def _sub(m):
        value = os.getenv(m.group(1), m.group(2))
        if value is None:
            raise ValueError(f"config.yaml references ${{{m.group(1)}}} but it is not set")
        return value
    return _ENV_PATTERN.sub(_sub, node)
```

  A missing required var fails at startup, not mid-request. `os.getenv` appears nowhere else.

## Registry

```python
class Exporter(Protocol):               # Protocol, not ABC — impls stay dependency-free
    name: str                           # registry key
    fmt: str                            # optional capability attr, read via getattr(x, "fmt", default)
    def export(self, data: dict) -> bytes: ...

_REGISTRY: dict[str, Exporter] = {}

def register_exporter(e):               # register instances, not classes
    _REGISTRY[e.name] = e
    logger.info("Registered exporter: %s", e.name)
    return e

def get_registered_exporter_names() -> set[str]:   # so schemas validate against the live registry
    return set(_REGISTRY)

def get_exporter(name):
    if name not in _REGISTRY:           # error names the available options, never a bare KeyError
        raise ValueError(f"Unknown exporter: {name!r}. Registered: {', '.join(sorted(_REGISTRY)) or '(none)'}")
    return _REGISTRY[name]
```

- One impl per module; its docstring shows the input format and any quirk; `register_exporter(MyImpl())` is the last line.
- `<plugins>/__init__.py` is explicit imports (`import app.<domain>.<plugins>.csv  # noqa: F401`), not `pkgutil` discovery — order is visible and commenting one out disables it.
- Selection + caching live in a separate **factory**, not the registry. Cache what's request-independent; `bind()`/re-wrap what's request-scoped (never bake a `request_id` into a cached client).

## Pydantic

- Every field gets `Field(..., description=...)` — that's the OpenAPI doc.
- `field_validator` for one field, `model_validator(mode="after")` for cross-field rules, inheritance for endpoint variants (`class UpdateRequest(CreateRequest)`).
- Validate enums against config/registry (import inside the validator to dodge circular imports). Error messages name the bad value *and* the allowed set.
- Response models declare optionals as `| None = None`; the service strips `None` and internal state keys before returning.
- Internal structures are **not** Pydantic: `TypedDict` for state passed between functions, frozen `dataclass` for computed bundles.

## request_id

```python
_request_id_var: ContextVar[str | None] = ContextVar("request_id", default=None)

class RequestContextMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        request_id = request.headers.get("X-Request-ID") or str(uuid.uuid4())  # caller's id wins
        token = _request_id_var.set(request_id)
        try:
            response = await call_next(request)
            response.headers["X-Request-ID"] = request_id      # echo it back
            return response
        finally:
            _request_id_var.reset(token)                       # always

class RequestIdFilter(logging.Filter):
    def filter(self, record):
        record.request_id = get_request_id() or "N/A"
        return True
```

ContextVar over threading it through signatures: survives `async` hops and `gather`, isolates concurrent requests.
Log format puts it second — `%(asctime)s | %(request_id)s | %(levelname)s | %(name)s | %(message)s` — so one `grep` replays a request. Also put it in any pipeline state (for non-HTTP code) and in outbound call headers.

## Layering

- **`main.py`**: `configure_logging()` → `FastAPI(...)` → exception handlers → middleware → router. Nothing else. Always a `GET /health`.
- **Routes**: resolve `request_id`, log entry, delegate. Past ~25 lines, it belongs in `service.py`.
- **Service**: orchestration, timeouts from config (`0`/`null` = none), error translation, response shaping. Parameterize shared helpers by a **label** so two endpoints share code and still log distinguishably.
- **Errors**: domain raises a domain exception (`class PayloadValidationError(ValueError)`); the service logs *why* with identifying context, then converts with `raise HTTPException(...) from exc`. Log before converting — otherwise the only trace is uvicorn's bare `400`.
  `400` bad payload · `503` dependency failed · `504` timeout · `500` genuine bug.

## Logging

`logger = get_logger(__name__)` at module top. Lazy `%` formatting, never f-strings. Messages are greppable `key=value` pairs (`"Export finished: records=%d, bytes=%d"`). Log at boundaries and decisions — including a decision's *inputs*: `"Route resolved: kind=%s score=%s path=%s"`. `warning` for defaulted/recovered paths, `error(..., exc_info=True)` for failures. Never log secrets or full payloads.

## External calls

Wrap flaky calls once in a util and reuse. Retry **only** the error classes retrying can fix, with `reraise=True` and counts from config. Normalize vendor response shapes in a private helper so callers see one type. Degrade rather than crash on partial responses from non-deterministic sources (`result.get("status", "unknown")` over `result["status"]`). Run independent async work with `asyncio.gather`, keeping a parallel name list so failures can be attributed.

## Templates & prompts

Any long text the service emits or sends — prompts, emails, reports, SQL — is a `.md`/template file under `app/<templates>/<category>/`, never a string in `.py`. The directory mirrors the domain so selection is a path join: `load_template(f"{category}/{subtype}", name)`.

Structure each file with markdown headings — `#` for the opening role/purpose line, `##` for every section — in a fixed order the whole project shares. A typical order for a structured-output prompt:

```text
# Role: <what this agent is>        ← one line of goal right under it
## Output Constraints               ← exact format, no fences, no preamble
## Field Definitions                ← one bullet per output field, what it means
## Field Extraction Rules           ← per-field: what qualifies, what to leave empty
## Output Schema                    ← the literal output shape, empty values as placeholders
## <Injected context>               ← the data the template renders over, last
```

Add project-specific sections as needed; keep the order identical across files so they stay diffable.

**State the output structure explicitly** as a schema block holding the literal skeleton, with a definition for every field above it. A schema without field definitions produces guessed semantics; definitions without a schema produce shape drift. Both, or neither works.

Loading is Jinja2 only — one `Environment` with `FileSystemLoader`, `trim_blocks=True`, `lstrip_blocks=True`, built once at import in `loader.py`. The loader appends the extension, renders, and raises `FileNotFoundError` on `TemplateNotFound`. Never `.format()` or f-strings on template text.

- Variables are `{{ var | default("...") }}` — a missing kwarg must degrade, not render `None`.
- Conditional/repeated sections use `{% if %}` / `{% for %}` rather than branching in Python and concatenating.
- Shared fragments live in `_partials/` and are `{% include %}`d. Check optional includes with a `template_exists()` helper so a missing file is logged, not silently empty.


## Docstrings & comments

Docstring every module, class, and public function — `Args:`/`Returns:`/`Raises:` when non-obvious, and state in-place mutation explicitly. Comments explain **why**, never what:
`# Force the max score here so the router cannot divert to the fallback path.`
`# Validate every id before mutating any row, so a partial mapping can't leave mixed state.`

## Testing

`tests/test_<feature>.py`, docstring stating what's covered and what's stubbed. Mock at the boundary (network, storage, third-party APIs), never inside domain logic. `TestClient(app)` so middleware and validators run. Small named fixture builders; make the data adversarial and comment why. Test the registry contract itself.

## Checklists

**New plugin:** module with input-format docstring → implement Protocol, set `name` → `register_x(Impl())` last line → add import to `<plugins>/__init__.py` → config block if needed → test. Schemas pick it up automatically.

**New endpoint:** schemas with descriptions + validators → service function returning plain dicts → thin route → config knobs → `TestClient` tests incl. validation failures.

**Review red flags:** literal that belongs in config · `os.getenv` outside `config.py` · `if provider == "x": elif ...` · f-string in `logger.*` · logic in `routes.py` · `fastapi` in a domain package · `except Exception: pass` or converting an error without logging · inline prompt/template · new concept with no docstring.
