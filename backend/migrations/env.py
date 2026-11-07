"""Alembic environment. Runs over the direct endpoint, because the pooler isn't suitable."""

from alembic import context
from sqlalchemy import create_engine, pool

from app.config import get_database_config
from app.db.tables import metadata


def _include_name(name: str | None, type_: str, parent_names: object) -> bool:
    """Exclude other schemas: neon_auth belongs to Neon and is never migrated here."""
    if type_ == "schema":
        return name in (None, "public")
    return True


def _sqlalchemy_url(url: str) -> str:
    """Point a Neon connection string at the psycopg 3 driver."""
    for prefix in ("postgresql://", "postgres://"):
        if url.startswith(prefix):
            return "postgresql+psycopg://" + url.removeprefix(prefix)
    return url


def run_offline() -> None:
    """Write the migration SQL without connecting to a database."""
    context.configure(
        dialect_name="postgresql",
        target_metadata=metadata,
        literal_binds=True,
        include_name=_include_name,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_online() -> None:
    """Apply migrations to the database at DATABASE_URL_DIRECT."""
    direct_url = get_database_config()["direct_url"]
    if not direct_url:
        raise RuntimeError("DATABASE_URL_DIRECT is not set")
    engine = create_engine(_sqlalchemy_url(direct_url), poolclass=pool.NullPool)
    with engine.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=metadata,
            include_name=_include_name,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_offline()
else:
    run_online()
