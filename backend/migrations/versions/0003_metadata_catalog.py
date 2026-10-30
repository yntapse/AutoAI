"""Metadata catalog: registered sources, scans, tables, columns, relationships.

Revision ID: 0003_metadata_catalog
Revises: 0002_cleaning_step_metadata
"""

from alembic import context, op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0003_metadata_catalog"
down_revision = "0002_cleaning_step_metadata"
branch_labels = None
depends_on = None

_TABLES = (
    "catalog_relationships",
    "catalog_columns",
    "catalog_tables",
    "catalog_scans",
    "data_sources",
)


def _existing_tables():
    if context.is_offline_mode():
        return set()
    return set(sa.inspect(op.get_bind()).get_table_names())


def upgrade() -> None:
    existing = _existing_tables()

    if "data_sources" not in existing:
        op.create_table(
            "data_sources",
            sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
            sa.Column("source_name", sa.String(), nullable=False),
            sa.Column("db_type", sa.String(), nullable=False),
            sa.Column("host", sa.String(), nullable=True),
            sa.Column("port", sa.Integer(), nullable=True),
            sa.Column("database", sa.String(), nullable=True),
            sa.Column("username", sa.String(), nullable=True),
            sa.Column("schema_name", sa.String(), nullable=True),
            sa.Column("trusted_connection", sa.Boolean(), nullable=False, server_default=sa.false()),
            sa.Column("odbc_driver", sa.String(), nullable=True),
            sa.Column("description", sa.String(), nullable=True),
            sa.Column("status", sa.String(), nullable=False, server_default="registered"),
            sa.Column("last_scanned_at", sa.DateTime(), nullable=True),
            sa.Column("created_by", sa.String(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
            sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
            sa.UniqueConstraint("source_name", name="uq_data_sources_source_name"),
        )

    if "catalog_scans" not in existing:
        op.create_table(
            "catalog_scans",
            sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
            sa.Column("data_source_id", postgresql.UUID(as_uuid=True), nullable=False),
            sa.Column("schema_name", sa.String(), nullable=False),
            sa.Column("status", sa.String(), nullable=False, server_default="running"),
            sa.Column("table_count", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("column_count", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("relationship_count", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("included_row_counts", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("included_sample_values", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("duration_ms", sa.Integer(), nullable=True),
            sa.Column("error", sa.Text(), nullable=True),
            sa.Column("created_by", sa.String(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
            sa.Column("completed_at", sa.DateTime(), nullable=True),
            sa.ForeignKeyConstraint(["data_source_id"], ["data_sources.id"], ondelete="CASCADE"),
        )
        op.create_index("ix_catalog_scans_data_source_id", "catalog_scans", ["data_source_id"])

    if "catalog_tables" not in existing:
        op.create_table(
            "catalog_tables",
            sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
            sa.Column("scan_id", postgresql.UUID(as_uuid=True), nullable=False),
            sa.Column("data_source_id", postgresql.UUID(as_uuid=True), nullable=False),
            sa.Column("schema_name", sa.String(), nullable=False),
            sa.Column("table_name", sa.String(), nullable=False),
            sa.Column("column_count", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("row_count", sa.BigInteger(), nullable=True),
            sa.Column("row_count_is_estimate", sa.Boolean(), nullable=False, server_default=sa.true()),
            sa.Column("has_primary_key", sa.Boolean(), nullable=False, server_default=sa.false()),
            sa.Column("business_description", sa.String(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
            sa.ForeignKeyConstraint(["scan_id"], ["catalog_scans.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(["data_source_id"], ["data_sources.id"], ondelete="CASCADE"),
            sa.UniqueConstraint("scan_id", "table_name", name="uq_catalog_tables_scan_table"),
        )
        op.create_index("ix_catalog_tables_scan_id", "catalog_tables", ["scan_id"])
        op.create_index("ix_catalog_tables_table_name", "catalog_tables", ["table_name"])

    if "catalog_columns" not in existing:
        op.create_table(
            "catalog_columns",
            sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
            sa.Column("catalog_table_id", postgresql.UUID(as_uuid=True), nullable=False),
            sa.Column("column_name", sa.String(), nullable=False),
            sa.Column("data_type", sa.String(), nullable=False),
            sa.Column("ordinal_position", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("is_nullable", sa.Boolean(), nullable=False, server_default=sa.true()),
            sa.Column("is_primary_key", sa.Boolean(), nullable=False, server_default=sa.false()),
            sa.Column("is_foreign_key", sa.Boolean(), nullable=False, server_default=sa.false()),
            sa.Column("normalized_name", sa.String(), nullable=True),
            sa.Column("sample_values", sa.JSON(), nullable=True),
            sa.Column("business_description", sa.String(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
            sa.ForeignKeyConstraint(["catalog_table_id"], ["catalog_tables.id"], ondelete="CASCADE"),
            sa.UniqueConstraint("catalog_table_id", "column_name", name="uq_catalog_columns_table_column"),
        )
        op.create_index("ix_catalog_columns_catalog_table_id", "catalog_columns", ["catalog_table_id"])
        op.create_index("ix_catalog_columns_column_name", "catalog_columns", ["column_name"])
        op.create_index("ix_catalog_columns_normalized_name", "catalog_columns", ["normalized_name"])

    if "catalog_relationships" not in existing:
        op.create_table(
            "catalog_relationships",
            sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
            sa.Column("scan_id", postgresql.UUID(as_uuid=True), nullable=False),
            sa.Column("data_source_id", postgresql.UUID(as_uuid=True), nullable=False),
            sa.Column("left_table", sa.String(), nullable=False),
            sa.Column("left_column", sa.String(), nullable=False),
            sa.Column("right_table", sa.String(), nullable=False),
            sa.Column("right_column", sa.String(), nullable=False),
            sa.Column("constraint_name", sa.String(), nullable=True),
            sa.Column("origin", sa.String(), nullable=False, server_default="declared"),
            sa.Column("confidence", sa.Float(), nullable=False, server_default="1.0"),
            sa.Column("cardinality", sa.String(), nullable=True),
            sa.Column("approved", sa.Boolean(), nullable=True),
            sa.Column("approved_by", sa.String(), nullable=True),
            sa.Column("approved_at", sa.DateTime(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
            sa.ForeignKeyConstraint(["scan_id"], ["catalog_scans.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(["data_source_id"], ["data_sources.id"], ondelete="CASCADE"),
            sa.UniqueConstraint(
                "scan_id", "left_table", "left_column", "right_table", "right_column",
                name="uq_catalog_relationships_edge",
            ),
        )
        op.create_index("ix_catalog_relationships_scan_id", "catalog_relationships", ["scan_id"])


def downgrade() -> None:
    existing = _existing_tables()
    for table in _TABLES:
        if context.is_offline_mode() or table in existing:
            op.drop_table(table)
