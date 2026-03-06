import json
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

from database import SessionLocal
from models.agent_run import AgentRun
from models.project import Project
from models.sandbox_job import SandboxJob
from models.training_run import TrainingRun


POLL_INTERVAL_SECONDS = 1
MAX_WAIT_SECONDS = 120


def ensure_project_and_agent(db):
    upload_candidates = sorted(Path("uploads").glob("*.csv"))
    if not upload_candidates:
        raise RuntimeError("No CSV files found under backend/uploads")

    project = Project(
        project_name=f"sandbox-case-tests-{uuid.uuid4()}",
        file_id=upload_candidates[0].name,
        target_column="target",
        num_rows=10,
        num_features=3,
        num_numeric_features=3,
        num_categorical_features=0,
        missing_value_count=0,
        target_variance=1.0,
    )
    db.add(project)
    db.flush()

    agent = AgentRun(
        project_id=project.id,
        status="queued",
        current_iteration=0,
        max_iterations=4,
        improvement_threshold=0.001,
        started_at=datetime.now(timezone.utc),
    )
    db.add(agent)
    db.flush()

    return project, agent


def create_jobs(db, project, agent):
    jobs = {}

    good_script = """import json\nprint(json.dumps({\"rmse\": 0.32, \"mae\": 0.28, \"r2\": 0.91}))\n"""
    infinite_loop_script = """while True:\n    pass\n"""
    syntax_error_script = """def broken(:\n    return 1\n"""
    invalid_json_script = """print('not-json-output')\n"""

    cases = [
        ("success", good_script, 30),
        ("timeout", infinite_loop_script, 2),
        ("syntax_error", syntax_error_script, 30),
        ("invalid_json", invalid_json_script, 30),
    ]

    for idx, (case_name, script_content, timeout_seconds) in enumerate(cases, start=1):
        job = SandboxJob(
            agent_id=agent.id,
            project_id=project.id,
            iteration_number=700 + idx,
            script_content=script_content,
            status="queued",
            timeout_seconds=timeout_seconds,
        )
        db.add(job)
        db.flush()
        jobs[case_name] = str(job.id)

    db.commit()
    return jobs


def fetch_statuses(db, jobs):
    out = {}
    for case_name, job_id in jobs.items():
        row = db.query(SandboxJob).filter(SandboxJob.id == job_id).first()
        if row is None:
            out[case_name] = {"missing": True}
            continue
        out[case_name] = {
            "id": str(row.id),
            "status": row.status,
            "result_json": row.result_json,
            "error_log": row.error_log,
            "started_at": row.started_at.isoformat() if row.started_at else None,
            "completed_at": row.completed_at.isoformat() if row.completed_at else None,
        }
    return out


def all_done(statuses):
    terminal = {"completed", "failed", "timeout"}
    return all(v.get("status") in terminal for v in statuses.values() if not v.get("missing"))


def main():
    db = SessionLocal()
    try:
        project, agent = ensure_project_and_agent(db)
        jobs = create_jobs(db, project, agent)

        print("CASE_JOB_IDS", json.dumps(jobs))

        started = time.time()
        last_statuses = {}
        while time.time() - started <= MAX_WAIT_SECONDS:
            statuses = fetch_statuses(db, jobs)
            if statuses != last_statuses:
                print("CASE_STATUSES", json.dumps(statuses))
                last_statuses = statuses

            if all_done(statuses):
                print("FINAL_CASE_STATUSES", json.dumps(statuses))
                return

            time.sleep(POLL_INTERVAL_SECONDS)
            db.expire_all()

        print("FINAL_CASE_STATUSES_TIMEOUT", json.dumps(last_statuses))
    finally:
        db.close()


if __name__ == "__main__":
    main()
