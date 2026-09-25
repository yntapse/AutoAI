import json
from pathlib import Path

from database import SessionLocal
from models.sandbox_job import SandboxJob

state = json.loads(Path("tmp_worker_test_state.json").read_text(encoding="utf-8"))
job_id = state.get("recovery_job_id_live")


def main() -> None:
    db = SessionLocal()
    try:
        job = db.query(SandboxJob).filter(SandboxJob.id == job_id).first() if job_id else None
        if job is None:
            print('{"missing": true}')
            return
        print(
            json.dumps(
                {
                    "id": str(job.id),
                    "status": job.status,
                    "started_at": job.started_at.isoformat() if job.started_at else None,
                    "completed_at": job.completed_at.isoformat() if job.completed_at else None,
                    "result_json": job.result_json,
                    "error_log": job.error_log,
                }
            )
        )
    finally:
        db.close()


if __name__ == "__main__":
    main()
