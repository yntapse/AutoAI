from database import Base, engine
from models.project import Project
from models.agent_run import AgentRun
from models.sandbox_job import SandboxJob
from models.dataset_profile import DatasetProfile
from models.experiment_memory import ExperimentMemory
from models.pipeline_definition import PipelineDefinition
from models.pipeline_version import PipelineVersion
from models.pipeline_run import PipelineRun
from models.cleaning_rule import CleaningRule
from models.transformation_rule import TransformationRule
from models.agent_decision import AgentDecision
from models.source_profile_snapshot import SourceProfileSnapshot
from models.quality_rule import QualityRule


def create_tables() -> None:
    Base.metadata.create_all(bind=engine)
    print("Tables created successfully!")


if __name__ == "__main__":
    create_tables()
