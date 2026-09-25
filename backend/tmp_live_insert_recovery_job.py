import json
from pathlib import Path

from database import SessionLocal
from models.agent_run import AgentRun
from models.project import Project
from models.sandbox_job import SandboxJob
from models.training_run import TrainingRun

state_path = Path("tmp_worker_test_state.json")
state = json.loads(state_path.read_text(encoding="utf-8"))


def main() -> None:
    db = SessionLocal()
    try:
        project = db.query(Project).filter(Project.id == state["project_id"]).first()
        agent = db.query(AgentRun).filter(AgentRun.id == state["agent_id"]).first()
        if project is None or agent is None:
            raise SystemExit("Missing project/agent in state. Run tmp_live_insert_jobs.py first.")

        recovery_script = """import time\nprint('recovery-start')\ntime.sleep(10)\nprint('recovery-end')\n"""
        job = SandboxJob(
            agent_id=agent.id,
            project_id=project.id,
            iteration_number=601,
            script_content=recovery_script,
            status="queued",
            timeout_seconds=60,
        )
        db.add(job)
        db.commit()

        state["recovery_job_id_live"] = str(job.id)
        state_path.write_text(json.dumps(state, indent=2), encoding="utf-8")
        print(f"RECOVERY_JOB_INSERTED {job.id}")
    finally:
        db.close()


if __name__ == "__main__":
    main()
