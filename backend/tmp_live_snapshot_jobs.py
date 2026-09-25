import json
from pathlib import Path

from database import SessionLocal
from models.sandbox_job import SandboxJob

state = json.loads(Path("tmp_worker_test_state.json").read_text(encoding="utf-8"))
single_id = state.get("single_job_id_live")
seq_ids = state.get("sequential_job_ids_live", [])


def snap(job):
    return {
        "id": str(job.id),
        "status": job.status,
        "started_at": job.started_at.isoformat() if job.started_at else None,
        "completed_at": job.completed_at.isoformat() if job.completed_at else None,
        "result_json": job.result_json,
        "error_log": job.error_log,
    }


def main() -> None:
    db = SessionLocal()
    try:
        out = {"single": None, "sequential": []}
        if single_id:
            job = db.query(SandboxJob).filter(SandboxJob.id == single_id).first()
            out["single"] = snap(job) if job else None

        if seq_ids:
            jobs = db.query(SandboxJob).filter(SandboxJob.id.in_(seq_ids)).all()
            by_id = {str(j.id): j for j in jobs}
            for jid in seq_ids:
                j = by_id.get(jid)
                out["sequential"].append(snap(j) if j else {"id": jid, "missing": True})

        print(json.dumps(out))
    finally:
        db.close()


if __name__ == "__main__":
    main()
