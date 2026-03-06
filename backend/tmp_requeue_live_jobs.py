import json
from pathlib import Path

from database import SessionLocal
from models.agent_run import AgentRun
from models.project import Project
from models.sandbox_job import SandboxJob
from models.training_run import TrainingRun

state = json.loads(Path("tmp_worker_test_state.json").read_text(encoding="utf-8"))
ids = []
if state.get("single_job_id_live"):
    ids.append(state["single_job_id_live"])
ids.extend(state.get("sequential_job_ids_live", []))


def main() -> None:
    db = SessionLocal()
    try:
        jobs = db.query(SandboxJob).filter(SandboxJob.id.in_(ids)).all() if ids else []
        for job in jobs:
            job.status = "queued"
            job.started_at = None
            job.completed_at = None
            job.result_json = None
            job.error_log = None
        db.commit()
        print(f"REQUEUED {len(jobs)}")
    finally:
        db.close()


if __name__ == "__main__":
    main()
