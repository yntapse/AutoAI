import uuid
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from database import SessionLocal
from main import run_agent_loop
from models.agent_run import AgentRun
from models.model_version import ModelVersion
from models.project import Project
from models.training_run import TrainingRun
from models.training_run import TrainingRun


def main() -> None:
    db = SessionLocal()
    try:
        upload_candidates = sorted(Path("uploads").glob("*.csv"))
        if not upload_candidates:
            raise RuntimeError("No CSV files found under backend/uploads")

        dataset_df = pd.read_csv(upload_candidates[0])
        numeric_cols = dataset_df.select_dtypes(include=["number"]).columns.tolist()
        if not numeric_cols:
            raise RuntimeError("No numeric target column found in selected dataset")
        chosen_target = numeric_cols[-1]

        project = Project(
            project_name=f"autonomous-iter-{uuid.uuid4()}",
            file_id=upload_candidates[0].name,
            target_column=chosen_target,
            num_rows=len(dataset_df),
            num_features=max(0, len(dataset_df.columns) - 1),
            num_numeric_features=max(0, len(numeric_cols) - 1),
            num_categorical_features=max(0, len(dataset_df.columns) - len(numeric_cols)),
            missing_value_count=int(dataset_df.isnull().sum().sum()),
            target_variance=float(dataset_df[chosen_target].var() if chosen_target in dataset_df else 0.0),
        )
        db.add(project)
        db.flush()

        agent = AgentRun(
            project_id=project.id,
            status="running",
            current_iteration=0,
            max_iterations=1,
            improvement_threshold=0.001,
            started_at=datetime.now(timezone.utc),
        )
        db.add(agent)
        db.commit()
        agent_id = agent.id
        project_id = project.id
    finally:
        db.close()

    run_agent_loop(agent_id)

    verify_db = SessionLocal()
    try:
        final_agent = verify_db.query(AgentRun).filter(AgentRun.id == agent_id).first()
        run = (
            verify_db.query(TrainingRun)
            .filter(TrainingRun.agent_run_id == agent_id)
            .order_by(TrainingRun.created_at.desc())
            .first()
        )
        versions = (
            verify_db.query(ModelVersion)
            .filter(ModelVersion.training_run_id == run.id)
            .order_by(ModelVersion.rank_position.asc())
            .all()
            if run is not None
            else []
        )

        print("AUTONOMOUS_ITER_RESULT", {
            "project_id": str(project_id),
            "agent_id": str(agent_id),
            "agent_status": final_agent.status if final_agent else None,
            "training_run_status": run.status if run else None,
            "best_model_name": run.best_model_name if run else None,
            "rmse": run.rmse if run else None,
            "mae": run.mae if run else None,
            "r2": run.r2 if run else None,
            "model_versions_count": len(versions),
        })
    finally:
        verify_db.close()


if __name__ == "__main__":
    main()
