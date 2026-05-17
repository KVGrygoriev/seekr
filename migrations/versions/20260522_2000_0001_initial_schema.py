"""initial schema

Revision ID: 0001
Revises:
Create Date: 2026-05-22 20:00:00
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "sources",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.String(64), nullable=False, unique=True),
    )

    op.create_table(
        "searches",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "source_id",
            sa.Integer(),
            sa.ForeignKey("sources.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("name", sa.String(255), nullable=False, unique=True),
        sa.Column("url", sa.Text(), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("config_hash", sa.String(64), nullable=False),
        sa.Column("last_run_at", sa.DateTime(timezone=True), nullable=True),
    )

    op.create_table(
        "listings",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column(
            "source_id",
            sa.Integer(),
            sa.ForeignKey("sources.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("external_id", sa.String(64), nullable=False),
        sa.Column("current_url", sa.Text(), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("location", sa.Text(), nullable=False, server_default=""),
        sa.Column("area_m2", sa.Numeric(12, 2), nullable=True),
        sa.Column("current_price", sa.Numeric(14, 2), nullable=True),
        sa.Column("currency", sa.String(3), nullable=False, server_default="USD"),
        sa.Column("current_price_per_100m2", sa.Numeric(14, 2), nullable=True),
        sa.Column("phone", sa.String(64), nullable=True),
        sa.Column("fingerprint", sa.String(255), nullable=False),
        sa.Column("status", sa.String(16), nullable=False, server_default="active"),
        sa.Column("posted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at_source", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "first_seen_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "last_seen_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.UniqueConstraint("source_id", "external_id", name="uq_listings_source_external"),
    )
    op.create_index("ix_listings_fingerprint", "listings", ["fingerprint"])
    op.create_index("ix_listings_last_seen_at", "listings", ["last_seen_at"])

    op.create_table(
        "listing_history",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column(
            "listing_id",
            sa.BigInteger(),
            sa.ForeignKey("listings.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "captured_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("price", sa.Numeric(14, 2), nullable=True),
        sa.Column("currency", sa.String(3), nullable=False, server_default="USD"),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("location", sa.Text(), nullable=False, server_default=""),
        sa.Column("area_m2", sa.Numeric(12, 2), nullable=True),
        sa.Column("seller_url", sa.Text(), nullable=True),
        sa.Column("change_kind", sa.String(32), nullable=False),
    )
    op.create_index(
        "ix_listing_history_listing_id_captured_at",
        "listing_history",
        ["listing_id", "captured_at"],
    )

    op.create_table(
        "report_dispatches",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column(
            "listing_id",
            sa.BigInteger(),
            sa.ForeignKey("listings.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "search_id",
            sa.Integer(),
            sa.ForeignKey("searches.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "dispatched_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("classification", sa.String(32), nullable=False),
        sa.Column("price_snapshot", sa.Numeric(14, 2), nullable=True),
    )
    op.create_index(
        "ix_report_dispatches_search_listing",
        "report_dispatches",
        ["search_id", "listing_id", "dispatched_at"],
    )

    op.create_table(
        "operator_notes",
        sa.Column(
            "listing_id",
            sa.BigInteger(),
            sa.ForeignKey("listings.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("status", sa.String(64), nullable=True),
        sa.Column("comment", sa.Text(), nullable=True),
        sa.Column("operator", sa.String(64), nullable=True),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )


def downgrade() -> None:
    op.drop_table("operator_notes")
    op.drop_index("ix_report_dispatches_search_listing", table_name="report_dispatches")
    op.drop_table("report_dispatches")
    op.drop_index("ix_listing_history_listing_id_captured_at", table_name="listing_history")
    op.drop_table("listing_history")
    op.drop_index("ix_listings_last_seen_at", table_name="listings")
    op.drop_index("ix_listings_fingerprint", table_name="listings")
    op.drop_table("listings")
    op.drop_table("searches")
    op.drop_table("sources")
