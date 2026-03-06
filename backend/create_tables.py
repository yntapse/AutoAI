from database import Base, engine
from models.project import Project
from models.agent_run import AgentRun
from models.sandbox_job import SandboxJob
from models.dataset_profile import DatasetProfile
from models.experiment_memory import ExperimentMemory


def create_tables() -> None:
    Base.metadata.create_all(bind=engine)
    print("Tables created successfully!")


if __name__ == "__main__":
    create_tables()
