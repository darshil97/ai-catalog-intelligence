"""Normalization: standard values, the value each product shows, categories and variants.

Expand-only: new tables, new nullable product columns, seed categories, and wider checks.

Revision ID: 0009
Revises: 0008
"""

from alembic import op

revision = "0009"
down_revision = "0008"
branch_labels = None
depends_on = None

# code, name, parent code. Home & Kitchen is the demo category (D18's category-independence:
# this is seed data, not code). "other" catches products outside the tree.
_CATEGORIES = (
    ("home-kitchen", "Home & Kitchen", None),
    ("kitchen", "Kitchen", "home-kitchen"),
    ("kitchen.cookware", "Cookware", "kitchen"),
    ("kitchen.cookware.pans", "Pans and skillets", "kitchen.cookware"),
    ("kitchen.cookware.pots", "Pots and saucepans", "kitchen.cookware"),
    ("kitchen.cookware.pressure-cookers", "Pressure cookers", "kitchen.cookware"),
    ("kitchen.bakeware", "Bakeware", "kitchen"),
    ("kitchen.tools", "Kitchen tools and gadgets", "kitchen"),
    ("kitchen.tools.utensils", "Cooking utensils", "kitchen.tools"),
    ("kitchen.tools.cutting-boards", "Cutting boards", "kitchen.tools"),
    ("kitchen.tools.knives", "Knives", "kitchen.tools"),
    ("kitchen.tools.mixing-bowls", "Mixing bowls", "kitchen.tools"),
    ("kitchen.appliances", "Small kitchen appliances", "kitchen"),
    ("kitchen.storage", "Food storage", "kitchen"),
    ("dining", "Dining", "home-kitchen"),
    ("dining.drinkware", "Drinkware", "dining"),
    ("dining.drinkware.mugs", "Mugs and cups", "dining.drinkware"),
    ("dining.drinkware.glasses", "Glasses", "dining.drinkware"),
    ("dining.drinkware.jugs", "Jugs and pitchers", "dining.drinkware"),
    ("dining.drinkware.bottles", "Water bottles and flasks", "dining.drinkware"),
    ("dining.dinnerware", "Dinnerware: plates and bowls", "dining"),
    ("dining.serveware", "Serveware", "dining"),
    ("dining.cutlery", "Cutlery", "dining"),
    ("home", "Home", "home-kitchen"),
    ("home.cleaning", "Cleaning supplies", "home"),
    ("home.organisation", "Storage and organisation", "home"),
    ("home.decor", "Home decor", "home"),
    ("home.bath", "Bath", "home"),
    ("other", "Other (outside Home & Kitchen)", None),
)


