import json
import uuid
from datetime import datetime, timezone
from pathlib import Path

from database import SessionLocal
from models.agent_run import AgentRun
from models.project import Project
from models.sandbox_job import SandboxJob
from models.training_run import TrainingRun

state_path = Path("tmp_worker_test_state.json")
state = json.loads(state_path.read_text(encoding="utf-8")) if state_path.exists() else {}


def main() -> None:
    db = SessionLocal()
    try:
        upload_candidates = sorted(Path("uploads").glob("*.csv"))
        if not upload_candidates:
            raise SystemExit("No CSVs found in backend/uploads")
        chosen = upload_candidates[0].name

        project = Project(
            project_name=f"sandbox-live-{uuid.uuid4()}",
            file_id=chosen,
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
        state["project_id"] = str(project.id)
        state["agent_id"] = str(agent.id)
        state["project_file_id_live"] = chosen

        single_script = """import pandas as pd\nfrom pathlib import Path\n\ndf = pd.read_csv('/app/dataset.csv')\nprint('rows', len(df))\nprint('cols', len(df.columns))\nprint('dataset_exists', Path('/app/dataset.csv').exists())\n"""
        single_job = SandboxJob(
            agent_id=agent.id,
            project_id=project.id,
            iteration_number=501,
            script_content=single_script,
            status="queued",
            timeout_seconds=60,
        )
        db.add(single_job)
        db.flush()

        seq_script_a = """import time\nprint('seq-A-start')\ntime.sleep(1)\nprint('seq-A-end')\n"""
        seq_script_b = """import time\nprint('seq-B-start')\ntime.sleep(1)\nprint('seq-B-end')\n"""
        seq_a = SandboxJob(
            agent_id=agent.id,
            project_id=project.id,
            iteration_number=502,
            script_content=seq_script_a,
            status="queued",
            timeout_seconds=60,
        )
        seq_b = SandboxJob(
            agent_id=agent.id,
            project_id=project.id,
            iteration_number=503,
            script_content=seq_script_b,
            status="queued",
            timeout_seconds=60,
        )
        db.add(seq_a)
        db.add(seq_b)
        db.commit()

        state["single_job_id_live"] = str(single_job.id)
        state["sequential_job_ids_live"] = [str(seq_a.id), str(seq_b.id)]
        state_path.write_text(json.dumps(state, indent=2), encoding="utf-8")

        print(
            "LIVE_INSERTED",
            json.dumps(
                {
                    "project_id": state["project_id"],
                    "agent_id": state["agent_id"],
                    "single_job_id_live": state["single_job_id_live"],
                    "sequential_job_ids_live": state["sequential_job_ids_live"],
                }
            ),
        )
    finally:
        db.close()


if __name__ == "__main__":
    main()
