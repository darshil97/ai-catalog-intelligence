"""Human review: approval, rejection, duplicates, and a revision number against stale writes.

Expand-only: new nullable product columns, and a revision with a default.

Revision ID: 0012
Revises: 0011
"""

from alembic import op

revision = "0012"
down_revision = "0011"
branch_labels = None
depends_on = None

_COLUMNS = (
    # Every change through the API increments it; a write carrying an older one is refused.
    "revision INTEGER NOT NULL DEFAULT 0",
    "approved_at TIMESTAMPTZ",
    "approved_by TEXT",
    # What the product was when approved. The approval holds only while it still matches.
    "approval_fingerprint TEXT",
    "rejected_at TIMESTAMPTZ",
    "rejected_by TEXT",
    "rejection_reason TEXT",
    # The product kept, when this one was rejected as its duplicate.
    "duplicate_of UUID REFERENCES products (id) ON DELETE SET NULL",
)


def upgrade() -> None:
    """Add the review columns to products."""
    for column in _COLUMNS:
        op.execute(f"ALTER TABLE products ADD COLUMN {column}")  # noqa: S608 - constants


def downgrade() -> None:
    """Drop them, and put approved or rejected products back to draft."""
    op.execute("UPDATE products SET status = 'draft' WHERE status IN ('approved', 'rejected')")
    for column in reversed(_COLUMNS):
        name = column.split()[0]
        op.execute(f"ALTER TABLE products DROP COLUMN {name}")  # noqa: S608 - constants
