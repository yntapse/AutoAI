import json
import time
from pathlib import Path

from database import SessionLocal
from models.sandbox_job import SandboxJob

state = json.loads(Path("tmp_worker_test_state.json").read_text(encoding="utf-8"))
job_id = state["single_job_id"]


def read_status():
    db = SessionLocal()
    try:
        job = db.query(SandboxJob).filter(SandboxJob.id == job_id).first()
        if job is None:
            return {"missing": True}
        return {
            "status": job.status,
            "started_at": job.started_at.isoformat() if job.started_at else None,
            "completed_at": job.completed_at.isoformat() if job.completed_at else None,
            "result_json": job.result_json,
        }
    finally:
        db.close()


snap0 = read_status()
time.sleep(1)
snap1 = read_status()
time.sleep(2.5)
snap35 = read_status()

print("SNAPSHOT_0", snap0)
print("SNAPSHOT_1S", snap1)
print("SNAPSHOT_3P5S", snap35)
