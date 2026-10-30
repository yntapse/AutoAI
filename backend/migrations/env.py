from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

from database import Base, DATABASE_URL
from models.agent_decision import AgentDecision
from models.cleaning_rule import CleaningRule
from models.dataset_profile import DatasetProfile
from models.experiment_memory import ExperimentMemory
from models.model_version import ModelVersion
from models.pipeline_definition import PipelineDefinition
from models.pipeline_run import PipelineRun
from models.pipeline_version import PipelineVersion
from models.project import Project
from models.quality_rule import QualityRule
from models.sandbox_job import SandboxJob
from models.source_profile_snapshot import SourceProfileSnapshot
from models.training_run import TrainingRun
from models.transformation_rule import TransformationRule
from models.agent_run import AgentRun

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)
config.set_main_option("sqlalchemy.url", DATABASE_URL.replace("%", "%%"))

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    context.configure(
        url=DATABASE_URL,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        if connection.dialect.name == "postgresql":
            Base.metadata.create_all(bind=connection)
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_type=True,
            render_as_batch=connection.dialect.name == "sqlite",
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
