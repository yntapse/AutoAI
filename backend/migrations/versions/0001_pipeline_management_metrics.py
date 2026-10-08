"""Add pipeline status and run metrics.

Revision ID: 0001_pipeline_management_metrics
Revises:
"""

from alembic import context, op
import sqlalchemy as sa

revision = "0001_pipeline_management_metrics"
down_revision = None
branch_labels = None
depends_on = None


def _has_column(table_name: str, column_name: str) -> bool:
    return any(
        column["name"] == column_name
        for column in sa.inspect(op.get_bind()).get_columns(table_name)
    )


def _has_unique_constraint(table_name: str, name: str) -> bool:
    return any(
        constraint.get("name") == name
        for constraint in sa.inspect(op.get_bind()).get_unique_constraints(table_name)
    )


def upgrade() -> None:
    if context.is_offline_mode():
        op.create_unique_constraint(
            "uq_pipeline_version_number",
            "pipeline_versions",
            ["pipeline_definition_id", "version_number"],
        )
        op.add_column(
            "pipeline_definitions",
            sa.Column(
                "status",
                sa.String(),
                nullable=False,
                server_default="active",
            ),
        )
        op.alter_column(
            "pipeline_runs",
            "pipeline_version_id",
            existing_type=sa.UUID(),
            nullable=True,
        )
        op.add_column(
            "pipeline_runs",
            sa.Column("dataset_file_id", sa.String(), nullable=True),
        )
        op.add_column(
            "pipeline_runs",
            sa.Column("row_count", sa.Integer(), nullable=True),
        )
        op.add_column(
            "pipeline_runs",
            sa.Column("execution_time", sa.Float(), nullable=True),
        )
        return
    if not _has_unique_constraint("pipeline_versions", "uq_pipeline_version_number"):
        with op.batch_alter_table("pipeline_versions") as batch:
            batch.create_unique_constraint(
                "uq_pipeline_version_number",
                ["pipeline_definition_id", "version_number"],
            )
    if not _has_column("pipeline_definitions", "status"):
        op.add_column(
            "pipeline_definitions",
            sa.Column(
                "status",
                sa.String(),
                nullable=False,
                server_default="active",
            ),
        )
    run_columns = {
        column["name"]: column
        for column in sa.inspect(op.get_bind()).get_columns("pipeline_runs")
    }
    if not run_columns["pipeline_version_id"]["nullable"]:
        with op.batch_alter_table("pipeline_runs") as batch:
            batch.alter_column(
                "pipeline_version_id",
                existing_type=sa.UUID(),
                nullable=True,
            )
    if not _has_column("pipeline_runs", "dataset_file_id"):
        op.add_column(
            "pipeline_runs",
            sa.Column("dataset_file_id", sa.String(), nullable=True),
        )
    if not _has_column("pipeline_runs", "row_count"):
        op.add_column(
            "pipeline_runs",
            sa.Column("row_count", sa.Integer(), nullable=True),
        )
    if not _has_column("pipeline_runs", "execution_time"):
        op.add_column(
            "pipeline_runs",
            sa.Column("execution_time", sa.Float(), nullable=True),
        )
def downgrade() -> None:
    if context.is_offline_mode():
        op.drop_column("pipeline_runs", "execution_time")
        op.drop_column("pipeline_runs", "row_count")
        op.drop_column("pipeline_runs", "dataset_file_id")
        op.alter_column(
            "pipeline_runs",
            "pipeline_version_id",
            existing_type=sa.UUID(),
            nullable=False,
        )
        op.drop_column("pipeline_definitions", "status")
        op.drop_constraint(
            "uq_pipeline_version_number",
            "pipeline_versions",
            type_="unique",
        )
        return
    connection = op.get_bind()
    if _has_column("pipeline_runs", "execution_time"):
        op.drop_column("pipeline_runs", "execution_time")
    if _has_column("pipeline_runs", "row_count"):
        op.drop_column("pipeline_runs", "row_count")
    if _has_column("pipeline_runs", "dataset_file_id"):
        op.drop_column("pipeline_runs", "dataset_file_id")
    if _has_column("pipeline_runs", "pipeline_version_id"):
        unlinked_count = connection.execute(
            sa.text(
                "SELECT COUNT(*) FROM pipeline_runs "
                "WHERE pipeline_version_id IS NULL"
            )
        ).scalar_one()
        if unlinked_count:
            raise RuntimeError(
                "Cannot downgrade while unlinked initial build runs exist."
            )
        with op.batch_alter_table("pipeline_runs") as batch:
            batch.alter_column(
                "pipeline_version_id",
                existing_type=sa.UUID(),
                nullable=False,
            )
    if _has_column("pipeline_definitions", "status"):
        op.drop_column("pipeline_definitions", "status")
    if _has_unique_constraint("pipeline_versions", "uq_pipeline_version_number"):
        with op.batch_alter_table("pipeline_versions") as batch:
            batch.drop_constraint("uq_pipeline_version_number", type_="unique")
