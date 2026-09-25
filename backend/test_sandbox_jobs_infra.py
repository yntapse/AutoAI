import uuid
from datetime import datetime, timezone

from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from database import SessionLocal, engine
from models.agent_run import AgentRun
from models.project import Project
from models.sandbox_job import SandboxJob
from models.training_run import TrainingRun


def create_minimum_project_and_agent(session):
    project = Project(
        project_name=f"sandbox-infra-{uuid.uuid4()}",
        file_id=f"file-{uuid.uuid4()}",
        target_column="target",
        num_rows=10,
        num_features=3,
        num_numeric_features=3,
        num_categorical_features=0,
        missing_value_count=0,
        target_variance=1.0,
    )
    session.add(project)
    session.flush()

    agent = AgentRun(
        project_id=project.id,
        status="queued",
        current_iteration=0,
        max_iterations=4,
        improvement_threshold=0.001,
        started_at=datetime.now(timezone.utc),
    )
    session.add(agent)
    session.flush()

    return project, agent


def assert_index_exists(session, index_name: str):
    row = session.execute(
        text(
            """
            SELECT 1
            FROM pg_indexes
            WHERE schemaname = 'public'
              AND tablename = 'sandbox_jobs'
              AND indexname = :index_name
            """
        ),
        {"index_name": index_name},
    ).first()
    assert row is not None, f"Index not found: {index_name}"


def main():
    # Ensure table exists (migration-safe: create only if missing)
    SandboxJob.__table__.create(bind=engine, checkfirst=True)

    session = SessionLocal()
    project = None
    agent = None

    try:
        project, agent = create_minimum_project_and_agent(session)

        # 1) Insert dummy valid job
        valid_job = SandboxJob(
            agent_id=agent.id,
            project_id=project.id,
            iteration_number=1,
            script_content="print('hello sandbox')",
            status="queued",
            result_json={"ok": True},
            timeout_seconds=60,
        )
        session.add(valid_job)
        session.commit()
        print(f"PASS: Dummy job inserted ({valid_job.id})")

        # 2) Validate CHECK constraint on status
        invalid_status_job = SandboxJob(
            agent_id=agent.id,
            project_id=project.id,
            iteration_number=2,
            script_content="print('bad status')",
            status="invalid_status",
            timeout_seconds=60,
        )
        session.add(invalid_status_job)
        try:
            session.commit()
            raise AssertionError("CHECK constraint failed to block invalid status")
        except IntegrityError:
            session.rollback()
            print("PASS: CHECK constraint enforced for status")

        # 3) Validate foreign keys
        invalid_fk_job = SandboxJob(
            agent_id=uuid.uuid4(),
            project_id=project.id,
            iteration_number=3,
            script_content="print('bad fk')",
            status="queued",
            timeout_seconds=60,
        )
        session.add(invalid_fk_job)
        try:
            session.commit()
            raise AssertionError("FK constraint failed to block invalid agent_id")
        except IntegrityError:
            session.rollback()
            print("PASS: Foreign key enforced for agent_id")

        invalid_fk_job_2 = SandboxJob(
            agent_id=agent.id,
            project_id=uuid.uuid4(),
            iteration_number=4,
            script_content="print('bad fk project')",
            status="queued",
            timeout_seconds=60,
        )
        session.add(invalid_fk_job_2)
        try:
            session.commit()
            raise AssertionError("FK constraint failed to block invalid project_id")
        except IntegrityError:
            session.rollback()
            print("PASS: Foreign key enforced for project_id")

        # 4) Validate index existence
        assert_index_exists(session, "ix_sandbox_jobs_status")
        assert_index_exists(session, "ix_sandbox_jobs_created_at")
        print("PASS: Required indexes exist (status, created_at)")

        print("PASS: All sandbox_jobs infrastructure validations succeeded")

    finally:
        # Cleanup test rows; cascading will remove dependent rows
        if project is not None:
            session.query(Project).filter(Project.id == project.id).delete()
            session.commit()
        session.close()


if __name__ == "__main__":
    main()
