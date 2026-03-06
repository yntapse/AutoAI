import json
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

from database import SessionLocal
from models.agent_run import AgentRun
from models.project import Project
from models.training_run import TrainingRun
from models.sandbox_job import SandboxJob

STATE_PATH = Path("tmp_worker_test_state.json")


def load_state():
    return json.loads(STATE_PATH.read_text(encoding="utf-8"))


def save_state(state):
    STATE_PATH.write_text(json.dumps(state, indent=2), encoding="utf-8")


def reset_single():
    state = load_state()
    db = SessionLocal()
    try:
        job = db.query(SandboxJob).filter(SandboxJob.id == state["single_job_id"]).first()
        if job is None:
            print("SINGLE_JOB_MISSING")
            return
        job.status = "queued"
        job.started_at = None
        job.completed_at = None
        job.result_json = None
        job.error_log = None
        db.commit()
        print("RESET_SINGLE", str(job.id))
    finally:
        db.close()


def snapshot_single():
    state = load_state()
    db = SessionLocal()
    try:
        job = db.query(SandboxJob).filter(SandboxJob.id == state["single_job_id"]).first()
        if job is None:
            print("SINGLE_SNAPSHOT", {"missing": True})
            return
        print(
            "SINGLE_SNAPSHOT",
            {
                "id": str(job.id),
                "status": job.status,
                "started_at": job.started_at.isoformat() if job.started_at else None,
                "completed_at": job.completed_at.isoformat() if job.completed_at else None,
                "result_json": job.result_json,
            },
        )
    finally:
        db.close()


def insert_multi(count: int):
    state = load_state()
    db = SessionLocal()
    try:
        project_id = state["project_id"]
        agent_id = state["agent_id"]
        inserted = []
        for i in range(count):
            job = SandboxJob(
                agent_id=agent_id,
                project_id=project_id,
                iteration_number=100 + i,
                script_content=f"print('multi {i}')",
                status="queued",
                timeout_seconds=60,
            )
            db.add(job)
            db.flush()
            inserted.append(str(job.id))
        db.commit()
        state["multi_job_ids"] = inserted
        save_state(state)
        print("INSERT_MULTI", inserted)
    finally:
        db.close()


def snapshot_multi():
    state = load_state()
    ids = state.get("multi_job_ids", [])
    db = SessionLocal()
    try:
        jobs = db.query(SandboxJob).filter(SandboxJob.id.in_(ids)).order_by(SandboxJob.created_at.asc()).all() if ids else []
        payload = []
        for j in jobs:
            payload.append(
                {
                    "id": str(j.id),
                    "status": j.status,
                    "started_at": j.started_at.isoformat() if j.started_at else None,
                    "completed_at": j.completed_at.isoformat() if j.completed_at else None,
                }
            )
        print("MULTI_SNAPSHOT", payload)
    finally:
        db.close()


def insert_recovery_job():
    state = load_state()
    db = SessionLocal()
    try:
        job = SandboxJob(
            agent_id=state["agent_id"],
            project_id=state["project_id"],
            iteration_number=999,
            script_content="print('recovery')",
            status="queued",
            timeout_seconds=60,
        )
        db.add(job)
        db.commit()
        state["recovery_job_id"] = str(job.id)
        save_state(state)
        print("INSERT_RECOVERY", str(job.id))
    finally:
        db.close()


def snapshot_recovery():
    state = load_state()
    rid = state.get("recovery_job_id")
    db = SessionLocal()
    try:
        job = db.query(SandboxJob).filter(SandboxJob.id == rid).first() if rid else None
        print(
            "RECOVERY_SNAPSHOT",
            {
                "id": rid,
                "status": job.status if job else None,
                "started_at": job.started_at.isoformat() if job and job.started_at else None,
                "completed_at": job.completed_at.isoformat() if job and job.completed_at else None,
                "result_json": job.result_json if job else None,
            },
        )
    finally:
        db.close()


if __name__ == "__main__":
    cmd = sys.argv[1]
    if cmd == "reset_single":
        reset_single()
    elif cmd == "snapshot_single":
        snapshot_single()
    elif cmd == "insert_multi":
        insert_multi(int(sys.argv[2]))
    elif cmd == "snapshot_multi":
        snapshot_multi()
    elif cmd == "insert_recovery":
        insert_recovery_job()
    elif cmd == "snapshot_recovery":
        snapshot_recovery()
    else:
        raise SystemExit(f"Unknown command: {cmd}")