def upgrade() -> None:
    """Create the normalization tables and seed the category tree."""
    op.execute(
        """
        CREATE TABLE normalized_values (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            observation_id UUID NOT NULL REFERENCES attribute_observations (id) ON DELETE CASCADE,
            product_id UUID NOT NULL REFERENCES products (id) ON DELETE CASCADE,
            -- Usually the observation's attribute; a combined size gives length, width, height.
            attribute_id UUID NOT NULL REFERENCES attribute_definitions (id),
            value_text TEXT,
            value_number NUMERIC,
            -- The standard unit (g, ml, mm), or a currency code for a price.
            unit TEXT,
            -- What values are compared by: the number in standard units, or folded text.
            comparable TEXT NOT NULL,
            -- What people see, such as "1.5 L".
            display TEXT NOT NULL,
            -- Why the value can't be relied on alone, such as unit_missing or range.
            flags JSONB NOT NULL DEFAULT '[]',
            -- Lower is preferred when sources agree: spreadsheet, then PDF text, then photos.
            source_rank INTEGER NOT NULL,
            rule_version TEXT NOT NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            UNIQUE (observation_id, attribute_id, rule_version)
        )
        """
    )
    op.execute("CREATE INDEX normalized_values_by_product ON normalized_values (product_id)")

    op.execute(
        """
        CREATE TABLE product_attributes (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            product_id UUID NOT NULL REFERENCES products (id) ON DELETE CASCADE,
            attribute_id UUID NOT NULL REFERENCES attribute_definitions (id),
            selected_value_id UUID REFERENCES normalized_values (id) ON DELETE SET NULL,
            -- A person's own value, kept apart from every source (D19).
            reviewer_value JSONB,
            -- unclear: sources exist, but none can be relied on (unit missing, a range, ...).
            state TEXT NOT NULL
                CHECK (state IN ('verified', 'conflict', 'missing', 'unclear', 'reviewer_set')),
            -- 'system', or the person who decided.
            decided_by TEXT NOT NULL DEFAULT 'system',
            decided_at TIMESTAMPTZ,
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            UNIQUE (product_id, attribute_id)
        )
        """
    )

    op.execute("ALTER TABLE categories ADD COLUMN position INTEGER NOT NULL DEFAULT 0")
    for position, (code, name, parent) in enumerate(_CATEGORIES):
        quoted = name.replace("'", "''")
        # The values are the constants above.
        parent_sql = f"(SELECT id FROM categories WHERE code = '{parent}')" if parent else "NULL"  # noqa: S608
        op.execute(
            "INSERT INTO categories (code, name, parent_id, position) "  # noqa: S608
            f"VALUES ('{code}', '{quoted}', {parent_sql}, {position})"
        )

    op.execute(
        "ALTER TABLE products ADD COLUMN category_state TEXT NOT NULL DEFAULT 'none' "
        "CHECK (category_state IN ('none', 'assigned', 'needs_review'))"
    )
    op.execute(
        "ALTER TABLE products ADD COLUMN category_source TEXT "
        "CHECK (category_source IN ('llm', 'person'))"
    )
    op.execute(
        "ALTER TABLE products ADD COLUMN suggested_category_id UUID "
        "REFERENCES categories (id) ON DELETE SET NULL"
    )
    op.execute(
        "ALTER TABLE products ADD COLUMN category_confidence NUMERIC(4, 3) "
        "CHECK (category_confidence BETWEEN 0 AND 1)"
    )
    op.execute("ALTER TABLE products ADD COLUMN category_reason TEXT")

    op.execute(
        """
        CREATE TABLE variant_candidates (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            batch_id UUID NOT NULL REFERENCES batches (id) ON DELETE CASCADE,
            product_a_id UUID NOT NULL REFERENCES products (id) ON DELETE CASCADE,
            product_b_id UUID NOT NULL REFERENCES products (id) ON DELETE CASCADE,
            -- Which rules flagged the pair, and what they saw.
            reason JSONB NOT NULL,
            status TEXT NOT NULL DEFAULT 'flagged'
                CHECK (status IN ('flagged', 'confirmed', 'dismissed')),
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            CHECK (product_a_id < product_b_id),
            UNIQUE (product_a_id, product_b_id)
        )
        """
    )
    op.execute("CREATE INDEX variant_candidates_by_batch ON variant_candidates (batch_id)")

    op.execute("ALTER TABLE review_tasks DROP CONSTRAINT review_tasks_kind_check")
    op.execute(
        "ALTER TABLE review_tasks ADD CONSTRAINT review_tasks_kind_check "
        "CHECK (kind IN ('image_match', 'attribute_conflict', 'category'))"
    )
    # One open-or-closed task per product attribute, and per product's category.
    op.execute(
        "CREATE UNIQUE INDEX review_tasks_one_per_attribute ON review_tasks "
        "((subject_ref ->> 'product_id'), (subject_ref ->> 'attribute_id')) "
        "WHERE kind = 'attribute_conflict'"
    )
    op.execute(
        "CREATE UNIQUE INDEX review_tasks_one_per_category ON review_tasks "
        "((subject_ref ->> 'product_id')) WHERE kind = 'category'"
    )


def downgrade() -> None:
    """Drop the normalization tables and categories' seed rows."""
    op.execute("DROP INDEX review_tasks_one_per_category")
    op.execute("DROP INDEX review_tasks_one_per_attribute")
    op.execute("DELETE FROM review_tasks WHERE kind <> 'image_match'")
    op.execute("ALTER TABLE review_tasks DROP CONSTRAINT review_tasks_kind_check")
    op.execute(
        "ALTER TABLE review_tasks ADD CONSTRAINT review_tasks_kind_check "
        "CHECK (kind IN ('image_match'))"
    )
    op.execute("DROP TABLE variant_candidates")
    for column in (
        "category_reason",
        "category_confidence",
        "suggested_category_id",
        "category_source",
        "category_state",
    ):
        op.execute(f"ALTER TABLE products DROP COLUMN {column}")  # noqa: S608 - constant names
    op.execute("UPDATE products SET category_id = NULL")
    op.execute("DELETE FROM categories")
    op.execute("ALTER TABLE categories DROP COLUMN position")
    op.execute("DROP TABLE product_attributes")
    op.execute("DROP TABLE normalized_values")
