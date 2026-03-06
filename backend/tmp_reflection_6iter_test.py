import io
import json
import os
import re
import uuid
from contextlib import redirect_stdout
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from database import SessionLocal
from main import run_agent_loop
from models.agent_run import AgentRun
from models.project import Project
from models.sandbox_job import SandboxJob
from models.training_run import TrainingRun


def _extract_model_family(script_content: str) -> str:
    """Extract the primary training model family from explicit `model = Estimator(...)`.

    Rules:
    - Only match assignments to the exact variable name `model`
    - Ignore helper variables (e.g. `_importance_model`, `_selector_model`)
    - Ignore estimator mentions not tied to `model = ...`
    - Return the latest model assignment before `model.fit(...)`
    """
    candidates = {
        "XGBRegressor",
        "RandomForestRegressor",
        "LinearRegression",
        "Ridge",
        "Lasso",
        "ElasticNet",
    }

    fit_match = re.search(r"^\s*model\.fit\s*\(", script_content, flags=re.MULTILINE)
    fit_pos = fit_match.start() if fit_match else len(script_content)

    assignment_re = re.compile(
        r"^\s*model\s*=\s*([A-Za-z_][A-Za-z0-9_]*)\s*\(",
        flags=re.MULTILINE,
    )

    selected = None
    for match in assignment_re.finditer(script_content):
        if match.start() <= fit_pos:
            estimator_name = match.group(1)
            if estimator_name in candidates:
                selected = estimator_name

    if selected:
        return selected
    return "unknown"


def _extract_preprocessing(script_content: str) -> list[str]:
    features = []
    checks = {
        "one_hot": "get_dummies",
        "scaling": "StandardScaler",
        "polynomial": "PolynomialFeatures",
        "imputation": "SimpleImputer",
        "feature_selection": "SelectKBest",
        "pca": "PCA(",
        "train_test_split": "train_test_split",
    }
    for name, token in checks.items():
        if token in script_content:
            features.append(name)
    return features


def _extract_training_mutations(script_content: str) -> list[str]:
    features = []
    checks = {
        "cv": "cross_val_score",
        "grid_search": "GridSearchCV",
        "random_search": "RandomizedSearchCV",
        "early_stopping": "early_stopping",
        "ensembling": "VotingRegressor",
        "stacking": "StackingRegressor",
        "feature_selection": "SelectKBest",
        "regularization": "alpha=",
        "fit": "model.fit(",
        "predict": "predictions = model.predict(",
    }
    for name, token in checks.items():
        if token in script_content:
            features.append(name)
    return features


def _strategy_complexity(text: str) -> int:
    token_count = len(re.findall(r"\w+", text))
    technique_count = 0
    for keyword in [
        "boost",
        "forest",
        "scale",
        "polynomial",
        "feature",
        "regular",
        "cross",
        "ensemble",
        "preprocess",
        "variance",
    ]:
        if keyword in text.lower():
            technique_count += 1
    return token_count + (technique_count * 5)


def main() -> None:
    db = SessionLocal()
    try:
        csv_candidates = sorted(Path("uploads").glob("*.csv"))
        if not csv_candidates:
            raise RuntimeError("No CSV files found under backend/uploads")

        dataset_path = csv_candidates[0]
        df = pd.read_csv(dataset_path)
        numeric_cols = df.select_dtypes(include=["number"]).columns.tolist()
        if not numeric_cols:
            raise RuntimeError("No numeric column available for target")
        target_column = numeric_cols[-1]

        project = Project(
            project_name=f"reflection-6iter-{uuid.uuid4()}",
            file_id=dataset_path.name,
            target_column=target_column,
            num_rows=len(df),
            num_features=max(0, len(df.columns) - 1),
            num_numeric_features=max(0, len(numeric_cols) - 1),
            num_categorical_features=max(0, len(df.columns) - len(numeric_cols)),
            missing_value_count=int(df.isnull().sum().sum()),
            target_variance=float(df[target_column].var() if target_column in df else 0.0),
        )
        db.add(project)
        db.flush()

        agent = AgentRun(
            project_id=project.id,
            status="running",
            current_iteration=0,
            max_iterations=6,
            improvement_threshold=0.0001,
            started_at=datetime.now(timezone.utc),
        )
        db.add(agent)
        db.commit()
        agent_id = agent.id
    finally:
        db.close()

    os.environ["AGENT_EXECUTION_MODE"] = "sandbox"

    stream = io.StringIO()
    with redirect_stdout(stream):
        run_agent_loop(agent_id)
    loop_output = stream.getvalue()

    strategy_lines = []
    exploitation_lock_events = []
    exploration_events = []
    preprocessing_mutation_events = []
    for line in loop_output.splitlines():
        if "SANDBOX STRATEGY:" in line:
            strategy_lines.append(line.split("SANDBOX STRATEGY:", 1)[1].strip())
        if "EXPLOITATION LOCK ACTIVATED:" in line:
            exploitation_lock_events.append(line.strip())
        if "EXPLOITATION LOCK ITERATION:" in line:
            exploitation_lock_events.append(line.strip())
        if "EXPLORATION MODEL SELECTED" in line:
            exploration_events.append(line.strip())
        if "PREPROCESSING MUTATION MODE:" in line:
            preprocessing_mutation_events.append(line.strip())

    verify_db = SessionLocal()
    try:
        jobs = (
            verify_db.query(SandboxJob)
            .filter(SandboxJob.agent_id == agent_id)
            .order_by(SandboxJob.iteration_number.asc())
            .all()
        )
        runs = (
            verify_db.query(TrainingRun)
            .filter(TrainingRun.agent_run_id == agent_id)
            .order_by(TrainingRun.version_number.asc())
            .all()
        )

        model_families = []
        preprocess_by_iter = []
        training_by_iter = []
        for job in jobs:
            script = job.script_content or ""
            model_families.append(_extract_model_family(script))
            preprocess_by_iter.append(_extract_preprocessing(script))
            training_by_iter.append(_extract_training_mutations(script))

        rmse_history = [r.rmse for r in runs if r.rmse is not None]

        strategy_complexity = [_strategy_complexity(s) for s in strategy_lines]

        result = {
            "agent_id": str(agent_id),
            "iterations_requested": 6,
            "iterations_executed": len(jobs),
            "strategy_texts": strategy_lines,
            "strategy_complexity_scores": strategy_complexity,
            "model_families": model_families,
            "preprocessing_tokens": preprocess_by_iter,
            "training_mutation_tokens": training_by_iter,
            "rmse_history": rmse_history,
            "model_family_changed": len(set(model_families)) > 1,
            "preprocessing_evolved": len({tuple(x) for x in preprocess_by_iter}) > 1,
            "training_mutation_evolved": len({tuple(x) for x in training_by_iter}) > 1,
            "strategy_complexity_increased": (
                len(strategy_complexity) >= 2 and strategy_complexity[-1] > strategy_complexity[0]
            ),
            "exploitation_lock_events": exploitation_lock_events,
            "exploration_events": exploration_events,
            "preprocessing_mutation_events": preprocessing_mutation_events,
        }
        print("REFLECTION_TEST_RESULT", json.dumps(result))
    finally:
        verify_db.close()


if __name__ == "__main__":
    main()
