import os
import copy
import uuid
import json
import time
import re
import io
import zipfile
import pickle
import sys
import subprocess
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeoutError
from pathlib import Path
from typing import List, Dict, Any, Callable, Optional, Tuple
from dotenv import load_dotenv

load_dotenv()

DATABASE_URL = os.getenv("DATABASE_URL")

import pandas as pd
from fastapi import FastAPI, File, Form, UploadFile, HTTPException, Body, BackgroundTasks, Depends
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, Field, ConfigDict
from sqlalchemy import func, and_, inspect, text
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

# ML imports
from sklearn.model_selection import train_test_split
from sklearn.linear_model import LinearRegression, Ridge, Lasso, ElasticNet
from sklearn.ensemble import RandomForestRegressor
from sklearn.metrics import mean_squared_error, mean_absolute_error, r2_score
from models.training_run import TrainingRun
from models.model_version import ModelVersion
from models.agent_run import AgentRun
from models.sandbox_job import SandboxJob
from models.dataset_profile import DatasetProfile
from models.experiment_memory import ExperimentMemory
from models.project import Project
from xgboost import XGBRegressor
import numpy as np

from database import Base, SessionLocal, engine
from services.llm_service import get_hyperparameter_suggestions, validate_hyperparameters, SAFE_HYPERPARAMETERS
from services.llm_service import get_hyperparameter_suggestions, validate_hyperparameters, SAFE_HYPERPARAMETERS

app = FastAPI(title="PyrunAI Backend", version="1.0.0")

cors_origins_env = os.getenv("CORS_ALLOW_ORIGINS")
if cors_origins_env:
    allow_origins = [origin.strip() for origin in cors_origins_env.split(",") if origin.strip()]
else:
    allow_origins = [
        "http://localhost:3000",
        "http://127.0.0.1:3000",
        "http://localhost:3010",
        "http://127.0.0.1:3010",
    ]

app.add_middleware(
    CORSMiddleware,
    allow_origins=allow_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

try:
    Base.metadata.create_all(bind=engine)
except OperationalError as exc:
    detailed_message = str(getattr(exc, "orig", exc))
    raise RuntimeError(
        "Database connection failed during startup. "
        "Set DATABASE_URL correctly and ensure PostgreSQL is running "
        f"and accessible. Details: {detailed_message}"
    ) from exc
except Exception as exc:
    raise RuntimeError(
        "Backend failed to initialize database metadata. "
        "If using PostgreSQL, verify the driver is installed "
        "(e.g. `pip install psycopg2-binary`) and DATABASE_URL is valid."
    ) from exc

# Configuration
UPLOAD_DIR = Path("uploads")
UPLOAD_DIR.mkdir(exist_ok=True)
MAX_FILE_SIZE_MB = 20
MAX_ROWS = 50000
MAX_COLUMNS = 200
MAX_FILE_SIZE_BYTES = MAX_FILE_SIZE_MB * 1024 * 1024
JOB_TTL_MINUTES = 10
MAX_JOB_STATUS_SIZE = 100
FINE_TUNE_USE_SANDBOX = os.getenv("FINE_TUNE_USE_SANDBOX", "true").lower() in {"true", "1", "yes"}
FINE_TUNE_SANDBOX_TIMEOUT_SECONDS = max(30, int(os.getenv("FINE_TUNE_SANDBOX_TIMEOUT_SECONDS", "240")))
FINE_TUNE_SANDBOX_INACTIVITY_TIMEOUT_SECONDS = max(
    20,
    int(os.getenv("FINE_TUNE_SANDBOX_INACTIVITY_TIMEOUT_SECONDS", "45")),
)
SANDBOX_AUTOSTART_WORKER = os.getenv("SANDBOX_AUTOSTART_WORKER", "true").lower() in {"true", "1", "yes"}
SANDBOX_WORKER_BOOT_TIMEOUT_SECONDS = max(
    1,
    int(os.getenv("SANDBOX_WORKER_BOOT_TIMEOUT_SECONDS", "8")),
)

# In-memory storage for training history
TRAINING_HISTORY: Dict[str, List[Dict[str, Any]]] = {}
JOB_STATUS: Dict[str, Dict[str, Any]] = {}

# Track models currently being trained for each agent run (for parallel execution UI)
AGENT_MODELS_IN_PROGRESS: Dict[str, List[Dict[str, Any]]] = {}
AGENT_SANDBOX_EVENT_LOGS: Dict[str, List[str]] = {}
MAX_AGENT_SANDBOX_EVENT_LOGS = 250


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def _update_job_status(
    job_id: str,
    status: str = None,
    stage: str = None,
    progress: int = None,
    result: Dict[str, Any] = None,
    error: str = None,
    completed_at: datetime = None,
) -> None:
    """
    Update JOB_STATUS fields for a given job without overwriting unspecified keys.
    """
    current = JOB_STATUS.get(job_id, {
        "status": "pending",
        "stage": "pending",
        "progress": 0,
        "result": None,
        "error": None,
        "completed_at": None,
    })

    if status is not None:
        current["status"] = status
    if stage is not None:
        current["stage"] = stage
    if progress is not None:
        current["progress"] = progress
    if result is not None:
        current["result"] = result
    if error is not None:
        current["error"] = error
    if completed_at is not None:
        current["completed_at"] = completed_at

    JOB_STATUS[job_id] = current


def _append_job_log(job_id: Optional[str], message: str) -> None:
    if job_id is None:
        return

    current = JOB_STATUS.get(job_id)
    if current is None:
        return

    logs = current.get("logs")
    if not isinstance(logs, list):
        logs = []

    timestamp = datetime.utcnow().strftime("%H:%M:%S")
    logs.append(f"[{timestamp}] {message}")
    current["logs"] = logs
    JOB_STATUS[job_id] = current


def _append_agent_sandbox_event(agent_id: Optional[Any], message: str) -> None:
    if agent_id is None:
        return

    agent_key = str(agent_id)
    logs = AGENT_SANDBOX_EVENT_LOGS.get(agent_key, [])
    timestamp = datetime.utcnow().strftime("%H:%M:%S")
    logs.append(f"[{timestamp}] {message}")
    AGENT_SANDBOX_EVENT_LOGS[agent_key] = logs[-MAX_AGENT_SANDBOX_EVENT_LOGS:]


def _is_sandbox_worker_running() -> bool:
    try:
        import psutil  # type: ignore
    except Exception:
        # If psutil is unavailable, we cannot reliably detect worker processes.
        return False

    current_pid = os.getpid()
    for proc in psutil.process_iter(["pid", "cmdline"]):
        try:
            if proc.info.get("pid") == current_pid:
                continue
            cmdline = proc.info.get("cmdline") or []
            cmdline_str = " ".join(str(arg) for arg in cmdline)
            if "sandbox_worker.py" in cmdline_str and "check_sandbox_worker" not in cmdline_str:
                return True
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
    return False


def _ensure_sandbox_worker_running() -> None:
    if not SANDBOX_AUTOSTART_WORKER:
        return

    if _is_sandbox_worker_running():
        return

    backend_dir = Path(__file__).resolve().parent
    launch_env = os.environ.copy()
    launch_env.setdefault("SANDBOX_WORKER_CONCURRENCY", "2")

    popen_kwargs: Dict[str, Any] = {
        "cwd": str(backend_dir),
        "env": launch_env,
        "stdin": subprocess.DEVNULL,
        "stdout": subprocess.DEVNULL,
        "stderr": subprocess.DEVNULL,
        "close_fds": True,
    }

    if os.name == "nt":
        popen_kwargs["creationflags"] = (
            subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.DETACHED_PROCESS
        )

    subprocess.Popen([sys.executable, "sandbox_worker.py"], **popen_kwargs)

    deadline = time.time() + SANDBOX_WORKER_BOOT_TIMEOUT_SECONDS
    while time.time() < deadline:
        if _is_sandbox_worker_running():
            print("SANDBOX AUTOSTART: sandbox worker started successfully")
            return
        time.sleep(0.3)

    raise RuntimeError(
        "Sandbox worker could not be started automatically. "
        "Start backend/sandbox_worker.py manually and retry."
    )


def _set_agent_model_status(
    agent_id: Any,
    model_name: str,
    status: str,
    rmse: Optional[float] = None,
    r2: Optional[float] = None,
    mae: Optional[float] = None,
    job_id: Optional[uuid.UUID] = None,
    error: Optional[str] = None,
) -> None:
    agent_key = str(agent_id)
    models = AGENT_MODELS_IN_PROGRESS.get(agent_key, [])
    for model in models:
        if model.get("name") != model_name:
            continue

        model["status"] = status
        model["rmse"] = rmse
        if r2 is not None:
            model["r2"] = r2
        if mae is not None:
            model["mae"] = mae
        if job_id is not None:
            model["job_id"] = str(job_id)
        if error is not None:
            model["error"] = error
        elif "error" in model and error is None:
            model["error"] = None
        AGENT_MODELS_IN_PROGRESS[agent_key] = models
        return


def cleanup_old_jobs() -> None:
    """
    Cleanup completed/failed jobs older than TTL and cap dictionary size.
    """
    now = datetime.utcnow()
    cutoff = now - timedelta(minutes=JOB_TTL_MINUTES)

    expired_job_ids = [
        job_id
        for job_id, job in JOB_STATUS.items()
        if job.get("completed_at") is not None
        and isinstance(job.get("completed_at"), datetime)
        and job["completed_at"] < cutoff
    ]

    for job_id in expired_job_ids:
        JOB_STATUS.pop(job_id, None)

    if len(JOB_STATUS) <= MAX_JOB_STATUS_SIZE:
        return

    def _job_sort_key(item: Any) -> Any:
        _, job = item
        completed_at = job.get("completed_at")
        is_active = completed_at is None
        if isinstance(completed_at, datetime):
            return (is_active, completed_at)
        return (is_active, datetime.min)

    sorted_jobs = sorted(JOB_STATUS.items(), key=_job_sort_key, reverse=True)
    keep_ids = {job_id for job_id, _ in sorted_jobs[:MAX_JOB_STATUS_SIZE]}
    for job_id in list(JOB_STATUS.keys()):
        if job_id not in keep_ids:
            JOB_STATUS.pop(job_id, None)


def validate_dataset_limits(df: pd.DataFrame) -> None:
    """
    Validate uploaded/training dataset against configured limits.

    Raises:
        HTTPException: 400 when row/column limits are exceeded
    """
    if len(df) > MAX_ROWS:
        raise HTTPException(
            status_code=400,
            detail="Dataset too large. Max 50,000 rows allowed."
        )

    if len(df.columns) > MAX_COLUMNS:
        raise HTTPException(
            status_code=400,
            detail="Too many columns. Max 200 allowed."
        )


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _build_dataset_profile(df: pd.DataFrame, target_column: str) -> Dict[str, Any]:
    feature_df = df.drop(columns=[target_column], errors="ignore")
    rows = int(len(df))
    num_features = int(feature_df.shape[1])

    numeric_feature_df = feature_df.select_dtypes(include=["number"])
    categorical_feature_df = feature_df.select_dtypes(exclude=["number"])

    total_cells = max(1, int(df.shape[0] * df.shape[1]))
    missing_value_count = int(df.isnull().sum().sum())
    missing_value_ratio = float(missing_value_count / total_cells)

    variance_stats = {
        "min": 0.0,
        "max": 0.0,
        "mean": 0.0,
        "median": 0.0,
    }
    if numeric_feature_df.shape[1] > 0:
        variances = numeric_feature_df.var(numeric_only=True).replace([np.inf, -np.inf], np.nan).dropna()
        if len(variances) > 0:
            variance_stats = {
                "min": _safe_float(variances.min(), 0.0),
                "max": _safe_float(variances.max(), 0.0),
                "mean": _safe_float(variances.mean(), 0.0),
                "median": _safe_float(variances.median(), 0.0),
            }

    target_mean = _safe_float(df[target_column].mean(), 0.0) if target_column in df else 0.0
    target_std = _safe_float(df[target_column].std(), 0.0) if target_column in df else 0.0
    target_skew = _safe_float(df[target_column].skew(), 0.0) if target_column in df else 0.0

    cardinality_by_column: Dict[str, int] = {}
    for column in categorical_feature_df.columns:
        cardinality_by_column[column] = int(categorical_feature_df[column].nunique(dropna=True))

    high_dimensional = num_features >= 80
    small_dataset = rows <= 1000
    high_categorical_ratio = (
        num_features > 0 and (categorical_feature_df.shape[1] / num_features) >= 0.4
    )

    return {
        "rows": rows,
        "num_features": num_features,
        "num_numeric_features": int(numeric_feature_df.shape[1]),
        "num_categorical_features": int(categorical_feature_df.shape[1]),
        "missing_value_ratio": missing_value_ratio,
        "missing_value_count": missing_value_count,
        "feature_variance_stats": variance_stats,
        "target_distribution": {
            "mean": target_mean,
            "std": target_std,
            "skew": target_skew,
        },
        "categorical_cardinality": cardinality_by_column,
        "heuristics": {
            "small_dataset": small_dataset,
            "high_dimensional": high_dimensional,
            "high_categorical_ratio": high_categorical_ratio,
        },
    }


def _compute_dataset_fingerprint(profile: Dict[str, Any]) -> str:
    """Compute a deterministic dataset fingerprint from profile statistics.

    The fingerprint encodes row count, feature counts, numeric/categorical
    ratios, missing-value ratio, and target variance into a stable hex string.
    Two datasets with very similar statistical profiles will produce the same
    fingerprint, enabling cross-run memory retrieval.
    """
    import hashlib

    rows = int(profile.get("rows") or 0)
    num_features = int(profile.get("num_features") or 0)
    num_numeric = int(profile.get("num_numeric_features") or 0)
    num_categorical = int(profile.get("num_categorical_features") or 0)
    missing_ratio = float(profile.get("missing_value_ratio") or 0.0)
    target_dist = profile.get("target_distribution") or {}
    target_std = float(target_dist.get("std") or 0.0)

    # Bucket values for similarity tolerance
    row_bucket = (rows // 500) * 500          # 500-row buckets
    feat_bucket = num_features                 # exact
    numeric_ratio = round(num_numeric / max(num_features, 1), 2)
    cat_ratio = round(num_categorical / max(num_features, 1), 2)
    missing_bucket = round(missing_ratio, 2)
    std_bucket = round(target_std, 1)

    fingerprint_str = (
        f"r{row_bucket}|f{feat_bucket}|nr{numeric_ratio}|cr{cat_ratio}"
        f"|mr{missing_bucket}|ts{std_bucket}"
    )
    return hashlib.sha256(fingerprint_str.encode()).hexdigest()[:32]


def _retrieve_experiment_memory(
    db: Session,
    dataset_fingerprint: str,
    dataset_profile: Dict[str, Any],
    limit: int = 5,
) -> List[Dict[str, Any]]:
    """Retrieve the top experiments from memory, ranked by similarity then RMSE.

    Similarity scoring:
    - Exact fingerprint match gets priority score 0 (best)
    - Otherwise, compute L1 distance on normalised profile features
    Returns at most `limit` records sorted by (similarity_rank, rmse_cv).
    """
    all_records = (
        db.query(ExperimentMemory)
        .filter(ExperimentMemory.rmse_cv.isnot(None))
        .order_by(ExperimentMemory.rmse_cv.asc())
        .limit(200)
        .all()
    )
    if not all_records:
        return []

    cur_rows = int(dataset_profile.get("rows") or 0)
    cur_feats = int(dataset_profile.get("num_features") or 0)
    cur_numeric = int(dataset_profile.get("num_numeric_features") or 0)
    cur_cat = int(dataset_profile.get("num_categorical_features") or 0)
    cur_missing = float(dataset_profile.get("missing_value_ratio") or 0.0)
    t_dist = dataset_profile.get("target_distribution") or {}
    cur_std = float(t_dist.get("std") or 0.0)

    scored: List[Tuple[float, float, Dict[str, Any]]] = []
    for rec in all_records:
        if rec.dataset_fingerprint == dataset_fingerprint:
            sim_score = 0.0
        else:
            d_rows = abs((rec.num_rows or 0) - cur_rows) / max(cur_rows, 1)
            d_feats = abs((rec.num_features or 0) - cur_feats) / max(cur_feats, 1)
            d_numeric = abs((rec.num_numeric_features or 0) - cur_numeric) / max(cur_numeric, 1)
            d_cat = abs((rec.num_categorical_features or 0) - cur_cat) / max(cur_cat, 1)
            d_missing = abs((rec.missing_value_ratio or 0.0) - cur_missing)
            d_std = abs((rec.target_std or 0.0) - cur_std) / max(cur_std, 1.0)
            sim_score = d_rows + d_feats + d_numeric + d_cat + d_missing + d_std

        entry = {
            "model_family": rec.model_family,
            "preprocessing_tokens": rec.preprocessing_tokens,
            "training_tokens": rec.training_tokens,
            "strategy_summary": rec.strategy_summary,
            "feature_count": rec.feature_count,
            "categorical_ratio": rec.categorical_ratio,
            "rmse_cv": rec.rmse_cv,
            "similarity_score": round(sim_score, 4),
            "fingerprint_match": rec.dataset_fingerprint == dataset_fingerprint,
        }
        scored.append((sim_score, rec.rmse_cv or 999999.0, entry))

    scored.sort(key=lambda x: (x[0], x[1]))
    return [s[2] for s in scored[:limit]]


def _format_experiment_memory_for_prompt(memories: List[Dict[str, Any]]) -> str:
    """Format retrieved experiment memory records into an LLM-readable prompt block."""
    if not memories:
        return ""

    lines = ["Previous successful experiments on similar datasets:"]
    for i, mem in enumerate(memories, 1):
        fp_tag = " [exact dataset match]" if mem.get("fingerprint_match") else ""
        lines.append(
            f"  Experiment {i}{fp_tag}: "
            f"model={mem.get('model_family')}, "
            f"rmse_cv={mem.get('rmse_cv')}, "
            f"preprocessing=[{mem.get('preprocessing_tokens', '')}], "
            f"training=[{mem.get('training_tokens', '')}], "
            f"strategy=\"{mem.get('strategy_summary', 'N/A')}\""
        )
    lines.append("")
    lines.append("Use these past results to inform your strategy. Prefer approaches that worked well before.")
    return "\n".join(lines)


def _insert_experiment_memory(
    db: Session,
    dataset_fingerprint: str,
    dataset_profile: Dict[str, Any],
    model_family: Optional[str],
    preprocessing_tokens: List[str],
    training_tokens: List[str],
    strategy_summary: Optional[str],
    rmse_cv: Optional[float],
    rmse_holdout: Optional[float],
    project_id: Any,
    agent_run_id: Any,
    iteration_number: int,
) -> None:
    """Append a completed experiment summary to persistent memory (append-only)."""
    try:
        target_dist = dataset_profile.get("target_distribution") or {}
        num_features = int(dataset_profile.get("num_features") or 0)
        num_cat = int(dataset_profile.get("num_categorical_features") or 0)
        cat_ratio = round(num_cat / max(num_features, 1), 4)

        record = ExperimentMemory(
            dataset_fingerprint=dataset_fingerprint,
            model_family=model_family,
            preprocessing_tokens=",".join(preprocessing_tokens) if preprocessing_tokens else "",
            training_tokens=",".join(training_tokens) if training_tokens else "",
            strategy_summary=(strategy_summary or "")[:500],
            feature_count=num_features,
            categorical_ratio=cat_ratio,
            rmse_cv=rmse_cv,
            rmse_holdout=rmse_holdout,
            project_id=project_id,
            agent_run_id=agent_run_id,
            iteration_number=iteration_number,
            num_rows=int(dataset_profile.get("rows") or 0),
            num_features=num_features,
            num_numeric_features=int(dataset_profile.get("num_numeric_features") or 0),
            num_categorical_features=num_cat,
            missing_value_ratio=float(dataset_profile.get("missing_value_ratio") or 0.0),
            target_std=float(target_dist.get("std") or 0.0),
        )
        db.add(record)
        db.commit()
        print(f"EXPERIMENT_MEMORY_INSERTED: model={model_family}, rmse_cv={rmse_cv}, fingerprint={dataset_fingerprint[:12]}...")
    except Exception as mem_exc:
        db.rollback()
        print(f"EXPERIMENT_MEMORY_INSERT_FAILED: {mem_exc}")


def _summarize_dataset_profile(dataset_profile: Optional[Dict[str, Any]]) -> str:
    if not isinstance(dataset_profile, dict) or not dataset_profile:
        return "Dataset profile unavailable."

    heuristics = dataset_profile.get("heuristics") or {}
    recommendations: List[str] = []
    if heuristics.get("small_dataset"):
        recommendations.append("Small dataset detected: favor simpler and regularized models.")
    if heuristics.get("high_dimensional"):
        recommendations.append("High dimensional data: prioritize feature selection and dimensionality control.")
    if heuristics.get("high_categorical_ratio"):
        recommendations.append("High categorical ratio: prioritize robust encoding strategy.")

    if not recommendations:
        recommendations.append("Balanced dataset profile: use moderate preprocessing and stable tuning.")

    variance_stats = dataset_profile.get("feature_variance_stats") or {}
    target_stats = dataset_profile.get("target_distribution") or {}

    return (
        f"rows={dataset_profile.get('rows')}, features={dataset_profile.get('num_features')}, "
        f"numeric={dataset_profile.get('num_numeric_features')}, categorical={dataset_profile.get('num_categorical_features')}, "
        f"missing_ratio={_safe_float(dataset_profile.get('missing_value_ratio'), 0.0):.4f}, "
        f"feature_variance_mean={_safe_float(variance_stats.get('mean'), 0.0):.6f}, "
        f"target_std={_safe_float(target_stats.get('std'), 0.0):.6f}, target_skew={_safe_float(target_stats.get('skew'), 0.0):.6f}. "
        + " ".join(recommendations)
    )


def _ensure_project_dataset_profile(db: Session, project: Project, dataset_path: Path) -> Dict[str, Any]:
    existing = db.query(DatasetProfile).filter(DatasetProfile.project_id == project.id).first()
    if existing and isinstance(existing.profile_json, dict) and existing.profile_json:
        return existing.profile_json

    df = pd.read_csv(dataset_path)
    profile_json = _build_dataset_profile(df, project.target_column)

    if existing is None:
        existing = DatasetProfile(
            project_id=project.id,
            profile_json=profile_json,
        )
        db.add(existing)
    else:
        existing.profile_json = profile_json
        existing.profile_version = int(existing.profile_version or 1) + 1

    db.commit()
    return profile_json


def execute_training_with_timeout(func: Callable[[], Any], timeout_seconds: int = 30) -> Any:
    """
    Execute training logic with timeout protection.

    Args:
        func: Callable containing training execution logic
        timeout_seconds: Maximum execution time in seconds

    Returns:
        Result returned by func

    Raises:
        HTTPException: 408 if timeout occurs
        Exception: Re-raises any exception from func
    """
    with ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(func)
        try:
            return future.result(timeout=timeout_seconds)
        except FuturesTimeoutError:
            future.cancel()
            raise HTTPException(status_code=408, detail="Training timed out")


@app.get("/")
def root():
    return {"message": "PyrunAI Backend Running"}


# Training request model
class TrainingRequest(BaseModel):
    file_id: str
    target_column: str


# Fine-tune request model
class FineTuneRequest(BaseModel):
    model_config = ConfigDict(protected_namespaces=())

    file_id: str
    target_column: str
    llm_provider: str
    prompt: Optional[str] = None
    model_name: Optional[str] = None


class AgentStartRequest(BaseModel):
    project_id: uuid.UUID
    max_iterations: int = Field(gt=0)
    improvement_threshold: float = Field(ge=0)
    llm_provider: Optional[str] = None
    llm_model: Optional[str] = None


class AgentStartByFileRequest(BaseModel):
    file_id: str
    target_column: Optional[str] = None
    max_iterations: int = Field(default=6, gt=0)
    improvement_threshold: float = Field(default=0.0005, ge=0)
    llm_provider: Optional[str] = None
    llm_model: Optional[str] = None


@app.get("/dashboard/overview")
async def get_dashboard_overview(db: Session = Depends(get_db)):
    """
    Return dashboard summary and project list backed by database state.
    """
    try:
        try:
            project_columns = {
                column_info.get("name")
                for column_info in inspect(db.bind).get_columns("projects")
                if column_info.get("name")
            }
        except Exception:
            project_columns = set()

        def _project_select_expr(column_name: str, alias: str, fallback_sql: str) -> str:
            if column_name in project_columns:
                return f"{column_name} AS {alias}"
            return f"{fallback_sql} AS {alias}"

        order_by_sql = "created_at DESC" if "created_at" in project_columns else "id DESC"
        project_query = text(
            "SELECT "
            + ", ".join(
                [
                    _project_select_expr("id", "project_id", "NULL"),
                    _project_select_expr("file_id", "file_id", "''"),
                    _project_select_expr("project_name", "project_name", "''"),
                    _project_select_expr("num_rows", "num_rows", "0"),
                    _project_select_expr("created_at", "created_at", "NULL"),
                    _project_select_expr("target_column", "target_column", "''"),
                ]
            )
            + f" FROM projects ORDER BY {order_by_sql}"
        )

        projects = db.execute(project_query).mappings().all()

        project_ids: List[uuid.UUID] = []
        for project in projects:
            try:
                raw_project_id = project.get("project_id")
                if raw_project_id is None:
                    continue
                project_ids.append(uuid.UUID(str(raw_project_id)))
            except Exception:
                continue

        latest_run_by_project: Dict[str, Dict[str, Any]] = {}
        if project_ids:
            try:
                runs = (
                    db.query(
                        TrainingRun.project_id,
                        TrainingRun.status,
                        TrainingRun.r2,
                        TrainingRun.agent_run_id,
                        TrainingRun.version_number,
                        TrainingRun.created_at,
                    )
                    .filter(TrainingRun.project_id.in_(project_ids))
                    .order_by(
                        TrainingRun.project_id.asc(),
                        TrainingRun.version_number.desc(),
                        TrainingRun.created_at.desc(),
                    )
                    .all()
                )

                for run in runs:
                    key = str(run.project_id)
                    if key in latest_run_by_project:
                        continue
                    latest_run_by_project[key] = {
                        "status": run.status,
                        "r2": run.r2,
                        "agent_run_id": str(run.agent_run_id) if run.agent_run_id else None,
                    }
            except Exception:
                latest_run_by_project = {}

        def _to_dashboard_status(run_status: Optional[str]) -> str:
            if not run_status:
                return "Pending"
            if run_status in ("running", "queued"):
                return "Training"
            if run_status == "completed":
                return "Completed"
            if run_status == "failed":
                return "Failed"
            return "Pending"

        def _serialize_created_at(value: Any) -> Optional[str]:
            if value is None:
                return None
            if isinstance(value, datetime):
                return value.isoformat() + "Z"

            raw = str(value)
            if not raw:
                return None

            try:
                parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
                return parsed.isoformat().replace("+00:00", "Z")
            except Exception:
                return raw

        project_rows: List[Dict[str, Any]] = []
        completed_projects = 0
        running_projects = 0

        for project in projects:
            try:
                project_id_raw = project.get("project_id")
                project_id = str(project_id_raw) if project_id_raw is not None else ""
                latest_run = latest_run_by_project.get(project_id)
                run_status = latest_run.get("status") if latest_run else None
                status = _to_dashboard_status(run_status)

                if status == "Completed":
                    completed_projects += 1
                if status == "Training":
                    running_projects += 1

                accuracy_percent: Optional[float] = None
                run_r2 = latest_run.get("r2") if latest_run else None
                if run_r2 is not None:
                    candidate_accuracy = float(run_r2) * 100.0
                    if np.isfinite(candidate_accuracy):
                        accuracy_percent = candidate_accuracy

                try:
                    num_rows = int(project.get("num_rows") or 0)
                except (TypeError, ValueError):
                    num_rows = 0

                project_rows.append(
                    {
                        "project_id": project_id,
                        "file_id": str(project.get("file_id") or ""),
                        "project_name": str(project.get("project_name") or "Untitled Project"),
                        "status": status,
                        "accuracy_percent": accuracy_percent,
                        "num_rows": num_rows,
                        "created_at": _serialize_created_at(project.get("created_at")),
                        "target_column": str(project.get("target_column") or ""),
                        "agent_run_id": latest_run.get("agent_run_id") if latest_run else None,
                    }
                )
            except Exception:
                continue

        return {
            "summary": {
                "total_projects": len(projects),
                "completed_projects": completed_projects,
                "running_projects": running_projects,
            },
            "projects": project_rows,
        }
    except Exception as exc:
        print(f"Dashboard overview fallback triggered: {exc}")
        return {
            "summary": {
                "total_projects": 0,
                "completed_projects": 0,
                "running_projects": 0,
            },
            "projects": [],
        }


@app.delete("/projects/{project_id}")
async def delete_project(project_id: str):
    """
    Delete a project and its related records.
    """
    try:
        project_uuid = uuid.UUID(project_id)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid project_id")

    db = SessionLocal()
    try:
        project = db.query(Project).filter(Project.id == project_uuid).first()
        if project is None:
            raise HTTPException(status_code=404, detail="Project not found")

        file_id = str(project.file_id)

        training_run_ids = [
            row[0]
            for row in db.query(TrainingRun.id)
            .filter(TrainingRun.project_id == project_uuid)
            .all()
        ]

        if training_run_ids:
            db.query(ModelVersion).filter(ModelVersion.training_run_id.in_(training_run_ids)).delete(
                synchronize_session=False
            )

        db.query(SandboxJob).filter(SandboxJob.project_id == project_uuid).delete(synchronize_session=False)
        db.query(TrainingRun).filter(TrainingRun.project_id == project_uuid).delete(synchronize_session=False)
        db.query(AgentRun).filter(AgentRun.project_id == project_uuid).delete(synchronize_session=False)
        db.query(DatasetProfile).filter(DatasetProfile.project_id == project_uuid).delete(synchronize_session=False)
        db.query(ExperimentMemory).filter(ExperimentMemory.project_id == project_uuid).delete(synchronize_session=False)
        db.query(Project).filter(Project.id == project_uuid).delete(synchronize_session=False)

        db.commit()

        TRAINING_HISTORY.pop(file_id, None)

        uploaded_file_path = UPLOAD_DIR / file_id
        if uploaded_file_path.exists() and uploaded_file_path.is_file():
            try:
                uploaded_file_path.unlink()
            except Exception:
                pass

        return {"message": "Project deleted", "project_id": project_id}
    except HTTPException:
        db.rollback()
        raise
    except Exception:
        db.rollback()
        raise HTTPException(status_code=500, detail="Internal Server Error")
    finally:
        db.close()


@app.get("/projects/by-file/{file_id}")
async def get_project_by_file_id(file_id: str, db: Session = Depends(get_db)):
    project = db.query(Project).filter(Project.file_id == file_id).first()
    if project is None:
        raise HTTPException(status_code=404, detail="Project not found for file_id")

    return {
        "project_id": str(project.id),
        "file_id": str(project.file_id),
        "project_name": str(project.project_name or "Untitled Project"),
        "target_column": str(project.target_column or ""),
        "num_rows": int(project.num_rows or 0),
    }


def generate_mutated_hyperparameters(previous_best: Dict[str, Any]) -> Dict[str, Any]:
    """
    Generate improved hyperparameters using LLM suggestions.
    
    Args:
        previous_best: Dictionary containing:
            - model_name: str
            - rmse: float  
            - hyperparameters: dict
    
    Returns:
        Dictionary containing:
            - model_name: str
            - hyperparameters: dict (improved or fallback to original)
    """
    try:
        model_name = previous_best.get("model_name")
        original_hyperparameters = previous_best.get("hyperparameters", {})

        if not isinstance(model_name, str) or not model_name:
            return {
                "model_name": "",
                "hyperparameters": {},
            }

        if not isinstance(original_hyperparameters, dict):
            original_hyperparameters = {}

        if model_name not in SAFE_HYPERPARAMETERS:
            return {
                "model_name": model_name,
                "hyperparameters": {},
            }

        safe_original_hyperparameters = validate_hyperparameters(
            model_name,
            original_hyperparameters,
        )

        def _to_float(value: Any, default: float = 0.0) -> float:
            try:
                return float(value)
            except (TypeError, ValueError):
                return default

        metrics = {
            "rmse": _to_float(previous_best.get("rmse"), 0.0),
            "r2": _to_float(previous_best.get("r2"), 0.0),
            "mae": _to_float(previous_best.get("mae"), 0.0),
            "improvement": _to_float(previous_best.get("improvement"), 0.0),
            "number_of_rows": previous_best.get("number_of_rows", "unknown"),
            "number_of_features": previous_best.get("number_of_features", "unknown"),
            "numeric_feature_count": previous_best.get("numeric_feature_count", "unknown"),
            "categorical_feature_count": previous_best.get("categorical_feature_count", "unknown"),
            "missing_value_count": previous_best.get("missing_value_count", "unknown"),
            "target_variance": previous_best.get("target_variance", "unknown"),
        }

        llm_provider = "gemini"
        allowed_linear_regression_mutations = {
            "Ridge",
            "Lasso",
            "ElasticNet",
            "RandomForestRegressor",
            "XGBRegressor",
        }

        llm_response = get_hyperparameter_suggestions(
            model_name=model_name,
            metrics=metrics,
            llm_provider=llm_provider,
        )
        print("LLM RESPONSE:", llm_response)
        if not isinstance(llm_response, dict):
            llm_response = {}

        selected_model_name = model_name
        suggested_model_name = llm_response.get("model_name")

        if model_name == "LinearRegression":
            if isinstance(suggested_model_name, str) and suggested_model_name:
                if suggested_model_name in allowed_linear_regression_mutations:
                    selected_model_name = suggested_model_name
                elif suggested_model_name != "LinearRegression":
                    return {
                        "model_name": model_name,
                        "hyperparameters": safe_original_hyperparameters,
                    }
        else:
            if isinstance(suggested_model_name, str) and suggested_model_name != model_name:
                if suggested_model_name not in SAFE_HYPERPARAMETERS:
                    return {
                        "model_name": model_name,
                        "hyperparameters": safe_original_hyperparameters,
                    }
                return {
                    "model_name": model_name,
                    "hyperparameters": safe_original_hyperparameters,
                }

        if selected_model_name != model_name:
            llm_response = get_hyperparameter_suggestions(
                model_name=selected_model_name,
                metrics=metrics,
                llm_provider=llm_provider,
            )
            if not isinstance(llm_response, dict):
                llm_response = {}

            second_model_name = llm_response.get("model_name")
            if isinstance(second_model_name, str) and second_model_name in SAFE_HYPERPARAMETERS:
                selected_model_name = second_model_name

        suggested_params = llm_response.get("hyperparameters", {})
        validated_params = validate_hyperparameters(selected_model_name, suggested_params)

        if selected_model_name == model_name:
            final_hyperparameters = dict(safe_original_hyperparameters)
            if validated_params:
                final_hyperparameters.update(validated_params)
            return {
                "model_name": selected_model_name,
                "hyperparameters": final_hyperparameters,
            }

        return {
            "model_name": selected_model_name,
            "hyperparameters": validated_params,
        }

    except Exception:
        rows_raw = previous_best.get("number_of_rows", 0)
        numeric_features_raw = previous_best.get("numeric_feature_count", 0)

        try:
            rows = int(float(rows_raw))
        except (TypeError, ValueError):
            rows = 0

        try:
            numeric_features = int(float(numeric_features_raw))
        except (TypeError, ValueError):
            numeric_features = 0

        print("SMART FALLBACK ACTIVATED: Using deterministic mutation strategy.")

        if rows < 300:
            if numeric_features >= 5:
                return {
                    "model_name": "Lasso",
                    "hyperparameters": {
                        "alpha": 0.1,
                        "max_iter": 5000,
                    },
                }
            return {
                "model_name": "Ridge",
                "hyperparameters": {
                    "alpha": 1.0,
                },
            }
        elif rows < 1000:
            return {
                "model_name": "ElasticNet",
                "hyperparameters": {
                    "alpha": 0.1,
                    "l1_ratio": 0.5,
                    "max_iter": 5000,
                },
            }
        else:
            return {
                "model_name": "RandomForestRegressor",
                "hyperparameters": {
                    "n_estimators": 100,
                    "max_depth": 6,
                },
            }


def run_agent_loop(
    agent_run_id: uuid.UUID,
    preferred_llm_provider: Optional[str] = None,
    preferred_llm_model: Optional[str] = None,
) -> None:
    execution_mode = os.getenv("AGENT_EXECUTION_MODE", "native").strip().lower()
    if execution_mode not in {"native", "sandbox"}:
        execution_mode = "native"

    project_id = None
    project_file_id = None
    project_target_column = None
    project_num_rows = None
    project_num_features = None
    project_num_numeric_features = None
    project_num_categorical_features = None
    project_missing_value_count = None
    project_target_variance = None
    project_dataset_profile: Dict[str, Any] = {}
    project_dataset_profile_summary = ""

    def _sanitize_code_block(raw_text: Optional[str]) -> str:
        if not raw_text:
            return ""
        cleaned = raw_text.strip()
        if cleaned.startswith("```"):
            cleaned = cleaned.strip("`").strip()
            if cleaned.lower().startswith("python"):
                cleaned = cleaned[6:].lstrip()
        return cleaned

    def _build_autonomous_script_template(
        preprocessing_section: str,
        training_section: str,
        target_column: str,
        system_model_section: str = "",
    ) -> str:
        def _indent_block(code: str, prefix: str = "    ") -> str:
            lines = (code or "").splitlines() or [""]
            return "\n".join((prefix + line) if line.strip() else prefix for line in lines)

        template = (
            "import json\n"
            "import os\n"
            "import numpy as np\n"
            "import pandas as pd\n"
            "from sklearn.model_selection import train_test_split, cross_val_score, KFold, GridSearchCV, RandomizedSearchCV, ParameterGrid\n"
            "from sklearn.feature_selection import SelectKBest, f_regression, VarianceThreshold\n"
            "from sklearn.metrics import mean_squared_error, mean_absolute_error, r2_score\n"
            "from sklearn.linear_model import LinearRegression, Ridge, Lasso, ElasticNet\n"
            "from sklearn.ensemble import RandomForestRegressor\n"
            "from xgboost import XGBRegressor\n"
            "\n"
            "df = pd.read_csv('/app/dataset.csv')\n"
            "TARGET_COLUMN = __TARGET_COLUMN__\n"
            "\n"
            "# --- AUTONOMOUS_PREPROCESSING_SECTION ---\n"
            "try:\n"
            "__AUTONOMOUS_PREPROCESSING_SECTION__\n"
            "except Exception:\n"
            "    X = df.drop(columns=[TARGET_COLUMN])\n"
            "    y = df[TARGET_COLUMN]\n"
            "    X = pd.get_dummies(X, drop_first=True)\n"
            "    X_train, X_test, y_train, y_test = train_test_split(X, y, test_size=0.2, random_state=42)\n"
            "\n"
            "# --- SYSTEM SAFETY: ensure numeric aligned train/test matrices ---\n"
            "def _ensure_numeric_matrix(frame):\n"
            "    if not hasattr(frame, 'select_dtypes'):\n"
            "        frame = pd.DataFrame(frame)\n"
            "    if hasattr(frame, 'select_dtypes'):\n"
            "        non_numeric_cols = frame.select_dtypes(exclude=['number']).columns\n"
            "        if len(non_numeric_cols) > 0:\n"
            "            frame = pd.get_dummies(frame, drop_first=True)\n"
            "    frame = frame.replace([np.inf, -np.inf], np.nan).fillna(0.0)\n"
            "    return frame\n"
            "\n"
            "X_train = _ensure_numeric_matrix(X_train)\n"
            "X_test = _ensure_numeric_matrix(X_test)\n"
            "X_train, X_test = X_train.align(X_test, join='left', axis=1, fill_value=0.0)\n"
            "\n"
            "# --- SYSTEM MODEL SECTION ---\n"
            "__SYSTEM_MODEL_SECTION__\n"
            "\n"
            "# --- AUTONOMOUS_TRAINING_SECTION ---\n"
            "try:\n"
            "__AUTONOMOUS_TRAINING_SECTION__\n"
            "except Exception:\n"
            "    if 'model' not in locals() or model is None:\n"
            "        model = Ridge(alpha=1.0)\n"
            "    model.fit(X_train, y_train)\n"
            "    predictions = model.predict(X_test)\n"
            "\n"
            "# --- SYSTEM FEATURE SELECTION ENGINE (deterministic + safe) ---\n"
            "_fs_method_raw = str(globals().get('feature_selection_method', '')).strip().lower()\n"
            "_fs_params = globals().get('feature_selection_params', {})\n"
            "if not isinstance(_fs_params, dict):\n"
            "    _fs_params = {}\n"
            "if not _fs_method_raw and _fs_params.get('method') is not None:\n"
            "    _fs_method_raw = str(_fs_params.get('method')).strip().lower()\n"
            "_fs_method_map = {\n"
            "    'selectkbest': 'select_k_best',\n"
            "    'select_k_best': 'select_k_best',\n"
            "    'kbest': 'select_k_best',\n"
            "    'variancethreshold': 'variance_filter',\n"
            "    'variance_threshold': 'variance_filter',\n"
            "    'variance_filter': 'variance_filter',\n"
            "    'tree_importance': 'tree_importance',\n"
            "    'tree_based': 'tree_importance',\n"
            "    'tree_pruning': 'tree_importance',\n"
            "}\n"
            "_fs_method = _fs_method_map.get(_fs_method_raw, '')\n"
            "if _fs_method and 'X_train' in globals() and 'X_test' in globals() and 'y_train' in globals():\n"
            "    try:\n"
            "        _X_train_df = X_train if hasattr(X_train, 'iloc') else pd.DataFrame(X_train)\n"
            "        _X_test_df = X_test if hasattr(X_test, 'iloc') else pd.DataFrame(X_test)\n"
            "        _X_train_df, _X_test_df = _X_train_df.align(_X_test_df, join='left', axis=1, fill_value=0.0)\n"
            "        _X_train_df = _X_train_df.replace([np.inf, -np.inf], np.nan).fillna(0.0)\n"
            "        _X_test_df = _X_test_df.replace([np.inf, -np.inf], np.nan).fillna(0.0)\n"
            "        _y_train_arr = np.asarray(y_train).reshape(-1)\n"
            "\n"
            "        if _fs_method == 'select_k_best' and _X_train_df.shape[1] > 1:\n"
            "            _k_raw = globals().get('feature_selection_k', _fs_params.get('k', min(20, _X_train_df.shape[1])))\n"
            "            _k = int(_k_raw) if str(_k_raw).strip() else min(20, _X_train_df.shape[1])\n"
            "            _k = max(1, min(_k, _X_train_df.shape[1]))\n"
            "            _selector = SelectKBest(score_func=f_regression, k=_k)\n"
            "            X_train = _selector.fit_transform(_X_train_df, _y_train_arr)\n"
            "            X_test = _selector.transform(_X_test_df)\n"
            "\n"
            "        elif _fs_method == 'variance_filter' and _X_train_df.shape[1] > 1:\n"
            "            _threshold_raw = globals().get('feature_selection_threshold', _fs_params.get('threshold', 0.0))\n"
            "            _threshold = float(_threshold_raw) if str(_threshold_raw).strip() else 0.0\n"
            "            _threshold = max(0.0, _threshold)\n"
            "            _selector = VarianceThreshold(threshold=_threshold)\n"
            "            X_train = _selector.fit_transform(_X_train_df)\n"
            "            X_test = _selector.transform(_X_test_df)\n"
            "\n"
            "        elif _fs_method == 'tree_importance' and _X_train_df.shape[1] > 1:\n"
            "            _default_top_k = min(50, _X_train_df.shape[1])\n"
            "            _top_k_raw = globals().get('feature_selection_top_k', _fs_params.get('top_k', _default_top_k))\n"
            "            _top_k = int(_top_k_raw) if str(_top_k_raw).strip() else _default_top_k\n"
            "            _top_k = max(1, min(_top_k, _X_train_df.shape[1]))\n"
            "            _importance_model = RandomForestRegressor(n_estimators=100, random_state=42, n_jobs=1)\n"
            "            _importance_model.fit(_X_train_df, _y_train_arr)\n"
            "            _importance = np.asarray(_importance_model.feature_importances_).reshape(-1)\n"
            "            _selected_idx = np.argsort(_importance)[-int(_top_k):]\n"
            "            _selected_idx = np.sort(_selected_idx)\n"
            "            X_train = _X_train_df.iloc[:, _selected_idx]\n"
            "            X_test = _X_test_df.iloc[:, _selected_idx]\n"
            "    except Exception:\n"
            "        pass\n"
            "\n"
            "# --- SYSTEM CV CONFIG (shared by search + evaluation) ---\n"
            "_cv_enabled_raw = str(os.getenv('PYRUN_ENABLE_CV', '1')).strip().lower()\n"
            "_cv_enabled = _cv_enabled_raw not in {'0', 'false', 'no', 'off'}\n"
            "try:\n"
            "    _cv_folds = int(str(os.getenv('PYRUN_CV_FOLDS', '5')).strip() or '5')\n"
            "except Exception:\n"
            "    _cv_folds = 5\n"
            "_cv_folds = max(2, _cv_folds)\n"
            "_cv_splitter = None\n"
            "if 'X_train' in globals() and hasattr(X_train, 'shape') and X_train.shape[0] >= _cv_folds:\n"
            "    _cv_splitter = KFold(n_splits=_cv_folds, shuffle=True, random_state=42)\n"
            "\n"
            "# --- SYSTEM HYPERPARAM SEARCH (deterministic scoring + bounded size) ---\n"
            "_search_space_type = None\n"
            "if 'param_grid' in globals() and isinstance(param_grid, dict) and len(param_grid) > 0:\n"
            "    _search_space_type = 'grid'\n"
            "elif 'param_distributions' in globals() and isinstance(param_distributions, dict) and len(param_distributions) > 0:\n"
            "    _search_space_type = 'random'\n"
            "if _search_space_type and 'model' in globals() and model is not None and _cv_splitter is not None:\n"
            "    try:\n"
            "        _search_scoring = 'neg_root_mean_squared_error'\n"
            "        if _search_space_type == 'grid':\n"
            "            _all_candidates = list(ParameterGrid(param_grid))\n"
            "            _limited_candidates = _all_candidates[:20]\n"
            "            if len(_limited_candidates) > 0:\n"
            "                _grid_list = [{k: [v] for k, v in cand.items()} for cand in _limited_candidates]\n"
            "                _search = GridSearchCV(estimator=model, param_grid=_grid_list, scoring=_search_scoring, cv=_cv_splitter, n_jobs=1)\n"
            "                _search.fit(X_train, y_train)\n"
            "                model = _search.best_estimator_\n"
            "        else:\n"
            "            _search = RandomizedSearchCV(estimator=model, param_distributions=param_distributions, n_iter=20, scoring=_search_scoring, cv=_cv_splitter, n_jobs=1, random_state=42)\n"
            "            _search.fit(X_train, y_train)\n"
            "            model = _search.best_estimator_\n"
            "    except Exception:\n"
            "        pass\n"
            "\n"
            "if 'model' in globals() and model is not None and 'X_test' in globals():\n"
            "    try:\n"
            "        predictions = model.predict(X_test)\n"
            "    except Exception:\n"
            "        pass\n"
            "\n"
            "# --- SYSTEM METRIC FOOTER (deterministic) ---\n"
            "_pred_candidates = ['predictions', 'y_pred', 'preds', 'yhat', 'y_test_pred']\n"
            "_system_predictions = next((globals()[c] for c in _pred_candidates if c in globals() and globals()[c] is not None), None)\n"
            "if _system_predictions is None:\n"
            "    raise ValueError('No prediction variable found. Expected one of: predictions, y_pred, preds, yhat, y_test_pred')\n"
            "\n"
            "_system_y_true = np.asarray(y_test).reshape(-1)\n"
            "_system_y_pred = np.asarray(_system_predictions).reshape(-1)\n"
            "if _system_y_true.shape[0] != _system_y_pred.shape[0]:\n"
            "    raise ValueError(f'Prediction length mismatch: y_test={_system_y_true.shape[0]}, pred={_system_y_pred.shape[0]}')\n"
            "rmse_holdout = float(np.sqrt(mean_squared_error(_system_y_true, _system_y_pred)))\n"
            "mae = float(mean_absolute_error(_system_y_true, _system_y_pred))\n"
            "r2 = float(r2_score(_system_y_true, _system_y_pred))\n"
            "rmse_cv = None\n"
            "if _cv_enabled and 'model' in globals() and model is not None and 'X_train' in globals() and 'y_train' in globals() and _cv_splitter is not None:\n"
            "    try:\n"
            "        _cv_y = np.asarray(y_train).reshape(-1)\n"
            "        _cv_scores = cross_val_score(model, X_train, _cv_y, scoring='neg_root_mean_squared_error', cv=_cv_splitter)\n"
            "        rmse_cv = float(-np.mean(_cv_scores))\n"
            "    except Exception:\n"
            "        rmse_cv = None\n"
            "rmse = rmse_cv if rmse_cv is not None else rmse_holdout\n"
            "result_json = {'rmse': rmse, 'rmse_holdout': rmse_holdout, 'rmse_cv': rmse_cv, 'mae': mae, 'r2': r2}\n"
            "print(json.dumps({'result_json': result_json}))\n"
        )
        return (
            template
            .replace("__TARGET_COLUMN__", repr(target_column))
            .replace("__AUTONOMOUS_PREPROCESSING_SECTION__", _indent_block((preprocessing_section or "").strip()))
            .replace("__SYSTEM_MODEL_SECTION__", (system_model_section or "").strip())
            .replace("__AUTONOMOUS_TRAINING_SECTION__", _indent_block((training_section or "").strip()))
        )

    def _fallback_training_section_with_model() -> str:
        return (
            "hyperparams = {'n_estimators': 100, 'max_depth': 6, 'random_state': 42}\n"
            "model = RandomForestRegressor(n_estimators=100, max_depth=6, random_state=42)\n"
            "model.fit(X_train, y_train)\n"
            "predictions = model.predict(X_test)\n"
        )

    def _fallback_training_section_without_model() -> str:
        return (
            "hyperparams = {}\n"
            "model.fit(X_train, y_train)\n"
            "predictions = model.predict(X_test)\n"
        )

    def _parse_sectioned_autonomous_output(raw_text: Optional[str]) -> Tuple[Optional[str], Optional[str]]:
        cleaned = _sanitize_code_block(raw_text)
        if not cleaned:
            return None, None

        marker_pre = "AUTONOMOUS_PREPROCESSING_SECTION"
        marker_train = "AUTONOMOUS_TRAINING_SECTION"

        pre_idx = cleaned.find(marker_pre)
        train_idx = cleaned.find(marker_train)

        if pre_idx != -1 and train_idx != -1:
            if pre_idx < train_idx:
                pre_block = cleaned[pre_idx + len(marker_pre):train_idx]
                train_block = cleaned[train_idx + len(marker_train):]
            else:
                train_block = cleaned[train_idx + len(marker_train):pre_idx]
                pre_block = cleaned[pre_idx + len(marker_pre):]

            pre_block = pre_block.lstrip(":\n# \t").strip()
            train_block = train_block.lstrip(":\n# \t").strip()
            return (pre_block or None, train_block or None)

        return cleaned, None

    def _normalize_training_code_for_compare(code_text: Optional[str]) -> str:
        if not code_text:
            return ""
        return " ".join(code_text.split())

    def _remove_natural_language_from_code(code: Optional[str]) -> Optional[str]:
        """Remove natural language instruction lines from LLM-generated code."""
        if not code:
            return None
        
        # Pattern to detect natural language instructions
        natural_language_pattern = re.compile(
            r"^\s*(Use|Try|Apply|Implement|Consider|Ensure|Remember|Note|Make sure|You should|You can|This will|This should|To improve|For better)\s+",
            re.IGNORECASE,
        )
        
        cleaned_lines: List[str] = []
        for line in code.splitlines():
            stripped_line = line.strip()
            # Keep empty lines
            if not stripped_line:
                cleaned_lines.append(line)
                continue
            # Keep comment lines
            if stripped_line.startswith("#"):
                cleaned_lines.append(line)
                continue
            # Skip natural language instructions
            if natural_language_pattern.match(stripped_line):
                continue
            # Keep valid Python code
            cleaned_lines.append(line)
        
        result = "\n".join(cleaned_lines).strip()
        return result if result else None

    def _sanitize_training_section(
        training_code: Optional[str],
        disallow_model_redefinition: bool,
    ) -> Optional[str]:
        if not training_code:
            return None

        cleaned = training_code.strip()
        if disallow_model_redefinition:
            training_lines: List[str] = []
            for line in cleaned.splitlines():
                if re.match(r"^\s*model\s*=", line):
                    continue
                training_lines.append(line)
            cleaned = "\n".join(training_lines).strip()

        # System owns metric footer/result_json. Strip any LLM attempts to define/override.
        protected_assignment_pattern = re.compile(
            r"^\s*(result_json|rmse|rmse_holdout|rmse_cv|mae|r2)\s*=",
            re.IGNORECASE,
        )
        protected_print_pattern = re.compile(
            r"^\s*print\s*\(",
            re.IGNORECASE,
        )
        protected_import_pattern = re.compile(
            r"^\s*(import\s+|from\s+\S+\s+import\s+)",
            re.IGNORECASE,
        )
        # Pattern to detect natural language instructions (not valid Python code)
        natural_language_pattern = re.compile(
            r"^\s*(Use|Try|Apply|Implement|Consider|Ensure|Remember|Note|Make sure|You should|You can|This will|This should)\s+",
            re.IGNORECASE,
        )
        filtered_lines: List[str] = []
        for line in cleaned.splitlines():
            # Skip protected system assignments
            if protected_assignment_pattern.match(line):
                continue
            # Skip print statements
            if protected_print_pattern.match(line):
                continue
            # Skip import statements (system handles these)
            if protected_import_pattern.match(line):
                continue
            # Skip natural language instructions that LLM might output
            stripped_line = line.strip()
            if stripped_line and not stripped_line.startswith("#"):
                # If line looks like natural language instruction, skip it
                if natural_language_pattern.match(stripped_line):
                    continue
            filtered_lines.append(line)
        cleaned = "\n".join(filtered_lines).strip()

        return cleaned or None

    def _is_training_pressure_active(
        stagnation_count: int,
        improvement_delta: Optional[float],
    ) -> bool:
        if stagnation_count >= 1:
            return True
        if improvement_delta is not None and improvement_delta < 0.01:
            return True
        return False

    def _structural_training_keywords() -> List[str]:
        return [
            "KFold",
            "GridSearchCV",
            "SelectKBest",
            "VarianceThreshold",
            "feature_selection_method",
            "select_k_best",
            "variance_filter",
            "for fold",
            "np.mean",
            "ensemble",
            "stack",
            "subsample",
        ]

    def _list_absent_structural_keywords(previous_training_code: Optional[str]) -> List[str]:
        previous_lower = (previous_training_code or "").lower()
        absent: List[str] = []
        for keyword in _structural_training_keywords():
            if keyword.lower() not in previous_lower:
                absent.append(keyword)
        return absent

    def _has_new_required_structural_keyword(
        previous_training_code: Optional[str],
        new_training_code: Optional[str],
    ) -> bool:
        new_lower = (new_training_code or "").lower()
        if not new_lower:
            return False

        absent_keywords = _list_absent_structural_keywords(previous_training_code)
        return any(keyword.lower() in new_lower for keyword in absent_keywords)

    def _should_regenerate_training_section(
        previous_training_code: Optional[str],
        new_training_code: Optional[str],
        stagnation_count: int,
        improvement_delta: Optional[float],
    ) -> Tuple[bool, str]:
        if not _is_training_pressure_active(stagnation_count, improvement_delta):
            return False, ""

        if (
            previous_training_code
            and new_training_code
            and _normalize_training_code_for_compare(new_training_code)
            == _normalize_training_code_for_compare(previous_training_code)
        ):
            return True, (
                "previous and current training logic are effectively identical. "
                "You must introduce structural change now. Minor parameter tweaks are insufficient."
            )

        if previous_training_code and not _has_new_required_structural_keyword(previous_training_code, new_training_code):
            return True, (
                "no NEW required structural training keyword was introduced from the mandated list. "
                "You must introduce at least one new structural mechanism now."
            )

        return False, ""

    def _build_training_pressure_directive(
        stagnation_count: int,
        improvement_delta: Optional[float],
        previous_training_code: Optional[str],
    ) -> str:
        directive = ""
        if previous_training_code:
            directive += (
                "\nPrevious training section (you must evolve this):\n"
                f"{previous_training_code}\n"
            )

        if _is_training_pressure_active(stagnation_count, improvement_delta):
            absent_keywords = _list_absent_structural_keywords(previous_training_code)
            absent_list_text = ", ".join(absent_keywords) if absent_keywords else "none"
            directive += (
                "\nMutation Pressure Requirements (mandatory):\n"
                f"- Previously absent structural keywords: {absent_list_text}.\n"
                "- You must introduce at least one NEW structural training mechanism from the list above. Minor parameter tweaks are forbidden.\n"
                "- Required mechanism options:\n"
                "  1) K-fold cross-validation training loop,\n"
                "  2) hyperparameter search over at least two candidate values,\n"
                "  3) feature selection or dimensionality reduction,\n"
                "  4) subsampling or bootstrap/tree-depth logic modification,\n"
                "  5) ensemble combination of two models using weighted averaging.\n"
            )

        return directive

    def _get_model_instantiation_line(model_family: str) -> str:
        """Return a placeholder comment indicating which model family to use.
        The LLM will generate the actual instantiation with hyperparameters."""
        return f"# MODEL_FAMILY: {model_family}\n# LLM: Generate the model instantiation with optimized hyperparameters"

    # ──────────────────────────────────────────────────────────────────────────
    # CONTRACT VALIDATOR - LLM → Sandbox validation layer
    # ──────────────────────────────────────────────────────────────────────────

    _FORBIDDEN_MODEL_CLASSES: List[str] = [
        "MLPRegressor",
        "MLPClassifier",
        "KerasRegressor",
        "KerasClassifier",
        "TensorFlow",
        "torch",
        "nn.Module",
        "Sequential",
        "Dense",
        "Conv1D",
        "Conv2D",
        "LSTM",
        "GRU",
        "Transformer",
    ]

    _ALLOWED_MODEL_CLASSES: List[str] = [
        "LinearRegression",
        "Ridge",
        "Lasso",
        "ElasticNet",
        "RandomForestRegressor",
        "GradientBoostingRegressor",
        "XGBRegressor",
        "LGBMRegressor",
        "DecisionTreeRegressor",
        "SVR",
        "KNeighborsRegressor",
        "AdaBoostRegressor",
        "BaggingRegressor",
        "ExtraTreesRegressor",
        "HuberRegressor",
        "RANSACRegressor",
        "TheilSenRegressor",
        "PassiveAggressiveRegressor",
        "SGDRegressor",
    ]

    @dataclass
    class ContractValidationResult:
        """Result of script contract validation."""
        is_valid: bool
        errors: List[str]
        warnings: List[str]

    def _validate_script_contract(
        script_content: str,
        training_section: str,
        is_model_injected: bool,
        exploration_mode: bool,
        exploitation_lock: bool,
    ) -> ContractValidationResult:
        """
        Validate the assembled script before sandbox submission.

        Checks:
        1. Script contains model.fit( and a prediction assignment from model.predict(
        2. No new model class inside training section when exploration_mode or exploitation_lock
        3. LLM must not define/override result_json in training section
        4. No forbidden model classes appear
        5. Training section does not redefine model = when model is system-injected
        """
        errors: List[str] = []
        warnings: List[str] = []

        # ── Check 1: Required training calls ──
        if "model.fit(" not in script_content:
            errors.append("CONTRACT_VIOLATION: script missing required 'model.fit(' call")
        prediction_assignment_pattern = re.compile(
            r"^\s*[A-Za-z_]\w*\s*=\s*model\.predict\s*\(",
            re.MULTILINE,
        )
        if not prediction_assignment_pattern.search(training_section or ""):
            errors.append("CONTRACT_VIOLATION: training section missing prediction assignment from model.predict(...)")

        # ── Check 2: No model instantiation in training section under locked modes ──
        if (exploration_mode or exploitation_lock) and training_section:
            model_instantiation_pattern = re.compile(
                r"\bmodel\s*=\s*(" + "|".join(re.escape(m) for m in _ALLOWED_MODEL_CLASSES) + r")\s*\(",
                re.IGNORECASE,
            )
            if model_instantiation_pattern.search(training_section):
                errors.append(
                    "CONTRACT_VIOLATION: training section defines new model class while in "
                    f"exploration_mode={exploration_mode} or exploitation_lock={exploitation_lock}"
                )

        # ── Check 3: LLM cannot own result_json ──
        if training_section:
            result_json_redef_pattern = re.compile(r"^\s*result_json\s*=", re.IGNORECASE | re.MULTILINE)
            if result_json_redef_pattern.search(training_section):
                errors.append("CONTRACT_VIOLATION: training section attempts to define/override protected variable 'result_json'")

        result_json_assignment_pattern = re.compile(r"^\s*result_json\s*=", re.IGNORECASE | re.MULTILINE)
        result_json_assignment_count = len(result_json_assignment_pattern.findall(script_content))
        if result_json_assignment_count != 1:
            errors.append(
                "CONTRACT_VIOLATION: script must contain exactly one system-owned 'result_json =' assignment"
            )

        # ── Check 4: Forbidden model classes ──
        for forbidden_class in _FORBIDDEN_MODEL_CLASSES:
            forbidden_pattern = re.compile(rf"\b{re.escape(forbidden_class)}\b", re.IGNORECASE)
            if forbidden_pattern.search(script_content):
                errors.append(f"CONTRACT_VIOLATION: script contains forbidden model class '{forbidden_class}'")

        # ── Check 5: Model redefinition in training section when system-injected ──
        if is_model_injected and training_section:
            model_redef_pattern = re.compile(r"^\s*model\s*=", re.MULTILINE)
            if model_redef_pattern.search(training_section):
                errors.append(
                    "CONTRACT_VIOLATION: training section redefines 'model =' while model is system-injected"
                )

        return ContractValidationResult(
            is_valid=len(errors) == 0,
            errors=errors,
            warnings=warnings,
        )

    def _safe_deterministic_training_template(is_model_injected: bool) -> str:
        """
        Return a guaranteed-safe training template as final fallback.

        This template is deterministically correct and will always pass
        contract validation. Used when LLM output fails validation twice.
        """
        if is_model_injected:
            # Model already injected by system - just fit and predict
            return (
                "# Safe deterministic training (model injected by system)\n"
                "hyperparams = {}\n"
                "model.fit(X_train, y_train)\n"
                "predictions = model.predict(X_test)\n"
            )
        else:
            # Full training with model definition included
            return (
                "# Safe deterministic training with RandomForest fallback\n"
                "hyperparams = {'n_estimators': 100, 'max_depth': 6, 'random_state': 42}\n"
                "model = RandomForestRegressor(n_estimators=100, max_depth=6, random_state=42)\n"
                "model.fit(X_train, y_train)\n"
                "predictions = model.predict(X_test)\n"
            )

    def _generate_stricter_training_directive(validation_errors: List[str]) -> str:
        """
        Build a stricter regeneration directive based on validation errors.
        """
        directive = "\n\nSTRICT CONTRACT ENFORCEMENT - Your previous output FAILED validation:\n"
        for err in validation_errors:
            directive += f"  - {err}\n"
        directive += (
            "\nYou MUST fix these issues:\n"
            "- Include exactly: model.fit(X_train, y_train)\n"
            "- Include a prediction assignment from model.predict(X_test) (variable name may vary, e.g., predictions or y_pred)\n"
            "- Do NOT redefine 'model =' if model is system-injected\n"
            "- Do NOT define or override result_json, rmse, rmse_holdout, rmse_cv, mae, or r2\n"
            "- Do NOT use neural network libraries (torch, tensorflow, keras, MLPRegressor)\n"
            "- System metric footer computes metrics deterministically; do not print JSON metrics from training section\n"
            "- Use only scikit-learn compatible regressors\n"
        )
        return directive

    def _validate_and_regenerate_script(
        script_content: str,
        training_section: str,
        preprocessing_section: str,
        target_column: str,
        system_model_section: Optional[str],
        is_model_injected: bool,
        exploration_mode: bool,
        exploitation_lock: bool,
        original_prompt: str,
    ) -> Tuple[str, str, bool]:
        """
        Validate script contract and regenerate if necessary.

        Returns:
            (final_script_content, final_training_section, used_safe_fallback)
        """
        # First validation attempt
        validation_result = _validate_script_contract(
            script_content=script_content,
            training_section=training_section,
            is_model_injected=is_model_injected,
            exploration_mode=exploration_mode,
            exploitation_lock=exploitation_lock,
        )

        if validation_result.is_valid:
            print(f"CONTRACT_VALIDATOR: script passed validation")
            return script_content, training_section, False

        print(f"CONTRACT_VALIDATOR: first validation failed - {validation_result.errors}")

        # ── First regeneration attempt with stricter directive ──
        stricter_directive = _generate_stricter_training_directive(validation_result.errors)
        stricter_prompt = original_prompt + stricter_directive

        regenerated_sections = _generate_autonomous_section_via_llm(stricter_prompt)
        regen_preprocessing, regen_training = _parse_sectioned_autonomous_output(regenerated_sections)
        
        # Remove any natural language instructions
        regen_preprocessing = _remove_natural_language_from_code(regen_preprocessing)
        regen_training = _remove_natural_language_from_code(regen_training)

        # Use original preprocessing if regeneration didn't produce valid one
        if not regen_preprocessing or "train_test_split" not in regen_preprocessing:
            regen_preprocessing = preprocessing_section

        # Sanitize training section
        regen_training = _sanitize_training_section(
            regen_training,
            disallow_model_redefinition=is_model_injected,
        )

        if not regen_training:
            regen_training = _safe_deterministic_training_template(is_model_injected)

        # Rebuild script with regenerated sections
        regen_script = _build_autonomous_script_template(
            preprocessing_section=regen_preprocessing,
            training_section=regen_training,
            target_column=target_column,
            system_model_section=system_model_section,
        )

        # Second validation attempt
        validation_result_2 = _validate_script_contract(
            script_content=regen_script,
            training_section=regen_training,
            is_model_injected=is_model_injected,
            exploration_mode=exploration_mode,
            exploitation_lock=exploitation_lock,
        )

        if validation_result_2.is_valid:
            print(f"CONTRACT_VALIDATOR: regenerated script passed validation")
            return regen_script, regen_training, False

        print(f"CONTRACT_VALIDATOR: second validation failed - {validation_result_2.errors}")
        print("CONTRACT_VALIDATOR: falling back to safe deterministic template")

        # ── Final fallback: safe deterministic template ──
        safe_training = _safe_deterministic_training_template(is_model_injected)
        safe_script = _build_autonomous_script_template(
            preprocessing_section=preprocessing_section,
            training_section=safe_training,
            target_column=target_column,
            system_model_section=system_model_section,
        )

        return safe_script, safe_training, True

    # ──────────────────────────────────────────────────────────────────────────
    # END CONTRACT VALIDATOR
    # ──────────────────────────────────────────────────────────────────────────

    def _fallback_preprocessing_section(forbidden_preprocessing: List[str]) -> str:
        """Return preprocessing-only code (no model) for exploration mode."""
        use_one_hot = "one_hot" not in forbidden_preprocessing
        if use_one_hot:
            return (
                "X = df.drop(columns=[TARGET_COLUMN])\n"
                "y = df[TARGET_COLUMN]\n"
                "X = pd.get_dummies(X, drop_first=True)\n"
                "X_train, X_test, y_train, y_test = train_test_split(X, y, test_size=0.2, random_state=42)\n"
            )
        else:
            return (
                "X = df.drop(columns=[TARGET_COLUMN]).select_dtypes(include=['number']).fillna(0.0)\n"
                "y = df[TARGET_COLUMN]\n"
                "X_train, X_test, y_train, y_test = train_test_split(X, y, test_size=0.2, random_state=42)\n"
            )

    def _build_exploration_script_template(
        preprocessing_section: str,
        target_column: str,
        model_line: str,
        training_section: Optional[str] = None,
    ) -> str:
        """Build a complete script where the model is hardwired by the orchestrator.

        Model instantiation is injected deterministically so the LLM cannot
        override the selected model family. LLM-generated preprocessing/training
        code can mutate around this fixed model object.
        """
        resolved_training = training_section or _fallback_training_section_without_model()
        return _build_autonomous_script_template(
            preprocessing_section=preprocessing_section,
            training_section=resolved_training,
            target_column=target_column,
            system_model_section=model_line,
        )

    def _build_performance_context(
        current_iteration: int,
        mutation_payload: Optional[Dict[str, Any]],
        rmse_history: List[float],
        best_rmse: Optional[float],
        last_rmse: Optional[float],
        improvement_delta: Optional[float],
        stagnation_count: int,
        previous_failure_types: List[str],
        previous_model_family: Optional[str],
        previous_strategy: Optional[str],
        model_history: List[str],
        exploration_mode: bool,
    ) -> Dict[str, Any]:
        return {
            "iteration": current_iteration,
            "rmse_history": rmse_history,
            "current_best_rmse": best_rmse,
            "last_rmse": last_rmse,
            "improvement_delta": improvement_delta,
            "stagnation_count": stagnation_count,
            "previous_failure_types": previous_failure_types,
            "previous_model_family": previous_model_family,
            "previous_strategy": previous_strategy,
            "model_history": model_history,
            "exploration_mode": exploration_mode,
            "mutation_payload": mutation_payload,
            "dataset": {
                "target_column": project_target_column,
                "num_rows": project_num_rows,
                "num_features": project_num_features,
                "num_numeric_features": project_num_numeric_features,
                "num_categorical_features": project_num_categorical_features,
                "missing_value_count": project_missing_value_count,
                "target_variance": project_target_variance,
            },
            "dataset_profile": project_dataset_profile,
            "dataset_profile_summary": project_dataset_profile_summary,
            "experiment_memory_prompt": experiment_memory_prompt,
        }

    def _call_gemini_text(
        system_prompt: str,
        user_prompt: str,
        temperature: float,
        max_tokens: int,
        model_override: Optional[str] = None,
    ) -> Optional[str]:
        gemini_api_key = (os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY") or "").strip()
        if not gemini_api_key:
            return None

        gemini_model = (
            model_override
            or preferred_llm_model
            or os.getenv("GEMINI_MODEL")
            or os.getenv("GOOGLE_MODEL")
            or os.getenv("LLM_MODEL")
            or "gemini-2.0-flash"
        )

        payload = {
            "contents": [
                {
                    "parts": [
                        {
                            "text": f"{system_prompt}\n\n{user_prompt}",
                        }
                    ]
                }
            ],
            "generationConfig": {
                "temperature": float(temperature),
                "maxOutputTokens": int(max_tokens),
            },
        }

        import urllib.error
        import urllib.request

        endpoint = (
            f"https://generativelanguage.googleapis.com/v1beta/models/{gemini_model}:generateContent"
            f"?key={gemini_api_key}"
        )
        request = urllib.request.Request(
            endpoint,
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )

        with urllib.request.urlopen(request, timeout=45) as response:
            raw_body = response.read().decode("utf-8")
        parsed = json.loads(raw_body)

        candidates = parsed.get("candidates") or []
        if not candidates:
            return None

        candidate_content = candidates[0].get("content") or {}
        parts = candidate_content.get("parts") or []
        text_parts: List[str] = []
        for part in parts:
            text_val = part.get("text")
            if isinstance(text_val, str) and text_val.strip():
                text_parts.append(text_val)

        if not text_parts:
            return None
        return "\n".join(text_parts).strip()

    def _call_autonomous_llm_text(
        system_prompt: str,
        user_prompt: str,
        temperature: float,
        max_tokens: int,
        failure_log_prefix: str,
    ) -> Optional[str]:
        """Generate text for autonomous loop with provider fallback.

        Primary provider follows per-run preference, then env. On failure (e.g., rate limits),
        it falls back to other configured providers.
        """
        llm_client, llm_model, llm_provider = _resolve_autonomous_llm_client()

        configured_providers: List[str] = []
        if llm_provider:
            configured_providers.append(llm_provider)

        for fallback_provider in ["groq", "openai", "gemini"]:
            if fallback_provider not in configured_providers:
                configured_providers.append(fallback_provider)

        for provider_name in configured_providers:
            try:
                if provider_name == "gemini":
                    gemini_model_override = llm_model if llm_provider == "gemini" else None
                    gemini_text = _call_gemini_text(
                        system_prompt=system_prompt,
                        user_prompt=user_prompt,
                        temperature=temperature,
                        max_tokens=max_tokens,
                        model_override=gemini_model_override,
                    )
                    if gemini_text:
                        return gemini_text
                    continue

                if provider_name in {"groq", "openai"}:
                    if provider_name == llm_provider and llm_client and llm_model:
                        client = llm_client
                        model = llm_model
                    else:
                        client, model, resolved_provider = _resolve_autonomous_llm_client(provider_name)
                        if resolved_provider != provider_name:
                            continue

                    if not client or not model:
                        continue

                    response = client.chat.completions.create(
                        model=model,
                        messages=[
                            {"role": "system", "content": system_prompt},
                            {"role": "user", "content": user_prompt},
                        ],
                        temperature=temperature,
                        max_tokens=max_tokens,
                    )
                    text = (response.choices[0].message.content or "").strip()
                    if text:
                        return text
            except Exception as provider_exc:
                print(f"{failure_log_prefix}: {provider_name} failed: {provider_exc}")

        return None

    def _generate_strategy_reasoning(performance_context: Dict[str, Any]) -> str:
        recent_model_history = performance_context.get("model_history")
        if not isinstance(recent_model_history, list):
            recent_model_history = []
        recent_model_history = recent_model_history[-5:]
        exploration_mode = bool(performance_context.get("exploration_mode"))

        strategy_prompt = (
            "You are optimizing RMSE in an autonomous ML research loop.\n\n"
            "Performance Summary:\n\n"
            f"Iteration: {performance_context.get('iteration')}\n\n"
            f"Best RMSE: {performance_context.get('current_best_rmse')}\n\n"
            f"Last RMSE: {performance_context.get('last_rmse')}\n\n"
            f"Improvement Delta: {performance_context.get('improvement_delta')}\n\n"
            f"Stagnation Count: {performance_context.get('stagnation_count')}\n\n"
            f"Previous Model Family: {performance_context.get('previous_model_family')}\n\n"
            f"Previous Strategy: {performance_context.get('previous_strategy')}\n\n"
            f"Model History: {str(recent_model_history)}\n\n"
            f"Dataset Metadata: {performance_context.get('dataset')}\n\n"
            f"Dataset Profile Summary: {performance_context.get('dataset_profile_summary')}\n\n"
        )

        # ── Phase E: Inject experiment memory into strategy prompt ──
        memory_block = performance_context.get("experiment_memory_prompt") or ""
        if memory_block:
            strategy_prompt += f"{memory_block}\n\n"

        strategy_prompt += (
            "Do NOT repeat previous strategy text.\n\n"
            "Return:\n"
            "One short paragraph describing the next strategy.\n"
            "No code.\n"
            "No explanation."
        )

        if exploration_mode:
            strategy_prompt += (
                "\n\nExploration Mode Active: You must switch to a completely different model family "
                "than any used in the last 3 iterations. Hyperparameter tuning of the same model is forbidden."
            )

        try:
            stagnation_count = int(performance_context.get("stagnation_count") or 0)
            reflection_temperature = 0.5 if stagnation_count >= 3 else 0.3
            strategy_text = _call_autonomous_llm_text(
                system_prompt=(
                    "You are an ML research strategist. Return one short paragraph with strategy only, no code."
                ),
                user_prompt=strategy_prompt,
                temperature=reflection_temperature,
                max_tokens=180,
                failure_log_prefix="SANDBOX STRATEGY FALLBACK",
            )
            if strategy_text:
                return strategy_text[:600]
            print("SANDBOX STRATEGY FALLBACK: no configured autonomous LLM client")
        except Exception as strategy_exc:
            print(f"SANDBOX STRATEGY FALLBACK: {strategy_exc}")

        stagnation_count = int(performance_context.get("stagnation_count") or 0)
        previous_strategy = str(performance_context.get("previous_strategy") or "").strip()
        if stagnation_count >= 2:
            fallback = (
                "Switch away from the previous model family and introduce a stronger ensemble approach with "
                "feature engineering and regularization tuning, including richer preprocessing to reduce error plateaus."
            )
            if previous_strategy and fallback.lower() == previous_strategy.lower():
                return fallback + " Also add interaction features and calibration-oriented hyperparameter search."
            return fallback

        return "Refine the current approach conservatively by tuning key hyperparameters and improving generalization while keeping preprocessing stable."

    def _extract_model_family_from_code(code_text: str) -> Optional[str]:
        if not code_text:
            return None
        candidates = {
            "XGBRegressor",
            "RandomForestRegressor",
            "LinearRegression",
            "Ridge",
            "Lasso",
            "ElasticNet",
        }

        fit_match = re.search(r"^\s*model\.fit\s*\(", code_text, flags=re.MULTILINE)
        fit_pos = fit_match.start() if fit_match else len(code_text)

        assignment_re = re.compile(
            r"^\s*model\s*=\s*([A-Za-z_][A-Za-z0-9_]*)\s*\(",
            flags=re.MULTILINE,
        )

        selected: Optional[str] = None
        for match in assignment_re.finditer(code_text):
            if match.start() <= fit_pos:
                estimator_name = match.group(1)
                if estimator_name in candidates:
                    selected = estimator_name

        if selected is not None:
            return selected
        return None

    def _contains_forbidden_model_family(code_text: str, forbidden_models: List[str]) -> bool:
        if not code_text or not forbidden_models:
            return False
        return any(model_name in code_text for model_name in forbidden_models)

    def _extract_preprocessing_tokens_from_code(code_text: str) -> List[str]:
        if not code_text:
            return []

        token_patterns = {
            "one_hot": ["one_hot", "get_dummies", "OneHotEncoder"],
            "scaling": ["StandardScaler", "MinMaxScaler", "RobustScaler"],
            "polynomial": ["PolynomialFeatures"],
            "imputation": ["SimpleImputer", "fillna("],
        }

        found: List[str] = []
        lower_code = code_text.lower()
        for token_name, patterns in token_patterns.items():
            for pattern in patterns:
                if pattern.lower() in lower_code:
                    found.append(token_name)
                    break
        return found

    def _violates_forbidden_constraints(
        code_text: str,
        forbidden_models: List[str],
        forbidden_preprocessing: List[str],
    ) -> bool:
        if not code_text:
            return False

        if _contains_forbidden_model_family(code_text, forbidden_models):
            return True

        code_lower = code_text.lower()
        for token in forbidden_preprocessing:
            if token.lower() in code_lower:
                return True

        return False

    def _fallback_exploration_autonomous_section(
        selected_model: str,
        forbidden_preprocessing: List[str],
    ) -> str:
        model_lines = {
            "LinearRegression": "model = LinearRegression()\n",
            "Ridge": "model = Ridge(alpha=1.0)\n",
            "Lasso": "model = Lasso(alpha=0.01, max_iter=5000)\n",
            "ElasticNet": "model = ElasticNet(alpha=0.01, l1_ratio=0.5, max_iter=5000)\n",
            "RandomForestRegressor": "model = RandomForestRegressor(n_estimators=120, max_depth=8, random_state=42)\n",
            "XGBRegressor": "model = XGBRegressor(random_state=42, verbosity=0, n_estimators=150, max_depth=6, learning_rate=0.05)\n",
        }

        selected_model_line = model_lines.get(selected_model, model_lines["Ridge"])

        use_one_hot = "one_hot" not in forbidden_preprocessing
        if use_one_hot:
            preprocess_block = (
                "X = df.drop(columns=[TARGET_COLUMN])\n"
                "y = df[TARGET_COLUMN]\n"
                "X = pd.get_dummies(X, drop_first=True)\n"
            )
        else:
            preprocess_block = (
                "X = df.drop(columns=[TARGET_COLUMN]).select_dtypes(include=['number']).fillna(0.0)\n"
                "y = df[TARGET_COLUMN]\n"
            )

        return (
            preprocess_block
            + "X_train, X_test, y_train, y_test = train_test_split(X, y, test_size=0.2, random_state=42)\n"
            + selected_model_line
            + "model.fit(X_train, y_train)\n"
            + "predictions = model.predict(X_test)\n"
        )

    def _select_exploration_model_family(
        recent_model_history: List[str],
        iteration_number: int,
    ) -> str:
        model_pool = [
            "LinearRegression",
            "Ridge",
            "Lasso",
            "ElasticNet",
            "RandomForestRegressor",
            "XGBRegressor",
        ]

        excluded = set(recent_model_history[-3:])
        allowed = [model_name for model_name in model_pool if model_name not in excluded]
        if not allowed:
            allowed = model_pool

        return allowed[iteration_number % len(allowed)]

    def _generate_preprocessing_mutation_via_llm(
        prev_preprocessing_code: Optional[str],
        best_rmse: Optional[float],
        last_rmse: Optional[float],
        stagnation_count: int,
        dataset_metadata: Dict[str, Any],
        dataset_profile_summary: str,
    ) -> Optional[str]:
        """Ask the LLM to mutate only the preprocessing section, not the model."""
        prompt = (
            "You are an autonomous ML researcher performing controlled preprocessing evolution.\n"
            "Your task is to improve the preprocessing logic to reduce RMSE.\n\n"
            "Constraints:\n"
            "- You are NOT allowed to change the model family.\n"
            "- Do NOT define any model. Do NOT call model.fit or model.predict.\n"
            "- Do NOT include any import statements.\n"
            "- Do NOT print anything.\n"
            "- You MUST define: X_train, X_test, y_train, y_test.\n"
            "- Dataset is already loaded as df. Target column is TARGET_COLUMN.\n"
            "- Return only Python preprocessing code. No markdown, no explanation.\n\n"
            f"Best RMSE so far: {best_rmse}\n"
            f"Last RMSE: {last_rmse}\n"
            f"Stagnation count: {stagnation_count}\n"
            f"Dataset metadata: {json.dumps(dataset_metadata, default=str)}\n\n"
            f"Dataset profile summary: {dataset_profile_summary}\n\n"
            "Previous preprocessing code (mutate or extend this):\n"
            f"{prev_preprocessing_code or '# No previous preprocessing available'}\n\n"
            "You must modify or extend the preprocessing logic to attempt improvement. "
            "You are not allowed to change the model family.\n"
            "Return only updated preprocessing code."
        )
        try:
            response_text = _call_autonomous_llm_text(
                system_prompt=(
                    "You are an ML preprocessing specialist. "
                    "Return only Python code that defines X_train, X_test, y_train, y_test. "
                    "No model definitions. No imports. No markdown."
                ),
                user_prompt=prompt,
                temperature=0.4,
                max_tokens=800,
                failure_log_prefix="PREPROCESSING MUTATION LLM FALLBACK",
            )
            if not response_text:
                return None
            return _sanitize_code_block(response_text)
        except Exception as preproc_exc:
            print(f"PREPROCESSING MUTATION LLM FALLBACK: {preproc_exc}")
            return None

    def _resolve_autonomous_llm_client(
        force_provider: Optional[str] = None,
    ) -> Tuple[Optional[Any], Optional[str], Optional[str]]:
        """Resolve chat client/model for autonomous code generation.

        Provider selection order:
        1) Per-run override from API request
        2) LLM_PROVIDER env var
        3) Default priority: Groq -> OpenAI -> Gemini
        """
        requested_provider = (force_provider or preferred_llm_provider or os.getenv("LLM_PROVIDER") or "").strip().lower()
        groq_api_key = (os.getenv("GROQ_API_KEY") or "").strip()
        openai_api_key = (os.getenv("OPENAI_API_KEY") or "").strip()
        gemini_api_key = (os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY") or "").strip()

        if not groq_api_key and not openai_api_key and not gemini_api_key:
            return None, None, None

        try:
            from openai import OpenAI
        except Exception:
            # SDK is required for groq/openai; gemini can still be used.
            if gemini_api_key:
                gemini_model = (
                    preferred_llm_model
                    or os.getenv("GEMINI_MODEL")
                    or os.getenv("GOOGLE_MODEL")
                    or "gemini-2.0-flash"
                )
                return None, gemini_model, "gemini"
            return None, None, None

        use_groq = bool(groq_api_key) and requested_provider in {"", "groq", "groak"}
        use_openai = bool(openai_api_key) and requested_provider == "openai"
        use_gemini = bool(gemini_api_key) and requested_provider == "gemini"

        if requested_provider in {"", "auto"}:
            if groq_api_key:
                use_groq = True
            elif openai_api_key:
                use_openai = True
            elif gemini_api_key:
                use_gemini = True

        if use_groq:
            groq_model = (
                preferred_llm_model
                or os.getenv("GROQ_MODEL")
                or os.getenv("GROQ_CHAT_MODEL")
                or os.getenv("LLM_MODEL")
                or "llama-3.3-70b-versatile"
            )
            client = OpenAI(
                api_key=groq_api_key,
                base_url="https://api.groq.com/openai/v1",
                timeout=45.0,
            )
            return client, groq_model, "groq"

        if use_openai:
            openai_model = (
                preferred_llm_model
                or os.getenv("OPENAI_MODEL")
                or os.getenv("LLM_MODEL")
                or "gpt-4o-mini"
            )
            client = OpenAI(api_key=openai_api_key, timeout=45.0)
            return client, openai_model, "openai"

        if use_gemini or gemini_api_key:
            gemini_model = (
                preferred_llm_model
                or os.getenv("GEMINI_MODEL")
                or os.getenv("GOOGLE_MODEL")
                or os.getenv("LLM_MODEL")
                or "gemini-2.0-flash"
            )
            return None, gemini_model, "gemini"

        return None, None, None

    def _generate_autonomous_section_via_llm(prompt_text: str) -> Optional[str]:
        try:
            script_text = _call_autonomous_llm_text(
                system_prompt="Generate only autonomous section Python code, no markdown.",
                user_prompt=prompt_text,
                temperature=0.2,
                max_tokens=1200,
                failure_log_prefix="SANDBOX SCRIPT GENERATION FALLBACK",
            )
            if not script_text:
                return None
            return _sanitize_code_block(script_text)
        except Exception as llm_script_exc:
            print(f"SANDBOX SCRIPT GENERATION FALLBACK: {llm_script_exc}")
            return None

    def _create_sandbox_job(script_content: str, iteration_number: int) -> uuid.UUID:
        """Create a sandbox job and return its ID without polling."""
        create_sandbox_job_db = SessionLocal()
        try:
            sandbox_job_timeout_seconds = int(os.getenv("SANDBOX_JOB_TIMEOUT_SECONDS", "180"))
            sandbox_job = SandboxJob(
                agent_id=agent_run_id,
                project_id=project_id,
                iteration_number=iteration_number,
                script_content=script_content,
                status="queued",
                timeout_seconds=sandbox_job_timeout_seconds,
            )
            create_sandbox_job_db.add(sandbox_job)
            create_sandbox_job_db.commit()
            create_sandbox_job_db.refresh(sandbox_job)
            return sandbox_job.id
        except Exception:
            create_sandbox_job_db.rollback()
            raise
        finally:
            create_sandbox_job_db.close()

    def _empty_sandbox_metrics() -> Dict[str, Optional[float]]:
        return {
            "rmse": None,
            "rmse_cv": None,
            "rmse_holdout": None,
            "mae": None,
            "r2": None,
        }

    def _parse_sandbox_metrics(
        sandbox_status: str,
        sandbox_result_json: Any,
        sandbox_error_log: Optional[str],
    ) -> Dict[str, Optional[float]]:
        if sandbox_status == "timeout":
            raise RuntimeError("Execution timeout")
        if sandbox_status != "completed":
            raise RuntimeError(sandbox_error_log or "Sandbox execution failed")

        parsed_result = sandbox_result_json
        if isinstance(parsed_result, str):
            try:
                parsed_result = json.loads(parsed_result)
            except json.JSONDecodeError as decode_exc:
                raise RuntimeError(f"Sandbox result_json parse error: {decode_exc}")

        if not isinstance(parsed_result, dict):
            raise RuntimeError("Sandbox result_json is not a JSON object")

        system_result_json = parsed_result.get("result_json")
        if not isinstance(system_result_json, dict):
            raise RuntimeError("Sandbox result_json missing required system-generated 'result_json' object")

        def _to_float(value: Any) -> Optional[float]:
            try:
                return float(value) if value is not None else None
            except (TypeError, ValueError):
                return None

        rmse_cv = _to_float(system_result_json.get("rmse_cv"))
        rmse_holdout = _to_float(system_result_json.get("rmse_holdout"))
        rmse_legacy = _to_float(system_result_json.get("rmse"))
        rmse = rmse_cv
        if rmse is None:
            rmse = rmse_holdout
        if rmse is None:
            rmse = rmse_legacy

        return {
            "rmse": rmse,
            "rmse_cv": rmse_cv,
            "rmse_holdout": rmse_holdout,
            "mae": _to_float(system_result_json.get("mae")),
            "r2": _to_float(system_result_json.get("r2")),
        }

    def _poll_single_sandbox_job(sandbox_job_id: uuid.UUID, timeout_seconds: Optional[int] = None) -> Dict[str, Optional[float]]:
        """Poll a single sandbox job until completion and return metrics."""
        sandbox_poll_start = time.time()
        if timeout_seconds is None:
            timeout_seconds = int(os.getenv("SANDBOX_POLL_TIMEOUT_SECONDS", "900"))
        sandbox_status = "queued"
        sandbox_result_json: Any = None
        sandbox_error_log: Optional[str] = None

        while True:
            if time.time() - sandbox_poll_start > timeout_seconds:
                raise RuntimeError(f"Sandbox polling timeout exceeded ({timeout_seconds}s)")

            poll_db = SessionLocal()
            try:
                polled_job = poll_db.query(SandboxJob).filter(SandboxJob.id == sandbox_job_id).first()
                if polled_job is None:
                    raise RuntimeError("Sandbox job not found during polling")
                sandbox_status = polled_job.status
                sandbox_result_json = polled_job.result_json
                sandbox_error_log = polled_job.error_log
            finally:
                poll_db.close()

            if sandbox_status in {"completed", "failed", "timeout"}:
                break

            time.sleep(2)

        return _parse_sandbox_metrics(sandbox_status, sandbox_result_json, sandbox_error_log)

    def _poll_sandbox_jobs_parallel(
        job_ids: List[uuid.UUID],
        model_names_by_job_id: Optional[Dict[uuid.UUID, str]] = None,
    ) -> List[Dict[str, Optional[float]]]:
        """Poll a batch of sandbox jobs while tolerating queueing on a single worker."""
        if not job_ids:
            return []

        poll_interval_seconds = 2
        default_timeout_seconds = int(os.getenv("SANDBOX_POLL_TIMEOUT_SECONDS", "900"))
        timeout_buffer_seconds = int(os.getenv("SANDBOX_POLL_BUFFER_SECONDS", "120"))
        max_timeout_seconds = int(os.getenv("SANDBOX_POLL_MAX_TIMEOUT_SECONDS", "3600"))
        all_queued_timeout_seconds = max(
            15,
            int(os.getenv("SANDBOX_ALL_QUEUED_TIMEOUT_SECONDS", "45")),
        )

        metadata_db = SessionLocal()
        try:
            job_rows = (
                metadata_db.query(SandboxJob.id, SandboxJob.timeout_seconds)
                .filter(SandboxJob.id.in_(job_ids))
                .all()
            )
        finally:
            metadata_db.close()

        timeout_by_job_id = {
            row.id: max(1, int(row.timeout_seconds or 60))
            for row in job_rows
        }
        expected_runtime_seconds = sum(timeout_by_job_id.get(job_id, 60) for job_id in job_ids)
        total_timeout = max(default_timeout_seconds, expected_runtime_seconds + timeout_buffer_seconds)
        total_timeout = min(total_timeout, max_timeout_seconds)
        inactivity_timeout = max(
            max(timeout_by_job_id.values(), default=60) + timeout_buffer_seconds,
            int(os.getenv("SANDBOX_POLL_INACTIVITY_TIMEOUT_SECONDS", "240")),
        )

        print(
            "PARALLEL EXECUTION: Polling "
            f"{len(job_ids)} jobs with batch timeout {total_timeout}s "
            f"(expected_runtime={expected_runtime_seconds}s, inactivity_timeout={inactivity_timeout}s)"
        )

        pending_job_ids = list(job_ids)
        pending_job_id_set = set(job_ids)
        results: List[Optional[Dict[str, Optional[float]]]] = [None] * len(job_ids)
        last_status_by_job_id: Dict[uuid.UUID, str] = {}
        last_observed_status_by_job_id: Dict[uuid.UUID, str] = {}
        observed_execution_start = False
        batch_poll_start = time.time()
        last_progress_at = batch_poll_start
        first_all_queued_at: Optional[float] = None

        while pending_job_ids:
            now = time.time()
            if now - batch_poll_start > total_timeout:
                for idx, job_id in enumerate(job_ids):
                    if job_id in pending_job_id_set:
                        model_name = (model_names_by_job_id or {}).get(job_id, "UnknownModel")
                        print(
                            "PARALLEL EXECUTION: Job "
                            f"{job_id} failed: Sandbox batch polling timeout exceeded ({total_timeout}s)"
                        )
                        _append_agent_sandbox_event(
                            agent_run_id,
                            f"Sandbox batch timeout: {model_name} ({job_id}) after {total_timeout}s",
                        )
                        _set_agent_model_status(
                            agent_run_id,
                            model_name,
                            "failed",
                            job_id=job_id,
                            error=f"Sandbox batch polling timeout exceeded ({total_timeout}s)",
                        )
                        results[idx] = _empty_sandbox_metrics()
                break

            if now - last_progress_at > inactivity_timeout:
                for idx, job_id in enumerate(job_ids):
                    if job_id in pending_job_id_set:
                        model_name = (model_names_by_job_id or {}).get(job_id, "UnknownModel")
                        print(
                            "PARALLEL EXECUTION: Job "
                            f"{job_id} failed: No sandbox status change observed for {inactivity_timeout}s"
                        )
                        _append_agent_sandbox_event(
                            agent_run_id,
                            f"Sandbox polling stalled: {model_name} ({job_id}) for {inactivity_timeout}s",
                        )
                        _set_agent_model_status(
                            agent_run_id,
                            model_name,
                            "failed",
                            job_id=job_id,
                            error=f"No sandbox status change observed for {inactivity_timeout}s",
                        )
                        results[idx] = _empty_sandbox_metrics()
                break

            poll_db = SessionLocal()
            try:
                polled_jobs = (
                    poll_db.query(SandboxJob)
                    .filter(SandboxJob.id.in_(pending_job_ids))
                    .all()
                )
            finally:
                poll_db.close()

            jobs_by_id = {job.id: job for job in polled_jobs}

            for idx, job_id in enumerate(job_ids):
                if job_id not in pending_job_id_set:
                    continue

                polled_job = jobs_by_id.get(job_id)
                if polled_job is None:
                    model_name = (model_names_by_job_id or {}).get(job_id, "UnknownModel")
                    print(f"PARALLEL EXECUTION: Job {job_id} failed: Sandbox job not found during polling")
                    _append_agent_sandbox_event(
                        agent_run_id,
                        f"Sandbox job missing: {model_name} ({job_id})",
                    )
                    _set_agent_model_status(
                        agent_run_id,
                        model_name,
                        "failed",
                        job_id=job_id,
                        error="Sandbox job not found during polling",
                    )
                    results[idx] = _empty_sandbox_metrics()
                    pending_job_id_set.discard(job_id)
                    continue

                current_status = polled_job.status
                last_observed_status_by_job_id[job_id] = current_status
                previous_status = last_status_by_job_id.get(job_id)
                if current_status != previous_status:
                    model_name = (model_names_by_job_id or {}).get(job_id, "UnknownModel")
                    print(
                        f"PARALLEL EXECUTION: Job {job_id} status {previous_status or 'created'} -> {current_status}"
                    )
                    if current_status == "queued":
                        _append_agent_sandbox_event(
                            agent_run_id,
                            f"Sandbox job queued: {model_name} ({job_id})",
                        )
                        _set_agent_model_status(agent_run_id, model_name, "queued", job_id=job_id)
                    elif current_status == "running":
                        observed_execution_start = True
                        _append_agent_sandbox_event(
                            agent_run_id,
                            f"Sandbox job running: {model_name} ({job_id})",
                        )
                        _set_agent_model_status(agent_run_id, model_name, "training", job_id=job_id)
                    elif current_status == "completed":
                        observed_execution_start = True
                        _append_agent_sandbox_event(
                            agent_run_id,
                            f"Sandbox job completed: {model_name} ({job_id})",
                        )
                    elif current_status == "timeout":
                        observed_execution_start = True
                        _append_agent_sandbox_event(
                            agent_run_id,
                            f"Sandbox job timed out: {model_name} ({job_id})",
                        )
                    elif current_status == "failed":
                        observed_execution_start = True
                        _append_agent_sandbox_event(
                            agent_run_id,
                            f"Sandbox job failed: {model_name} ({job_id})",
                        )
                    last_status_by_job_id[job_id] = current_status
                    last_progress_at = now

                if current_status not in {"completed", "failed", "timeout"}:
                    continue

                try:
                    metrics = _parse_sandbox_metrics(
                        current_status,
                        polled_job.result_json,
                        polled_job.error_log,
                    )
                    model_name = (model_names_by_job_id or {}).get(job_id, "UnknownModel")
                    results[idx] = metrics
                    _set_agent_model_status(
                        agent_run_id,
                        model_name,
                        "completed",
                        rmse=metrics.get("rmse"),
                        r2=metrics.get("r2"),
                        mae=metrics.get("mae"),
                        job_id=job_id,
                    )
                    if metrics.get("rmse") is not None:
                        _append_agent_sandbox_event(
                            agent_run_id,
                            f"Model metrics ready: {model_name} ({job_id}) | RMSE={float(metrics['rmse']):.4f}",
                        )
                    print(f"PARALLEL EXECUTION: Job {job_id} completed successfully")
                except Exception as exc:
                    model_name = (model_names_by_job_id or {}).get(job_id, "UnknownModel")
                    print(f"PARALLEL EXECUTION: Job {job_id} failed: {exc}")
                    _set_agent_model_status(
                        agent_run_id,
                        model_name,
                        "failed",
                        job_id=job_id,
                        error=str(exc),
                    )
                    _append_agent_sandbox_event(
                        agent_run_id,
                        f"Model metrics unavailable: {model_name} ({job_id}) | {exc}",
                    )
                    results[idx] = _empty_sandbox_metrics()

                pending_job_id_set.discard(job_id)
                last_progress_at = now

            pending_job_ids = [job_id for job_id in job_ids if job_id in pending_job_id_set]
            if pending_job_ids:
                unresolved_pending_statuses = {
                    job_id: last_observed_status_by_job_id.get(job_id, "unknown")
                    for job_id in pending_job_ids
                }
                if unresolved_pending_statuses and all(
                    status == "queued" for status in unresolved_pending_statuses.values()
                ):
                    if first_all_queued_at is None:
                        first_all_queued_at = now
                    elif now - first_all_queued_at > all_queued_timeout_seconds:
                        raise RuntimeError(
                            "Sandbox worker appears offline: all sandbox jobs remained queued and never started. "
                            "Start backend/sandbox_worker.py and retry."
                        )
                else:
                    first_all_queued_at = None

            if pending_job_ids:
                time.sleep(poll_interval_seconds)

        if not observed_execution_start:
            unresolved_statuses = {
                job_id: last_observed_status_by_job_id.get(job_id, "unknown")
                for job_id in job_ids
            }
            if unresolved_statuses and all(status == "queued" for status in unresolved_statuses.values()):
                raise RuntimeError(
                    "Sandbox worker appears offline: all sandbox jobs remained queued and never started. "
                    "Start backend/sandbox_worker.py and retry."
                )

        return [item if item is not None else _empty_sandbox_metrics() for item in results]

    def _run_sandbox_script(script_content: str, iteration_number: int) -> Dict[str, Optional[float]]:
        """Queue a sandbox job, poll until terminal state, and return parsed metric payload."""
        job_id = _create_sandbox_job(script_content, iteration_number)
        return _poll_single_sandbox_job(job_id)

    def _update_training_run_stage(training_run_id: uuid.UUID, stage: str, progress: int) -> None:
        stage_db = SessionLocal()
        try:
            run = stage_db.query(TrainingRun).filter(TrainingRun.id == training_run_id).first()
            if run is None:
                return
            run.stage = stage
            run.progress = progress
            stage_db.commit()
        except Exception:
            stage_db.rollback()
            raise
        finally:
            stage_db.close()

    initial_db = SessionLocal()
    try:
        agent_run = initial_db.query(AgentRun).filter(AgentRun.id == agent_run_id).first()
        if agent_run is None:
            return

        project = initial_db.query(Project).filter(Project.id == agent_run.project_id).first()
        if project is None:
            agent_run.status = "failed"
            agent_run.error_message = "Project not found for agent run"
            agent_run.completed_at = datetime.now(timezone.utc)
            initial_db.commit()
            return

        project_id = project.id
        project_file_id = project.file_id
        project_target_column = project.target_column
        project_num_rows = project.num_rows
        project_num_features = project.num_features
        project_num_numeric_features = project.num_numeric_features
        project_num_categorical_features = project.num_categorical_features
        project_missing_value_count = project.missing_value_count
        project_target_variance = project.target_variance

        dataset_path = UPLOAD_DIR / str(project_file_id)
        if dataset_path.exists():
            try:
                project_dataset_profile = _ensure_project_dataset_profile(initial_db, project, dataset_path)
                project_dataset_profile_summary = _summarize_dataset_profile(project_dataset_profile)
            except Exception as profile_exc:
                print(f"DATASET PROFILE FALLBACK: {profile_exc}")
                project_dataset_profile = {}
                project_dataset_profile_summary = "Dataset profile unavailable due to profiling fallback."
        else:
            project_dataset_profile = {}
            project_dataset_profile_summary = "Dataset profile unavailable because dataset file is missing."

        # ── Phase E: Compute dataset fingerprint & retrieve experiment memory ──
        project_dataset_fingerprint = ""
        experiment_memory_prompt = ""
        if project_dataset_profile:
            try:
                project_dataset_fingerprint = _compute_dataset_fingerprint(project_dataset_profile)
                print(f"DATASET_FINGERPRINT: {project_dataset_fingerprint}")
                memories = _retrieve_experiment_memory(
                    initial_db, project_dataset_fingerprint, project_dataset_profile, limit=5
                )
                if memories:
                    experiment_memory_prompt = _format_experiment_memory_for_prompt(memories)
                    print(f"EXPERIMENT_MEMORY_RETRIEVED: {len(memories)} records loaded")
                else:
                    print("EXPERIMENT_MEMORY_RETRIEVED: no past experiments found")
            except Exception as mem_exc:
                print(f"EXPERIMENT_MEMORY_RETRIEVAL_FAILED: {mem_exc}")
    finally:
        initial_db.close()

    try:
        best_rmse = None
        rmse_history: List[float] = []
        last_rmse: Optional[float] = None
        improvement_delta: Optional[float] = None
        previous_failure_types: List[str] = []
        strategy_history: List[str] = []
        model_history: List[str] = []
        preprocessing_history: List[List[str]] = []
        previous_model_family: Optional[str] = None
        model_family_changed_once = False
        previous_preprocessing_code: Optional[str] = None
        best_preprocessing_code: Optional[str] = None
        previous_training_code: Optional[str] = None
        best_training_run_id = None
        patience = 5
        exploitation_lock_counter: int = 0
        exploitation_lock_model: Optional[str] = None
        EXPLOITATION_LOCK_N: int = 2
        no_improvement_counter = 0
        refinement_attempted = False
        terminated_by_status = False
        model_families = [
            "LinearRegression",
            "Ridge",
            "Lasso",
            "ElasticNet",
            "RandomForestRegressor",
            "XGBRegressor",
        ]
        per_model_previous_preprocessing: Dict[str, Optional[str]] = {m: None for m in model_families}
        per_model_previous_training: Dict[str, Optional[str]] = {m: None for m in model_families}
        per_model_best_rmse: Dict[str, Optional[float]] = {m: None for m in model_families}

        while True:
            iteration_db = SessionLocal()
            try:
                agent_run = iteration_db.query(AgentRun).filter(AgentRun.id == agent_run_id).first()
                if agent_run is None:
                    return

                if agent_run.status != "running":
                    terminated_by_status = True
                    break

                if agent_run.current_iteration >= agent_run.max_iterations:
                    break

                agent_run.current_iteration += 1
                iteration_db.commit()

                current_iteration = agent_run.current_iteration
                current_max_iterations = agent_run.max_iterations
                current_best_training_run_id = agent_run.best_training_run_id
                improvement_threshold = float(agent_run.improvement_threshold)
            except Exception:
                iteration_db.rollback()
                raise
            finally:
                iteration_db.close()

            print("=== AGENT ITERATION ===", current_iteration)
            _append_agent_sandbox_event(
                agent_run_id,
                f"Starting iteration {current_iteration}/{current_max_iterations}",
            )

            create_training_run_db = SessionLocal()
            try:
                current_version = create_training_run_db.query(
                    func.coalesce(func.max(TrainingRun.version_number), 0)
                ).filter(TrainingRun.project_id == project_id).scalar()

                training_run = TrainingRun(
                    project_id=project_id,
                    agent_run_id=agent_run_id,
                    version_number=current_version + 1,
                    status="running",
                    stage="analyzing_dataset",
                    progress=10,
                    started_at=datetime.now(timezone.utc),
                )
                create_training_run_db.add(training_run)
                create_training_run_db.commit()
                create_training_run_db.refresh(training_run)
                training_run_id = training_run.id
                _append_agent_sandbox_event(
                    agent_run_id,
                    f"Stage: Analyzing dataset (training run v{current_version + 1})",
                )
            except Exception:
                create_training_run_db.rollback()
                raise
            finally:
                create_training_run_db.close()

            mutation_payload: Optional[Dict[str, Any]] = None
            if current_iteration > 1:
                print("MUTATION PATH")
                source_training_run_id = best_training_run_id or current_best_training_run_id
                if source_training_run_id is not None:
                    mutation_db = SessionLocal()
                    try:
                        previous_best_model = (
                            mutation_db.query(ModelVersion)
                            .filter(ModelVersion.training_run_id == source_training_run_id)
                            .order_by(ModelVersion.rank_position.asc())
                            .first()
                        )
                    finally:
                        mutation_db.close()

                    if previous_best_model is not None:
                        previous_model_name = previous_best_model.model_name
                        original_params = previous_best_model.hyperparameters
                        if not isinstance(original_params, dict):
                            original_params = {}

                        safe_original_params = {}
                        if previous_model_name in SAFE_HYPERPARAMETERS:
                            safe_original_params = validate_hyperparameters(
                                previous_model_name,
                                original_params,
                            )

                        previous_best = {
                            "model_name": previous_model_name,
                            "rmse": previous_best_model.rmse,
                            "mae": previous_best_model.mae,
                            "r2": previous_best_model.r2,
                            "hyperparameters": safe_original_params,
                            "number_of_rows": project_num_rows,
                            "number_of_features": project_num_features,
                            "numeric_feature_count": project_num_numeric_features,
                            "categorical_feature_count": project_num_categorical_features,
                            "missing_value_count": project_missing_value_count,
                            "target_variance": project_target_variance,
                        }

                        if best_rmse is not None and previous_best_model.rmse is not None:
                            try:
                                previous_best["improvement"] = float(best_rmse) - float(previous_best_model.rmse)
                            except (TypeError, ValueError):
                                previous_best["improvement"] = 0.0
                        else:
                            previous_best["improvement"] = 0.0

                        selected_model_name = previous_model_name
                        selected_params = safe_original_params
                        try:
                            mutated = generate_mutated_hyperparameters(previous_best)

                            mutated_model_name = mutated.get("model_name")
                            mutated_params = mutated.get("hyperparameters")

                            if (
                                isinstance(mutated_model_name, str)
                                and mutated_model_name in SAFE_HYPERPARAMETERS
                                and isinstance(mutated_params, dict)
                            ):
                                validated_mutated_params = validate_hyperparameters(
                                    mutated_model_name,
                                    mutated_params,
                                )

                                if mutated_model_name == previous_model_name:
                                    if validated_mutated_params:
                                        selected_params = validated_mutated_params
                                else:
                                    selected_model_name = mutated_model_name
                                    selected_params = validated_mutated_params

                                improvement = 0.0
                                if best_rmse is not None and previous_best_model.rmse is not None:
                                    try:
                                        improvement = float(best_rmse) - float(previous_best_model.rmse)
                                    except (TypeError, ValueError):
                                        improvement = 0.0

                                unchanged_params = validated_mutated_params == safe_original_params
                                empty_or_unchanged = (not validated_mutated_params) or unchanged_params

                                if not refinement_attempted:
                                    if (
                                        current_iteration > 1
                                        and improvement <= 0
                                        and mutated_model_name == previous_model_name
                                        and empty_or_unchanged
                                    ):
                                        if previous_model_name in {"LinearRegression", "Ridge"}:
                                            selected_model_name = "RandomForestRegressor"
                                        elif previous_model_name == "RandomForestRegressor":
                                            selected_model_name = "XGBRegressor"
                                        elif previous_model_name == "XGBRegressor":
                                            selected_model_name = "RandomForestRegressor"
                                        else:
                                            selected_model_name = "RandomForestRegressor"
                                        selected_params = {}
                                        print("DETERMINISTIC OVERRIDE: Forcing model switch due to stagnation.")
                        except Exception:
                            selected_model_name = previous_model_name
                            selected_params = safe_original_params

                        mutation_payload = {
                            "model_name": selected_model_name,
                            "hyperparameters": selected_params,
                        }
            else:
                print("BASELINE TRAINING PATH")

            try:
                print("MUTATION PAYLOAD:", mutation_payload)
                generated_model_family: Optional[str] = None
                current_iter_preprocessing_code: Optional[str] = None
                current_iter_training_code: Optional[str] = None
                model_line: Optional[str] = None  # Set by orchestrator when model is system-injected
                sandbox_rmse_cv: Optional[float] = None
                sandbox_rmse_holdout: Optional[float] = None
                if execution_mode == "sandbox":
                    _update_training_run_stage(training_run_id, "preprocessing", 20)
                    _append_agent_sandbox_event(
                        agent_run_id,
                        "Stage: Preprocessing - building performance context",
                    )
                    performance_context = _build_performance_context(
                        current_iteration=current_iteration,
                        mutation_payload=mutation_payload,
                        rmse_history=rmse_history,
                        best_rmse=best_rmse,
                        last_rmse=last_rmse,
                        improvement_delta=improvement_delta,
                        stagnation_count=no_improvement_counter,
                        previous_failure_types=previous_failure_types,
                        previous_model_family=previous_model_family,
                        previous_strategy=(strategy_history[-1] if strategy_history else None),
                        model_history=model_history,
                        exploration_mode=False,
                    )
                    strategy_reasoning = _generate_strategy_reasoning(performance_context)
                    print(f"SANDBOX STRATEGY: {strategy_reasoning}")
                    _append_agent_sandbox_event(
                        agent_run_id,
                        f"Strategy: {strategy_reasoning[:100]}{'...' if len(strategy_reasoning) > 100 else ''}",
                    )
                    strategy_history.append(strategy_reasoning)
                    strategy_history = strategy_history[-20:]

                    per_model_results: List[Dict[str, Any]] = []
                    best_model_payload: Optional[Dict[str, Any]] = None
                    best_model_metrics: Optional[Dict[str, Optional[float]]] = None

                    # Phase 1: Generate and validate all model scripts
                    print(f"PARALLEL EXECUTION: Generating scripts for all {len(model_families)} models...")
                    
                    # Track models being trained for UI
                    AGENT_SANDBOX_EVENT_LOGS[str(agent_run_id)] = []
                    AGENT_MODELS_IN_PROGRESS[str(agent_run_id)] = [
                        {"name": model, "status": "pending", "rmse": None, "r2": None, "mae": None, "job_id": None, "error": None}
                        for model in model_families
                    ]
                    _append_agent_sandbox_event(
                        agent_run_id,
                        f"Initialized parallel sandbox execution for {len(model_families)} models.",
                    )
                    
                    model_scripts_data = []
                    
                    for model_family in model_families:
                        previous_model_preprocessing = per_model_previous_preprocessing.get(model_family)
                        previous_model_training = per_model_previous_training.get(model_family)
                        model_specific_best = per_model_best_rmse.get(model_family)
                        model_line = _get_model_instantiation_line(model_family)

                        # Build previous iteration context for this specific model
                        previous_iteration_context = ""
                        if previous_model_preprocessing or previous_model_training:
                            previous_iteration_context = f"""
=== PREVIOUS ITERATION FOR {model_family} ===
Previous RMSE: {model_specific_best if model_specific_best else 'N/A'}

Previous Preprocessing Code:
{previous_model_preprocessing or '# No previous preprocessing'}

Previous Training Code:
{previous_model_training or '# No previous training'}

What worked: {json.dumps(performance_context.get('successful_patterns', []), default=str)}
What failed: {json.dumps(performance_context.get('failed_patterns', []), default=str)}
==========================================
"""
                        
                        model_prompt = (
                            f"You are an autonomous ML optimization expert for {model_family}.\n\n"
                            "YOUR TASK: Generate unique, model-specific code to maximize performance.\n\n"
                            "Generate TWO sections using these exact markers:\n"
                            "AUTONOMOUS_PREPROCESSING_SECTION\n"
                            "AUTONOMOUS_TRAINING_SECTION\n\n"
                            f"=== CURRENT SITUATION ===\n"
                            f"Model Family: {model_family}\n"
                            f"Iteration: {current_iteration}\n"
                            f"Your Best RMSE So Far: {model_specific_best if model_specific_best else 'Not trained yet'}\n"
                            f"Global Best RMSE (all models): {best_rmse}\n"
                            f"Last Iteration RMSE: {last_rmse}\n"
                            f"Stagnation Count: {no_improvement_counter}\n"
                            f"Strategy Needed: {strategy_reasoning}\n\n"
                            f"=== DATASET INFORMATION ===\n"
                            f"Dataset Metadata: {json.dumps(performance_context.get('dataset', {}), default=str)}\n"
                            f"Dataset Profile: {performance_context.get('dataset_profile_summary')}\n\n"
                            f"{previous_iteration_context}\n"
                            f"=== YOUR CREATIVE FREEDOM ===\n"
                            f"For {model_family}, you have FULL CONTROL to:\n"
                            f"1. Define model instantiation with ANY hyperparameters you think will work best\n"
                            f"2. Choose unique preprocessing strategies tailored to {model_family}'s strengths\n"
                            f"3. Implement model-specific feature engineering\n"
                            f"4. Use hyperparameter tuning (GridSearch/RandomSearch) if beneficial\n"
                            f"5. Apply cross-validation strategies\n"
                            f"6. Experiment with feature selection techniques\n"
                            f"7. Try ensemble methods, stacking, or blending\n"
                            f"8. Use domain knowledge about what works well for {model_family}\n\n"
                            f"REQUIREMENTS:\n"
                            f"- PREPROCESSING must define: X_train, X_test, y_train, y_test\n"
                            f"- TRAINING must include: model instantiation, model.fit(), predictions = model.predict()\n"
                            f"- Use {model_family} as the base model class (import from sklearn/xgboost)\n"
                            f"- Be CREATIVE and DIFFERENT from other models - each model should have unique optimizations\n\n"
                            f"CRITICAL OUTPUT RULES:\n"
                            f"- Output ONLY executable Python code - NO natural language explanations\n"
                            f"- NO markdown formatting (no ```, no language tags)\n"
                            f"- NO instructional text like 'Use this' or 'Try that'\n"
                            f"- NO bullet points or suggestions - only valid Python statements\n"
                            f"- Every line must be valid Python syntax (code or comments starting with #)\n"
                            f"- Comments MUST start with # symbol\n"
                            f"- Write code AS IF you're writing a .py file that will be executed directly\n\n"
                            f"MAKE YOUR CODE UNIQUE FOR {model_family} - Don't generate generic code!\n"
                        )
                        model_prompt += _build_training_pressure_directive(
                            no_improvement_counter,
                            improvement_delta,
                            previous_model_training,
                        )

                        raw_sections = _generate_autonomous_section_via_llm(model_prompt)
                        preprocessing_code, training_code = _parse_sectioned_autonomous_output(raw_sections)
                        
                        # Remove any natural language instructions from LLM output
                        preprocessing_code = _remove_natural_language_from_code(preprocessing_code)
                        training_code = _remove_natural_language_from_code(training_code)

                        if not preprocessing_code or "train_test_split" not in preprocessing_code:
                            preprocessing_code = _fallback_preprocessing_section([])

                        training_code = _sanitize_training_section(
                            training_code,
                            disallow_model_redefinition=False,  # Allow LLM to define model with hyperparameters
                        )

                        needs_regen, regen_reason = _should_regenerate_training_section(
                            previous_model_training,
                            training_code,
                            no_improvement_counter,
                            improvement_delta,
                        )
                        if needs_regen:
                            stronger_prompt = model_prompt + "\nSTRICT REGENERATION DIRECTIVE: " + regen_reason + "\n"
                            stronger_sections = _generate_autonomous_section_via_llm(stronger_prompt)
                            _, stronger_training = _parse_sectioned_autonomous_output(stronger_sections)
                            # Remove natural language from regenerated code
                            stronger_training = _remove_natural_language_from_code(stronger_training)
                            training_code = _sanitize_training_section(
                                stronger_training,
                                disallow_model_redefinition=False,  # Allow LLM to define model
                            )

                        if not training_code or "model.fit" not in training_code or "predict" not in training_code:
                            training_code = _fallback_training_section_without_model()

                        script_content = _build_exploration_script_template(
                            preprocessing_code,
                            project_target_column,
                            "",  # No model injection - LLM generates it
                            training_code,
                        )

                        validation_result = _validate_script_contract(
                            script_content=script_content,
                            training_section=training_code,
                            is_model_injected=False,  # LLM generates model in training section
                            exploration_mode=False,
                            exploitation_lock=False,
                        )
                        if not validation_result.is_valid:
                            recovery_prompt = (
                                f"Generate TWO sections only:\n"
                                f"AUTONOMOUS_PREPROCESSING_SECTION\n"
                                f"AUTONOMOUS_TRAINING_SECTION\n\n"
                                f"For {model_family}, you must:\n"
                                f"- Define model instantiation: model = {model_family}(...)\n"
                                f"- Include model.fit(X_train, y_train)\n"
                                f"- Include predictions = model.predict(X_test)\n"
                                f"- No imports/prints/markdown\n"
                            )
                            script_content, training_code, _ = _validate_and_regenerate_script(
                                script_content=script_content,
                                training_section=training_code or "",
                                preprocessing_section=preprocessing_code,
                                target_column=project_target_column,
                                system_model_section="",  # No model injection
                                is_model_injected=False,  # LLM generates model
                                exploration_mode=False,
                                exploitation_lock=False,
                                original_prompt=recovery_prompt,
                            )

                        model_scripts_data.append({
                            "model_family": model_family,
                            "script_content": script_content,
                            "preprocessing_code": preprocessing_code,
                            "training_code": training_code,
                        })
                        print(f"PARALLEL EXECUTION: Script validated for {model_family}")

                    # Phase 2: Create all sandbox jobs at once
                    _update_training_run_stage(training_run_id, "training_models", 40)
                    _append_agent_sandbox_event(
                        agent_run_id,
                        f"Stage: Training models - executing {len(model_scripts_data)} sandbox jobs",
                    )
                    print(f"PARALLEL EXECUTION: Creating {len(model_scripts_data)} sandbox jobs...")
                    job_ids = []
                    model_names_by_job_id: Dict[uuid.UUID, str] = {}
                    for idx, script_data in enumerate(model_scripts_data):
                        job_id = _create_sandbox_job(script_data["script_content"], current_iteration)
                        job_ids.append(job_id)
                        model_names_by_job_id[job_id] = script_data["model_family"]
                        print(f"PARALLEL EXECUTION: Created job {job_id} for {script_data['model_family']}")
                        _append_agent_sandbox_event(
                            agent_run_id,
                            f"Sandbox job created: {script_data['model_family']} ({job_id})",
                        )
                        
                        # Update model status to training
                        _set_agent_model_status(
                            agent_run_id,
                            script_data["model_family"],
                            "queued",
                            job_id=job_id,
                        )

                    # Phase 3: Poll all jobs in parallel
                    print(f"PARALLEL EXECUTION: Polling {len(job_ids)} jobs in parallel...")
                    metrics_payloads = _poll_sandbox_jobs_parallel(job_ids, model_names_by_job_id)
                    print(f"PARALLEL EXECUTION: All {len(metrics_payloads)} jobs reached terminal state")
                    _append_agent_sandbox_event(
                        agent_run_id,
                        f"All {len(metrics_payloads)} sandbox jobs reached terminal state.",
                    )

                    # Phase 4: Process results
                    _update_training_run_stage(training_run_id, "evaluating_models", 70)
                    _append_agent_sandbox_event(
                        agent_run_id,
                        "Stage: Evaluating models - processing results",
                    )
                    for idx, script_data in enumerate(model_scripts_data):
                        model_family = script_data["model_family"]
                        preprocessing_code = script_data["preprocessing_code"]
                        training_code = script_data["training_code"]
                        metrics_payload = metrics_payloads[idx]
                        model_rmse = metrics_payload.get("rmse")
                        
                        # Update model status to completed
                        if model_rmse is not None:
                            _set_agent_model_status(
                                agent_run_id,
                                model_family,
                                "completed",
                                rmse=model_rmse,
                                r2=metrics_payload.get("r2"),
                                mae=metrics_payload.get("mae"),
                                job_id=job_ids[idx],
                            )
                            _append_agent_sandbox_event(
                                agent_run_id,
                                f"Model {model_family} completed with RMSE: {model_rmse:.6f}",
                            )
                        else:
                            _append_agent_sandbox_event(
                                agent_run_id,
                                f"Model {model_family} finished (no RMSE returned)",
                            )

                        per_model_previous_preprocessing[model_family] = preprocessing_code
                        per_model_previous_training[model_family] = training_code

                        if model_rmse is not None:
                            prior_best_for_model = per_model_best_rmse.get(model_family)
                            if prior_best_for_model is None or model_rmse < prior_best_for_model:
                                per_model_best_rmse[model_family] = model_rmse

                        model_result_payload = {
                            "name": model_family,
                            "rmse": model_rmse,
                            "mae": metrics_payload.get("mae"),
                            "r2": metrics_payload.get("r2"),
                            "accuracy": metrics_payload.get("r2"),
                            "hyperparameters": None,
                        }
                        per_model_results.append(model_result_payload)

                        if model_rmse is not None and (
                            best_model_payload is None
                            or best_model_payload.get("rmse") is None
                            or model_rmse < float(best_model_payload.get("rmse"))
                        ):
                            best_model_payload = dict(model_result_payload)
                            best_model_metrics = metrics_payload
                            generated_model_family = model_family
                            current_iter_preprocessing_code = preprocessing_code
                            current_iter_training_code = training_code

                    if not per_model_results:
                        raise RuntimeError("No model scripts executed in sandbox iteration")

                    if best_model_payload is None:
                        sorted_results = sorted(
                            per_model_results,
                            key=lambda item: (
                                item.get("rmse") is None,
                                item.get("rmse") if item.get("rmse") is not None else float("inf"),
                            ),
                        )
                        best_model_payload = dict(sorted_results[0])
                        generated_model_family = best_model_payload.get("name")

                    if current_iter_preprocessing_code:
                        current_preprocessing_tokens = _extract_preprocessing_tokens_from_code(current_iter_preprocessing_code)
                        preprocessing_history.append(current_preprocessing_tokens)
                        preprocessing_history = preprocessing_history[-20:]

                    if best_model_metrics is not None:
                        sandbox_rmse_cv = best_model_metrics.get("rmse_cv")
                        sandbox_rmse_holdout = best_model_metrics.get("rmse_holdout")

                    training_output = {
                        "best_model": best_model_payload,
                        "all_models": per_model_results,
                    }
                    
                    # Clear models in progress for this agent after iteration completes
                    if str(agent_run_id) in AGENT_MODELS_IN_PROGRESS:
                        del AGENT_MODELS_IN_PROGRESS[str(agent_run_id)]
                else:
                    if mutation_payload is None:
                        training_output = _execute_start_training_with_progress(
                            None,
                            project_file_id,
                            project_target_column,
                        )
                    else:
                        try:
                            training_output = _execute_start_training_with_progress(
                                None,
                                project_file_id,
                                project_target_column,
                                hyperparameter_overrides=mutation_payload,
                            )
                        except TypeError:
                            training_output = _execute_start_training_with_progress(
                                None,
                                project_file_id,
                                project_target_column,
                            )

                best_model = training_output.get("best_model", {})
                current_model_family = generated_model_family or best_model.get("name")
                if isinstance(current_model_family, str) and current_model_family:
                    if previous_model_family and previous_model_family != current_model_family:
                        model_family_changed_once = True
                        print(
                            f"MODEL FAMILY CHANGED: {previous_model_family} -> {current_model_family}"
                        )
                    previous_model_family = current_model_family
                    model_history.append(current_model_family)
                    model_history = model_history[-20:]
                current_rmse = best_model.get("rmse")
                print(f"ITERATION {current_iteration} RESULT → Model: {best_model.get('name')}, RMSE: {best_model.get('rmse')}")

                all_models = training_output.get("all_models")
                if not isinstance(all_models, list):
                    all_models = training_output.get("models", [])

                normalized_models: List[Dict[str, Any]] = []
                for model_data in all_models:
                    if not isinstance(model_data, dict):
                        continue

                    model_name = model_data.get("name") or model_data.get("model_name")
                    if not model_name:
                        continue

                    rmse_value = model_data.get("rmse")
                    mae_value = model_data.get("mae")
                    r2_value = model_data.get("r2")
                    hyperparameters = model_data.get("hyperparameters")

                    try:
                        rmse_value = float(rmse_value) if rmse_value is not None else None
                    except (TypeError, ValueError):
                        rmse_value = None

                    try:
                        mae_value = float(mae_value) if mae_value is not None else None
                    except (TypeError, ValueError):
                        mae_value = None

                    try:
                        r2_value = float(r2_value) if r2_value is not None else None
                    except (TypeError, ValueError):
                        r2_value = None

                    if hyperparameters is not None and not isinstance(
                        hyperparameters,
                        (dict, list, str, int, float, bool),
                    ):
                        hyperparameters = None

                    normalized_models.append(
                        {
                            "model_name": str(model_name),
                            "rmse": rmse_value,
                            "mae": mae_value,
                            "r2": r2_value,
                            "hyperparameters": hyperparameters,
                        }
                    )

                ranked_models = sorted(
                    normalized_models,
                    key=lambda item: (
                        item.get("rmse") is None,
                        item.get("rmse") if item.get("rmse") is not None else float("inf"),
                    ),
                )

                write_success_db = SessionLocal()
                try:
                    _update_training_run_stage(training_run_id, "ranking_models", 90)
                    _append_agent_sandbox_event(
                        agent_run_id,
                        f"Stage: Ranking models - selecting best from {len(ranked_models)} candidates",
                    )
                    run_to_update = (
                        write_success_db.query(TrainingRun)
                        .filter(TrainingRun.id == training_run_id)
                        .first()
                    )
                    if run_to_update is None:
                        raise RuntimeError("Training run not found for update")

                    run_to_update.status = "completed"
                    run_to_update.stage = "completed"
                    run_to_update.progress = 100
                    run_to_update.error_message = None
                    run_to_update.best_model_name = best_model.get("name")
                    run_to_update.rmse = best_model.get("rmse")
                    run_to_update.mae = best_model.get("mae")
                    run_to_update.r2 = best_model.get("r2")
                    run_to_update.completed_at = datetime.now(timezone.utc)

                    for rank_position, model_data in enumerate(ranked_models, start=1):
                        write_success_db.add(
                            ModelVersion(
                                training_run_id=training_run_id,
                                model_name=model_data["model_name"],
                                rmse=model_data["rmse"],
                                mae=model_data["mae"],
                                r2=model_data["r2"],
                                hyperparameters=model_data["hyperparameters"],
                                rank_position=rank_position,
                            )
                        )

                    write_success_db.commit()
                except Exception:
                    write_success_db.rollback()
                    raise
                finally:
                    write_success_db.close()

                # ── Phase E: Insert experiment memory record ──
                if project_dataset_fingerprint and project_dataset_profile:
                    mem_rmse_cv = sandbox_rmse_cv if execution_mode == "sandbox" else None
                    mem_rmse_holdout = sandbox_rmse_holdout if execution_mode == "sandbox" else None
                    if mem_rmse_cv is None and mem_rmse_holdout is None and current_rmse is not None:
                        mem_rmse_holdout = current_rmse
                    mem_preprocess_tokens = _extract_preprocessing_tokens_from_code(
                        current_iter_preprocessing_code or ""
                    )
                    mem_training_tokens = _extract_preprocessing_tokens_from_code(
                        current_iter_training_code or ""
                    )
                    # Use more specific training token extraction
                    mem_training_tokens_real = []
                    if current_iter_training_code:
                        for tok_name, tok_pat in [
                            ("cv", "cross_val_score"), ("grid_search", "GridSearchCV"),
                            ("random_search", "RandomizedSearchCV"), ("early_stopping", "early_stopping"),
                            ("regularization", "alpha="), ("fit", "model.fit("),
                        ]:
                            if tok_pat in (current_iter_training_code or ""):
                                mem_training_tokens_real.append(tok_name)
                    mem_strategy = strategy_history[-1] if strategy_history else None
                    mem_db = SessionLocal()
                    try:
                        _insert_experiment_memory(
                            db=mem_db,
                            dataset_fingerprint=project_dataset_fingerprint,
                            dataset_profile=project_dataset_profile,
                            model_family=generated_model_family or previous_model_family,
                            preprocessing_tokens=mem_preprocess_tokens,
                            training_tokens=mem_training_tokens_real,
                            strategy_summary=mem_strategy,
                            rmse_cv=mem_rmse_cv,
                            rmse_holdout=mem_rmse_holdout,
                            project_id=project_id,
                            agent_run_id=agent_run_id,
                            iteration_number=current_iteration,
                        )
                    finally:
                        mem_db.close()

            except Exception as training_exc:
                lower_exc = str(training_exc).lower()
                failure_type = "training_failed"
                if "timeout" in lower_exc:
                    failure_type = "timeout"
                elif "json" in lower_exc:
                    failure_type = "invalid_json"
                elif "sandbox" in lower_exc:
                    failure_type = "sandbox_failed"

                infrastructure_failure = (
                    execution_mode == "sandbox"
                    and (
                        "sandbox worker appears offline" in lower_exc
                        or "never started" in lower_exc
                    )
                )

                previous_failure_types.append(failure_type)
                previous_failure_types = previous_failure_types[-10:]

                write_failure_db = SessionLocal()
                try:
                    failed_run = (
                        write_failure_db.query(TrainingRun)
                        .filter(TrainingRun.id == training_run_id)
                        .first()
                    )
                    if failed_run is not None:
                        failed_run.status = "failed"
                        failed_run.stage = "failed"
                        failed_run.error_message = str(training_exc)
                        failed_run.completed_at = datetime.now(timezone.utc)
                        write_failure_db.commit()
                except Exception:
                    write_failure_db.rollback()
                finally:
                    write_failure_db.close()

                _append_agent_sandbox_event(
                    agent_run_id,
                    f"Iteration {current_iteration} failed: {training_exc}",
                )
                if str(agent_run_id) in AGENT_MODELS_IN_PROGRESS:
                    for model_state in AGENT_MODELS_IN_PROGRESS[str(agent_run_id)]:
                        if model_state.get("status") not in {"completed", "failed"}:
                            model_state["status"] = "failed"
                            model_state["error"] = str(training_exc)

                if infrastructure_failure:
                    raise RuntimeError(str(training_exc))
                continue

            if current_rmse is None:
                previous_failure_types.append("no_valid_rmse")
                previous_failure_types = previous_failure_types[-10:]
                print(
                    f"ITERATION {current_iteration}: No valid RMSE produced; continuing to next iteration."
                )
                continue

            try:
                current_rmse = float(current_rmse)
            except (TypeError, ValueError):
                current_rmse = None

            if current_rmse is None:
                previous_failure_types.append("invalid_rmse")
                previous_failure_types = previous_failure_types[-10:]
                print(
                    f"ITERATION {current_iteration}: Invalid RMSE value; continuing to next iteration."
                )
                continue

            rmse_history.append(current_rmse)
            rmse_history = rmse_history[-20:]
            improvement_delta = None if last_rmse is None else (last_rmse - current_rmse)
            last_rmse = current_rmse
            _append_agent_sandbox_event(
                agent_run_id,
                f"Iteration {current_iteration} RMSE: {current_rmse:.6f}" + (
                    f" (delta: {improvement_delta:+.6f})" if improvement_delta is not None else ""
                ),
            )

            if best_rmse is None:
                best_rmse = current_rmse
                best_training_run_id = training_run_id
                _append_agent_sandbox_event(
                    agent_run_id,
                    f"New best RMSE: {current_rmse:.6f} (first iteration)",
                )
                if execution_mode == "sandbox" and current_iter_preprocessing_code:
                    previous_preprocessing_code = current_iter_preprocessing_code
                    best_preprocessing_code = current_iter_preprocessing_code
                continue

            if current_rmse >= (best_rmse - 1e-9):
                improvement = best_rmse - current_rmse
                no_improvement_counter += 1
            else:
                improvement = best_rmse - current_rmse
                no_improvement_counter = 0
                best_rmse = current_rmse
                best_training_run_id = training_run_id
                _append_agent_sandbox_event(
                    agent_run_id,
                    f"New best RMSE: {current_rmse:.6f} (improved by {improvement:.6f})",
                )
                if execution_mode == "sandbox" and current_iter_preprocessing_code:
                    best_preprocessing_code = current_iter_preprocessing_code
                # Activate exploitation lock on new best
                if execution_mode == "sandbox":
                    lock_candidate = generated_model_family or exploitation_lock_model
                    if lock_candidate:
                        exploitation_lock_counter = EXPLOITATION_LOCK_N
                        exploitation_lock_model = lock_candidate
                        print(f"EXPLOITATION LOCK ACTIVATED: model={exploitation_lock_model}, "
                              f"iterations_locked={EXPLOITATION_LOCK_N}")

            if execution_mode == "sandbox" and current_iter_preprocessing_code:
                previous_preprocessing_code = current_iter_preprocessing_code
            if execution_mode == "sandbox" and current_iter_training_code:
                previous_training_code = current_iter_training_code

            if no_improvement_counter >= patience:
                if not model_family_changed_once:
                    print(
                        "EARLY STOPPING SUPPRESSED: no model-family exploration has occurred yet."
                    )
                    continue

                if not refinement_attempted:
                    refinement_attempted = True

                    if best_training_run_id is not None:
                        refinement_db = SessionLocal()
                        try:
                            best_run_for_refinement = (
                                refinement_db.query(TrainingRun)
                                .filter(TrainingRun.id == best_training_run_id)
                                .first()
                            )
                            if best_run_for_refinement is not None:
                                print(
                                    f"REFINEMENT TARGET MODEL: {best_run_for_refinement.best_model_name}"
                                )
                        finally:
                            refinement_db.close()

                    print("REFINEMENT PHASE: Attempting fine-tuning of best model before stopping.")
                    continue

                print("EARLY STOPPING: Patience limit reached.")
                break

        finalize_db = SessionLocal()
        try:
            agent_run = finalize_db.query(AgentRun).filter(AgentRun.id == agent_run_id).first()
            if agent_run is None:
                return

            agent_run.best_training_run_id = best_training_run_id
            if not terminated_by_status:
                if best_training_run_id is None:
                    agent_run.status = "failed"
                    agent_run.error_message = "No iteration produced a valid RMSE."
                    _append_agent_sandbox_event(
                        agent_run_id,
                        "Training failed: No iteration produced a valid RMSE",
                    )
                else:
                    agent_run.status = "completed"
                    agent_run.error_message = None
                    completion_msg = (
                        f"Training completed successfully! Best RMSE: {best_rmse:.6f}"
                        if best_rmse else "Training completed successfully!"
                    )
                    _append_agent_sandbox_event(agent_run_id, completion_msg)
                agent_run.completed_at = datetime.now(timezone.utc)
            finalize_db.commit()
        except Exception:
            finalize_db.rollback()
            raise
        finally:
            finalize_db.close()

    except Exception as exc:
        failure_db = SessionLocal()
        try:
            agent_run = failure_db.query(AgentRun).filter(AgentRun.id == agent_run_id).first()
            if agent_run is not None:
                agent_run.status = "failed"
                agent_run.error_message = str(exc)
                agent_run.completed_at = datetime.now(timezone.utc)
                failure_db.commit()
        except Exception:
            failure_db.rollback()
        finally:
            failure_db.close()


def train_regression_models(file_path: Path, target_column: str) -> Dict[str, Any]:
    """
    Train multiple regression models and evaluate their performance.
    
    Args:
        file_path: Path to the CSV file
        target_column: Name of the target column
        
    Returns:
        Dictionary containing model results and best model
    """
    # Load dataset
    df = pd.read_csv(file_path)
    validate_dataset_limits(df)
    
    # Validate target column exists
    if target_column not in df.columns:
        raise ValueError(f"Target column '{target_column}' not found in dataset.")
    
    # Validate target is numeric
    if not pd.api.types.is_numeric_dtype(df[target_column]):
        raise ValueError("Target column must be numeric for regression.")
    
    # Split features and target
    X = df.drop(columns=[target_column])
    y = df[target_column]
    
    # Handle categorical columns with one-hot encoding
    X = pd.get_dummies(X, drop_first=True)
    
    # Split into train and test sets
    X_train, X_test, y_train, y_test = train_test_split(
        X, y, test_size=0.2, random_state=42
    )
    
    # Define models to train
    models = {
        "LinearRegression": LinearRegression(),
        "Ridge": Ridge(),
        "RandomForestRegressor": RandomForestRegressor(random_state=42),
        "XGBRegressor": XGBRegressor(random_state=42, verbosity=0)
    }
    
    # Train and evaluate each model
    results = []
    
    for model_name, model in models.items():
        # Train the model
        model.fit(X_train, y_train)
        
        # Make predictions
        y_pred = model.predict(X_test)
        
        # Calculate metrics
        rmse = np.sqrt(mean_squared_error(y_test, y_pred))
        mae = mean_absolute_error(y_test, y_pred)
        r2 = r2_score(y_test, y_pred)
        
        results.append({
            "name": model_name,
            "rmse": float(rmse),
            "mae": float(mae),
            "r2": float(r2)
        })
    
    # Sort by RMSE (ascending - lower is better)
    results.sort(key=lambda x: x["rmse"])
    
    # Best model is the first one after sorting
    best_model = results[0]
    
    return {
        "models": results,
        "best_model": best_model
    }


def _execute_start_training_with_progress(
    job_id: Optional[str],
    file_id: str,
    target_column: str,
    lifecycle_update: Callable[[str, int], None] = None,
    hyperparameter_overrides: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    Execute training/start logic with staged progress updates, timeout protection,
    and training-history versioning.
    """
    file_path = UPLOAD_DIR / file_id

    if lifecycle_update is not None:
        lifecycle_update("analyzing_dataset", 10)
    elif job_id is not None:
        _update_job_status(job_id, status="running", stage="analyzing_dataset", progress=10)
    _append_job_log(job_id, "Analyzing dataset")

    if not file_path.exists():
        raise HTTPException(
            status_code=404,
            detail=f"File '{file_id}' not found. Please upload a CSV file first."
        )

    df = pd.read_csv(file_path)
    validate_dataset_limits(df)

    if lifecycle_update is not None:
        lifecycle_update("preprocessing", 20)
    elif job_id is not None:
        _update_job_status(job_id, stage="preprocessing", progress=20)
    _append_job_log(job_id, "Preprocessing dataset")

    if target_column not in df.columns:
        raise ValueError(f"Target column '{target_column}' not found in dataset.")

    if not pd.api.types.is_numeric_dtype(df[target_column]):
        raise ValueError("Target column must be numeric for regression.")

    X = df.drop(columns=[target_column])
    y = df[target_column]
    X = pd.get_dummies(X, drop_first=True)
    X_train, X_test, y_train, y_test = train_test_split(
        X, y, test_size=0.2, random_state=42
    )

    def _build_model(model_name: str, params: Dict[str, Any]) -> Any:
        if model_name == "LinearRegression":
            return LinearRegression(**params)
        if model_name == "Ridge":
            return Ridge(**params)
        if model_name == "Lasso":
            return Lasso(**params)
        if model_name == "ElasticNet":
            return ElasticNet(**params)
        if model_name == "RandomForestRegressor":
            base_params = {"random_state": 42}
            base_params.update(params)
            return RandomForestRegressor(**base_params)
        if model_name == "XGBRegressor":
            base_params = {"random_state": 42, "verbosity": 0}
            base_params.update(params)
            return XGBRegressor(**base_params)
        raise ValueError(f"Unsupported model '{model_name}'")

    # Default mode: train all supported models.
    # Directed mode: train only the requested model with provided hyperparameters.
    directed_hyperparameters_by_model: Dict[str, Dict[str, Any]] = {}
    if hyperparameter_overrides is None:
        models = {
            "LinearRegression": _build_model("LinearRegression", {}),
            "Ridge": _build_model("Ridge", {}),
            "Lasso": _build_model("Lasso", {}),
            "ElasticNet": _build_model("ElasticNet", {}),
            "RandomForestRegressor": _build_model("RandomForestRegressor", {}),
            "XGBRegressor": _build_model("XGBRegressor", {}),
        }
    else:
        if not isinstance(hyperparameter_overrides, dict):
            raise ValueError("hyperparameter_overrides must be a dictionary")

        directed_model_name = hyperparameter_overrides.get("model_name")
        if not isinstance(directed_model_name, str) or not directed_model_name:
            raise ValueError("hyperparameter_overrides.model_name is required")

        raw_hyperparameters = hyperparameter_overrides.get("hyperparameters", {})
        if not isinstance(raw_hyperparameters, dict):
            raw_hyperparameters = {}

        validated_hyperparameters = validate_hyperparameters(
            directed_model_name,
            raw_hyperparameters,
        )
        directed_hyperparameters_by_model[directed_model_name] = validated_hyperparameters

        models = {
            directed_model_name: _build_model(
                directed_model_name,
                validated_hyperparameters,
            )
        }

    if lifecycle_update is not None:
        lifecycle_update("training_models", 40)
    elif job_id is not None:
        _update_job_status(job_id, stage="training_models", progress=40)
    _append_job_log(job_id, "Training candidate models")

    def _train_and_evaluate() -> List[Dict[str, Any]]:
        from sklearn.model_selection import KFold
        from sklearn.preprocessing import StandardScaler
        from sklearn.compose import ColumnTransformer
        from sklearn.pipeline import Pipeline

        metrics: List[Dict[str, Any]] = []
        numeric_columns = X.select_dtypes(include=["number"]).columns

        for model_name, model_instance in models.items():
            _append_job_log(job_id, f"Training model: {model_name}")
            kfold = KFold(n_splits=5, shuffle=True, random_state=42)

            preprocessor = ColumnTransformer(
                transformers=[
                    ("num", StandardScaler(), numeric_columns)
                ],
                remainder="passthrough"
            )
            model_pipeline = Pipeline([
                ("preprocessor", preprocessor),
                ("model", model_instance)
            ])

            rmse_list: List[float] = []
            mae_list: List[float] = []
            r2_list: List[float] = []

            for train_index, val_index in kfold.split(X, y):
                X_train_fold = X.iloc[train_index]
                X_val_fold = X.iloc[val_index]
                y_train_fold = y.iloc[train_index]
                y_val_fold = y.iloc[val_index]

                model_pipeline.fit(X_train_fold, y_train_fold)
                y_pred_fold = model_pipeline.predict(X_val_fold)

                rmse_list.append(float(np.sqrt(mean_squared_error(y_val_fold, y_pred_fold))))
                mae_list.append(float(mean_absolute_error(y_val_fold, y_pred_fold)))
                r2_list.append(float(r2_score(y_val_fold, y_pred_fold)))

            rmse = float(np.mean(rmse_list))
            mae = float(np.mean(mae_list))
            r2 = float(np.mean(r2_list))

            metrics.append({
                "name": model_name,
                "rmse": float(rmse),
                "mae": float(mae),
                "r2": float(r2),
                "hyperparameters": directed_hyperparameters_by_model.get(model_name),
            })
            _append_job_log(job_id, f"Completed model: {model_name} | RMSE={rmse:.4f}")
        return metrics

    results = execute_training_with_timeout(_train_and_evaluate)

    if lifecycle_update is not None:
        lifecycle_update("evaluating_models", 70)
    elif job_id is not None:
        _update_job_status(job_id, stage="evaluating_models", progress=70)
    _append_job_log(job_id, "Evaluating model metrics")

    # Metrics are computed in the timed execution above; this stage marks evaluation completion.
    if lifecycle_update is not None:
        lifecycle_update("ranking_models", 90)
    elif job_id is not None:
        _update_job_status(job_id, stage="ranking_models", progress=90)
    _append_job_log(job_id, "Ranking models and selecting best")

    results.sort(key=lambda x: x["rmse"])
    best_model = results[0]

    if file_id not in TRAINING_HISTORY:
        TRAINING_HISTORY[file_id] = []
        version = 1
    else:
        version = len(TRAINING_HISTORY[file_id]) + 1

    version_entry = {
        "version": version,
        "models": results,
        "best_model": best_model
    }
    TRAINING_HISTORY[file_id].append(copy.deepcopy(version_entry))

    return {
        "version": version,
        "models": results,
        "best_model": best_model
    }


@app.post("/projects/create")
async def create_project(
    project_name: str = Form(..., description="Name of the project"),
    target_column: str = Form(..., description="Target column for regression"),
    file: UploadFile = File(..., description="CSV file to upload")
):
    """
    Create a new project by uploading a CSV file.
    
    Validates that the target column exists and is numeric (regression only).
    Returns project metadata including row/column counts and column names.
    """
    try:
        # Validate file extension
        if not file.filename.endswith('.csv'):
            raise HTTPException(
                status_code=400,
                detail="Only CSV files are supported. Please upload a .csv file."
            )
        
        # Generate unique filename
        file_extension = Path(file.filename).suffix
        unique_filename = f"{uuid.uuid4()}{file_extension}"
        file_path = UPLOAD_DIR / unique_filename
        
        # Save uploaded file
        try:
            contents = await file.read()

            if len(contents) > MAX_FILE_SIZE_BYTES:
                raise HTTPException(
                    status_code=413,
                    detail="File too large. Max 20MB allowed."
                )

            with open(file_path, "wb") as f:
                f.write(contents)
        except HTTPException:
            raise
        except Exception as e:
            raise HTTPException(
                status_code=500,
                detail="Internal Server Error"
            )
        
        # Read and analyze CSV
        try:
            df = pd.read_csv(file_path)
            validate_dataset_limits(df)
            project_id = None
            
            # Validate target column exists
            if target_column not in df.columns:
                raise HTTPException(
                    status_code=400,
                    detail=f"Target column '{target_column}' not found in CSV. Available columns: {', '.join(df.columns.tolist())}"
                )
            
            # Validate target column is numeric (regression only)
            if not pd.api.types.is_numeric_dtype(df[target_column]):
                raise HTTPException(
                    status_code=400,
                    detail="Currently only regression (numeric target) is supported."
                )

            num_rows = len(df)
            num_features = df.shape[1] - 1
            numeric_features = df.drop(columns=[target_column]).select_dtypes(include=["number"])
            categorical_features = df.drop(columns=[target_column]).select_dtypes(exclude=["number"])
            num_numeric_features = numeric_features.shape[1]
            num_categorical_features = categorical_features.shape[1]
            missing_value_count = int(df.isnull().sum().sum())
            target_variance = float(df[target_column].var())
            dataset_profile = _build_dataset_profile(df, target_column)
            dataset_profile_summary = _summarize_dataset_profile(dataset_profile)
            
            db = SessionLocal()
            try:
                new_project = Project(
                    project_name=project_name,
                    file_id=unique_filename,
                    target_column=target_column,
                    num_rows=num_rows,
                    num_features=num_features,
                    num_numeric_features=num_numeric_features,
                    num_categorical_features=num_categorical_features,
                    missing_value_count=missing_value_count,
                    target_variance=target_variance,
                )
                db.add(new_project)
                db.commit()
                db.refresh(new_project)

                profile_row = DatasetProfile(
                    project_id=new_project.id,
                    profile_json=dataset_profile,
                )
                db.add(profile_row)
                db.commit()
                project_id = str(new_project.id)
            except Exception:
                db.rollback()
                if file_path.exists():
                    os.remove(file_path)
                raise HTTPException(
                    status_code=500,
                    detail="Database insert failed"
                )
            finally:
                db.close()

            response_data = {
                "project_id": project_id,
                "project_name": project_name,
                "file_id": str(unique_filename),
                "target_column": target_column,
                "rows": len(df),
                "columns": len(df.columns),
                "dataset_profile_summary": dataset_profile_summary,
            }
            print(f"[DEBUG] /projects/create response: {response_data}")
            
            return JSONResponse(
                status_code=201,
                content=response_data
            )
            
        except HTTPException:
            if file_path.exists():
                os.remove(file_path)
            raise
        except pd.errors.EmptyDataError:
            # Clean up the invalid file
            if file_path.exists():
                os.remove(file_path)
            raise HTTPException(
                status_code=400,
                detail="The uploaded CSV file is empty."
            )
        except pd.errors.ParserError as e:
            # Clean up the invalid file
            if file_path.exists():
                os.remove(file_path)
            raise HTTPException(
                status_code=400,
                detail=f"Invalid CSV format: {str(e)}"
            )
        except Exception:
            # Clean up on any other error
            if file_path.exists():
                os.remove(file_path)
            raise HTTPException(
                status_code=500,
                detail="Internal Server Error"
            )
            
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail="Internal Server Error"
        )


@app.post("/training/start")
async def start_training(request: TrainingRequest, background_tasks: BackgroundTasks):
    """
    Start training regression models on the uploaded dataset.
    
    Trains models asynchronously and returns a job ID immediately.
    """
    try:
        cleanup_old_jobs()

        # Construct file path
        file_path = UPLOAD_DIR / request.file_id
        
        # Check if file exists
        if not file_path.exists():
            raise HTTPException(
                status_code=404,
                detail=f"File '{request.file_id}' not found. Please upload a CSV file first."
            )

        job_id = str(uuid.uuid4())
        JOB_STATUS[job_id] = {
            "status": "pending",
            "stage": "pending",
            "progress": 0,
            "result": None,
            "error": None,
            "completed_at": None,
            "logs": [],
        }
        _append_job_log(job_id, "Training job queued")

        background_tasks.add_task(
            run_training_job,
            job_id,
            request.file_id,
            request.target_column
        )

        return JSONResponse(
            status_code=200,
            content={
                "job_id": job_id,
                "status": "started"
            }
        )
            
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail="Internal Server Error"
        )


@app.post("/agent/start")
async def start_agent(request: AgentStartRequest, background_tasks: BackgroundTasks):
    agent_run = None
    try:
        _ensure_sandbox_worker_running()
    except Exception as exc:
        raise HTTPException(status_code=503, detail=str(exc))

    db = SessionLocal()
    try:
        project = db.query(Project).filter(Project.id == str(request.project_id)).first()
        if project is None:
            raise HTTPException(status_code=404, detail="Project not found")

        agent_run = AgentRun(
            project_id=request.project_id,
            status="running",
            current_iteration=0,
            max_iterations=request.max_iterations,
            improvement_threshold=request.improvement_threshold,
            started_at=datetime.now(timezone.utc),
        )
        db.add(agent_run)
        db.commit()
        db.refresh(agent_run)
    except HTTPException:
        db.rollback()
        raise
    except Exception:
        db.rollback()
        raise HTTPException(status_code=500, detail="Internal Server Error")
    finally:
        db.close()

    background_tasks.add_task(
        run_agent_loop,
        agent_run.id,
        request.llm_provider,
        request.llm_model,
    )

    return JSONResponse(
        status_code=201,
        content={"agent_run_id": str(agent_run.id)},
    )


@app.post("/agent/start-by-file")
async def start_agent_by_file(request: AgentStartByFileRequest, background_tasks: BackgroundTasks):
    """
    Start autonomous agent loop using file_id (frontend-friendly bridge).
    """
    agent_run = None
    try:
        _ensure_sandbox_worker_running()
    except Exception as exc:
        raise HTTPException(status_code=503, detail=str(exc))

    db = SessionLocal()
    try:
        project = db.query(Project).filter(Project.file_id == request.file_id).first()
        if project is None:
            raise HTTPException(status_code=404, detail="Project not found for file_id")

        if request.target_column and project.target_column != request.target_column:
            project.target_column = request.target_column
            db.commit()

        agent_run = AgentRun(
            project_id=project.id,
            status="running",
            current_iteration=0,
            max_iterations=request.max_iterations,
            improvement_threshold=request.improvement_threshold,
            started_at=datetime.now(timezone.utc),
        )
        db.add(agent_run)
        db.commit()
        db.refresh(agent_run)
    except HTTPException:
        db.rollback()
        raise
    except Exception:
        db.rollback()
        raise HTTPException(status_code=500, detail="Internal Server Error")
    finally:
        db.close()

    background_tasks.add_task(
        run_agent_loop,
        agent_run.id,
        request.llm_provider,
        request.llm_model,
    )

    return JSONResponse(
        status_code=201,
        content={
            "job_id": str(agent_run.id),
            "agent_run_id": str(agent_run.id),
            "status": "started",
        },
    )


@app.get("/agent/status/{agent_id}")
def get_agent_status(agent_id: uuid.UUID, db: Session = Depends(get_db)):
    agent_run = db.query(AgentRun).filter(AgentRun.id == agent_id).first()
    if agent_run is None:
        raise HTTPException(status_code=404, detail="Agent not found")

    best_model_name = None
    best_rmse = None
    stage = "pending"
    progress = 0
    logs: List[str] = []
    error_message = agent_run.error_message

    if agent_run.best_training_run_id is not None:
        best_training_run = (
            db.query(TrainingRun)
            .filter(TrainingRun.id == agent_run.best_training_run_id)
            .first()
        )
        if best_training_run is not None:
            best_model_name = best_training_run.best_model_name
            best_rmse = best_training_run.rmse

    latest_run = (
        db.query(TrainingRun)
        .filter(TrainingRun.agent_run_id == agent_run.id)
        .order_by(TrainingRun.version_number.desc(), TrainingRun.created_at.desc())
        .first()
    )
    if latest_run is not None:
        stage = latest_run.stage or stage
        progress = int(latest_run.progress or 0)
        if latest_run.error_message and not error_message:
            error_message = latest_run.error_message

    logs.append(f"Agent iteration: {agent_run.current_iteration}/{agent_run.max_iterations}")
    if stage:
        logs.append(f"Current stage: {stage}")
    if best_model_name:
        logs.append(f"Best model so far: {best_model_name}")
    if best_rmse is not None:
        logs.append(f"Best RMSE: {best_rmse:.4f}")
    sandbox_event_logs = AGENT_SANDBOX_EVENT_LOGS.get(str(agent_id), [])
    if sandbox_event_logs:
        logs.extend(sandbox_event_logs)
    
    # Get models currently being trained in parallel
    models_in_progress = AGENT_MODELS_IN_PROGRESS.get(str(agent_id), [])

    if progress == 0:
        if agent_run.max_iterations == 0:
            progress = 0
        else:
            progress = int(round((agent_run.current_iteration / agent_run.max_iterations) * 100))

    if agent_run.max_iterations == 0:
        progress_percent = 0.0
    else:
        progress_percent = round(
            (agent_run.current_iteration / agent_run.max_iterations) * 100,
            2,
        )

    if agent_run.status == "completed":
        progress_percent = 100.0
        progress = 100
        stage = "completed"

    status_value = "running"
    if agent_run.status == "completed":
        status_value = "completed"
    elif agent_run.status in {"failed", "stopped"}:
        status_value = "failed"

    return {
        "agent_id": str(agent_run.id),
        "status": status_value,
        "stage": stage,
        "progress": progress,
        "error": error_message,
        "logs": logs,
        "result": {
            "best_model_name": best_model_name,
            "best_rmse": best_rmse,
        },
        "completed_at": agent_run.completed_at.isoformat() if agent_run.completed_at else None,
        "current_iteration": agent_run.current_iteration,
        "max_iterations": agent_run.max_iterations,
        "best_model_name": best_model_name,
        "best_rmse": best_rmse,
        "progress_percent": progress_percent,
        "models_in_progress": models_in_progress,
    }


@app.post("/agent/stop/{agent_id}")
def stop_agent(agent_id: uuid.UUID, db: Session = Depends(get_db)):
    agent_run = db.query(AgentRun).filter(AgentRun.id == agent_id).first()
    if agent_run is None:
        raise HTTPException(status_code=404, detail="Agent not found")

    if agent_run.status == "completed":
        return {
            "agent_id": str(agent_run.id),
            "new_status": "completed",
            "message": "Agent already completed",
        }

    if agent_run.status == "failed":
        return {
            "agent_id": str(agent_run.id),
            "new_status": "failed",
            "message": "Agent already failed",
        }

    if agent_run.status == "stopped":
        return {
            "agent_id": str(agent_run.id),
            "new_status": "stopped",
            "message": "Agent already stopped",
        }

    if agent_run.status == "running":
        agent_run.status = "stopped"
        agent_run.completed_at = datetime.now(timezone.utc)
        db.commit()

        return {
            "agent_id": str(agent_run.id),
            "new_status": "stopped",
            "message": "Agent execution stopped successfully",
        }

    return {
        "agent_id": str(agent_run.id),
        "new_status": agent_run.status,
        "message": f"Agent is in '{agent_run.status}' state",
    }


@app.get("/agent/history/{agent_id}")
def get_agent_history(agent_id: uuid.UUID, db: Session = Depends(get_db)):
    """
    Fetch iteration history for an agent.
    Returns all training runs for the agent's project, ordered by version_number.
    """
    agent_run = db.query(AgentRun).filter(AgentRun.id == agent_id).first()
    if agent_run is None:
        raise HTTPException(status_code=404, detail="Agent not found")

    training_runs = (
        db.query(TrainingRun)
        .filter(TrainingRun.project_id == agent_run.project_id)
        .order_by(TrainingRun.version_number.asc())
        .all()
    )

    iterations = []
    for train_run in training_runs:
        iterations.append({
            "iteration": train_run.version_number,
            "training_run_id": str(train_run.id),
            "model_name": train_run.best_model_name,
            "rmse": train_run.rmse,
            "mae": train_run.mae,
            "r2": train_run.r2,
            "started_at": train_run.started_at.isoformat() if train_run.started_at else None,
            "completed_at": train_run.completed_at.isoformat() if train_run.completed_at else None,
        })

    return {
        "agent_id": str(agent_run.id),
        "iterations": iterations,
    }


def run_training_job(job_id: Optional[str], file_id: str, target_column: str) -> None:
    """
    Background job runner for training/start endpoint.
    """
    training_run_id = None
    lifecycle_db = None

    def _update_run_lifecycle(
        stage: str = None,
        progress: int = None,
        status: str = None,
        error_message: str = None,
        completed: bool = False,
    ) -> None:
        if training_run_id is None or lifecycle_db is None:
            return
        try:
            _update_job_status(
                job_id,
                status=status,
                stage=stage,
                progress=progress,
                error=error_message,
                completed_at=datetime.utcnow() if completed else None,
            )

            run = lifecycle_db.query(TrainingRun).filter(TrainingRun.id == training_run_id).first()
            if run is not None:
                if status is not None:
                    run.status = status
                if stage is not None:
                    run.stage = stage
                if progress is not None:
                    run.progress = progress
                if error_message is not None:
                    run.error_message = error_message
                if completed:
                    run.completed_at = datetime.utcnow()
                lifecycle_db.commit()
        except Exception:
            lifecycle_db.rollback()
    try:
        db = SessionLocal()
        try:
            project = db.query(Project).filter(Project.file_id == file_id).first()
            if project is None:
                return

            current_version = db.query(
                func.coalesce(func.max(TrainingRun.version_number), 0)
            ).filter(TrainingRun.project_id == project.id).scalar()

            training_run = TrainingRun(
                project_id=project.id,
                version_number=current_version + 1,
                status="running",
                stage="analyzing_dataset",
                progress=10,
                started_at=datetime.utcnow(),
            )
            db.add(training_run)
            db.commit()
            db.refresh(training_run)
            training_run_id = training_run.id
            lifecycle_db = SessionLocal()
        except Exception:
            db.rollback()
            return
        finally:
            db.close()
    except Exception:
        return

    _append_job_log(job_id, f"Training job started for file: {file_id}")

    try:
        training_output = _execute_start_training_with_progress(
            job_id,
            file_id,
            target_column,
            lifecycle_update=lambda stage, progress: _update_run_lifecycle(
                stage=stage,
                progress=progress,
                status="running",
            ),
        )
        safe_result = copy.deepcopy(training_output)
        _update_job_status(
            job_id,
            status="completed",
            stage="completed",
            progress=100,
            result=safe_result,
            error=None,
            completed_at=datetime.utcnow(),
        )
        _append_job_log(job_id, "Training completed successfully")

        if training_run_id is not None:
            db = SessionLocal()
            try:
                run = db.query(TrainingRun).filter(TrainingRun.id == training_run_id).first()
                if run is not None:
                    best_model = safe_result.get("best_model", {})
                    run.status = "completed"
                    run.stage = "completed"
                    run.progress = 100
                    run.error_message = None
                    run.best_model_name = best_model.get("name")
                    run.rmse = best_model.get("rmse")
                    run.mae = best_model.get("mae")
                    run.r2 = best_model.get("r2")
                    run.completed_at = datetime.utcnow()

                    all_models = safe_result.get("all_models")
                    if not isinstance(all_models, list):
                        all_models = safe_result.get("models", [])

                    normalized_models: List[Dict[str, Any]] = []
                    for model_data in all_models:
                        if not isinstance(model_data, dict):
                            continue

                        model_name = model_data.get("name") or model_data.get("model_name")
                        if not model_name:
                            continue

                        rmse_value = model_data.get("rmse")
                        mae_value = model_data.get("mae")
                        r2_value = model_data.get("r2")
                        hyperparameters = model_data.get("hyperparameters")

                        try:
                            rmse_value = float(rmse_value) if rmse_value is not None else None
                        except (TypeError, ValueError):
                            rmse_value = None

                        try:
                            mae_value = float(mae_value) if mae_value is not None else None
                        except (TypeError, ValueError):
                            mae_value = None

                        try:
                            r2_value = float(r2_value) if r2_value is not None else None
                        except (TypeError, ValueError):
                            r2_value = None

                        if hyperparameters is not None and not isinstance(
                            hyperparameters,
                            (dict, list, str, int, float, bool),
                        ):
                            hyperparameters = None

                        normalized_models.append(
                            {
                                "model_name": str(model_name),
                                "rmse": rmse_value,
                                "mae": mae_value,
                                "r2": r2_value,
                                "hyperparameters": hyperparameters,
                            }
                        )

                    ranked_models = sorted(
                        normalized_models,
                        key=lambda item: (
                            item.get("rmse") is None,
                            item.get("rmse") if item.get("rmse") is not None else float("inf"),
                        ),
                    )

                    for rank_position, model_data in enumerate(ranked_models, start=1):
                        db.add(
                            ModelVersion(
                                training_run_id=training_run_id,
                                model_name=model_data["model_name"],
                                rmse=model_data["rmse"],
                                mae=model_data["mae"],
                                r2=model_data["r2"],
                                hyperparameters=model_data["hyperparameters"],
                                rank_position=rank_position,
                            )
                        )

                    db.commit()
            except Exception:
                db.rollback()
            finally:
                db.close()
    except HTTPException as e:
        error_message = e.detail if isinstance(e.detail, str) else str(e.detail)
        _update_run_lifecycle(
            stage="failed",
            status="failed",
            error_message=error_message,
            completed=True,
        )
        _append_job_log(job_id, f"Training failed: {error_message}")
    except Exception as e:
        _update_run_lifecycle(
            stage="failed",
            status="failed",
            error_message=str(e),
            completed=True,
        )
        _append_job_log(job_id, f"Training failed: {str(e)}")
    finally:
        if lifecycle_db is not None:
            lifecycle_db.close()


@app.get("/training/status/{job_id}")
async def get_training_job_status(job_id: str):
    """
    Get asynchronous training job status/result.
    """
    cleanup_old_jobs()

    if job_id not in JOB_STATUS:
        raise HTTPException(
            status_code=404,
            detail=f"Job '{job_id}' not found."
        )

    job = copy.deepcopy(JOB_STATUS[job_id])
    completed_at = job.get("completed_at")
    if isinstance(completed_at, datetime):
        job["completed_at"] = completed_at.isoformat() + "Z"

    return JSONResponse(
        status_code=200,
        content={
            "status": job.get("status"),
            "stage": job.get("stage"),
            "progress": job.get("progress"),
            "result": job.get("result"),
            "error": job.get("error"),
            "completed_at": job.get("completed_at"),
            "logs": job.get("logs", []),
        }
    )


@app.get("/training/history/{file_id}")
async def get_training_history(file_id: str):
    """
    Get training history for a specific file.
    
    Returns all training versions for the given file_id.
    """
    try:
        db = SessionLocal()
        version_to_training_run_id: Dict[int, str] = {}
        version_to_generation_source: Dict[int, str] = {}
        version_to_run_duration_seconds: Dict[int, float] = {}
        training_runs: List[TrainingRun] = []
        target_column: Optional[str] = None
        try:
            project = db.query(Project).filter(Project.file_id == file_id).first()
            if project is not None:
                target_column = project.target_column
                training_runs = (
                    db.query(TrainingRun)
                    .filter(TrainingRun.project_id == project.id)
                    .order_by(TrainingRun.version_number.asc())
                    .all()
                )
                for run in training_runs:
                    version_to_training_run_id[int(run.version_number)] = str(run.id)
                    if run.started_at is not None and run.completed_at is not None:
                        duration_seconds = (run.completed_at - run.started_at).total_seconds()
                        version_to_run_duration_seconds[int(run.version_number)] = max(0.0, float(duration_seconds))
                    else:
                        version_to_run_duration_seconds[int(run.version_number)] = 0.0
                    if run.agent_run_id is not None:
                        version_to_generation_source[int(run.version_number)] = "llm_generated"
                    elif (run.stage or "").lower() == "fine_tuned":
                        version_to_generation_source[int(run.version_number)] = "fine_tuned"
                    else:
                        version_to_generation_source[int(run.version_number)] = "standard"
        finally:
            db.close()

        versions_with_ids: List[Dict[str, Any]] = []

        if file_id in TRAINING_HISTORY and len(TRAINING_HISTORY[file_id]) > 0:
            for version_entry in TRAINING_HISTORY[file_id]:
                if isinstance(version_entry, dict):
                    version_number = int(version_entry.get("version", 0) or 0)
                    enriched = copy.deepcopy(version_entry)
                    enriched["training_run_id"] = version_to_training_run_id.get(version_number)
                    run_duration_seconds = version_to_run_duration_seconds.get(version_number, 0.0)
                    enriched["training_time_seconds"] = run_duration_seconds
                    generated_by = version_to_generation_source.get(version_number, "standard")
                    enriched["generated_by"] = generated_by

                    if isinstance(enriched.get("models"), list):
                        model_count = max(1, len(enriched["models"]))
                        per_model_time = run_duration_seconds / model_count
                        next_models: List[Dict[str, Any]] = []
                        for model_entry in enriched["models"]:
                            if isinstance(model_entry, dict):
                                model_copy = copy.deepcopy(model_entry)
                                model_copy["generated_by"] = model_copy.get("generated_by", generated_by)
                                model_copy["training_time_seconds"] = float(
                                    model_copy.get("training_time_seconds", per_model_time)
                                )
                                next_models.append(model_copy)
                            else:
                                next_models.append(model_entry)
                        enriched["models"] = next_models

                    versions_with_ids.append(enriched)
                else:
                    versions_with_ids.append(version_entry)
        else:
            if not training_runs:
                raise HTTPException(
                    status_code=404,
                    detail=f"No training history found for file '{file_id}'."
                )

            db = SessionLocal()
            try:
                for run in training_runs:
                    model_versions = (
                        db.query(ModelVersion)
                        .filter(ModelVersion.training_run_id == run.id)
                        .order_by(ModelVersion.rank_position.asc(), ModelVersion.created_at.asc())
                        .all()
                    )

                    run_duration_seconds = version_to_run_duration_seconds.get(int(run.version_number), 0.0)
                    model_count = max(1, len(model_versions))
                    per_model_time = run_duration_seconds / model_count

                    models_payload: List[Dict[str, Any]] = []
                    for mv in model_versions:
                        models_payload.append(
                            {
                                "model_name": mv.model_name,
                                "name": mv.model_name,
                                "rmse": mv.rmse,
                                "mae": mv.mae,
                                "r2": mv.r2,
                                "hyperparameters": mv.hyperparameters,
                                "rank_position": mv.rank_position,
                                "training_time_seconds": per_model_time,
                                "generated_by": (
                                    "llm_generated"
                                    if run.agent_run_id is not None
                                    else "fine_tuned"
                                    if (run.stage or "").lower() == "fine_tuned"
                                    else "standard"
                                ),
                            }
                        )

                    best_model_payload: Dict[str, Any] = {
                        "name": run.best_model_name,
                        "model_name": run.best_model_name,
                        "rmse": run.rmse,
                        "mae": run.mae,
                        "r2": run.r2,
                    }

                    versions_with_ids.append(
                        {
                            "version": int(run.version_number),
                            "models": models_payload,
                            "training_time_seconds": run_duration_seconds,
                            "best_model": best_model_payload,
                            "training_run_id": str(run.id),
                            "generated_by": (
                                "llm_generated"
                                if run.agent_run_id is not None
                                else "fine_tuned"
                                if (run.stage or "").lower() == "fine_tuned"
                                else "standard"
                            ),
                        }
                    )
            finally:
                db.close()

        # Return history
        response = {
            "file_id": file_id,
            "target_column": target_column,
            "versions": versions_with_ids
        }
        
        return JSONResponse(
            status_code=200,
            content=response
        )
        
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail="Internal Server Error"
        )


def _build_training_code(training_run: TrainingRun, model_versions: List[ModelVersion]) -> str:
    model_blocks: List[str] = []
    for mv in model_versions:
        model_name = mv.model_name
        params = mv.hyperparameters if isinstance(mv.hyperparameters, dict) else {}
        model_blocks.append(
            f"    {{\"model_name\": \"{model_name}\", \"hyperparameters\": {json.dumps(params)} }}"
        )

    models_literal = "[\n" + ",\n".join(model_blocks) + "\n]" if model_blocks else "[]"

    return f'''import pandas as pd
from sklearn.model_selection import train_test_split
from sklearn.metrics import mean_squared_error, mean_absolute_error, r2_score
from sklearn.linear_model import LinearRegression, Ridge, Lasso, ElasticNet
from sklearn.ensemble import RandomForestRegressor
from xgboost import XGBRegressor
import numpy as np

# Generated from training_run_id: {training_run.id}
# Version: {training_run.version_number}

DATA_PATH = "YOUR_DATASET.csv"
TARGET_COLUMN = "YOUR_TARGET_COLUMN"

MODEL_CONFIGS = {models_literal}


def build_model(model_name: str, hyperparameters: dict):
    if model_name == "LinearRegression":
        return LinearRegression(**hyperparameters)
    if model_name == "Ridge":
        return Ridge(**hyperparameters)
    if model_name == "Lasso":
        return Lasso(**hyperparameters)
    if model_name == "ElasticNet":
        return ElasticNet(**hyperparameters)
    if model_name == "RandomForestRegressor":
        base_params = {{"random_state": 42}}
        base_params.update(hyperparameters)
        return RandomForestRegressor(**base_params)
    if model_name == "XGBRegressor":
        base_params = {{"random_state": 42, "verbosity": 0}}
        base_params.update(hyperparameters)
        return XGBRegressor(**base_params)
    raise ValueError(f"Unsupported model: {{model_name}}")


def run_training():
    df = pd.read_csv(DATA_PATH)
    X = pd.get_dummies(df.drop(columns=[TARGET_COLUMN]), drop_first=True)
    y = df[TARGET_COLUMN]
    X_train, X_test, y_train, y_test = train_test_split(X, y, test_size=0.2, random_state=42)

    summary = []
    for cfg in MODEL_CONFIGS:
        model = build_model(cfg["model_name"], cfg.get("hyperparameters", {{}}))
        model.fit(X_train, y_train)
        preds = model.predict(X_test)

        rmse = float(np.sqrt(mean_squared_error(y_test, preds)))
        mae = float(mean_absolute_error(y_test, preds))
        r2 = float(r2_score(y_test, preds))
        summary.append({{
            "model_name": cfg["model_name"],
            "rmse": rmse,
            "mae": mae,
            "r2": r2,
        }})

    summary.sort(key=lambda x: x["rmse"])
    return summary


if __name__ == "__main__":
    output = run_training()
    print(output)
'''


@app.get("/models/download/{training_run_id}")
def download_model_artifact(training_run_id: uuid.UUID, model_name: Optional[str] = None):
    db = SessionLocal()
    try:
        training_run = db.query(TrainingRun).filter(TrainingRun.id == training_run_id).first()
        if training_run is None:
            raise HTTPException(status_code=404, detail="Training run not found")

        source_training_run = training_run
        query = db.query(ModelVersion).filter(ModelVersion.training_run_id == training_run_id)
        if model_name:
            query = query.filter(ModelVersion.model_name == model_name)

        model_versions = query.order_by(ModelVersion.rank_position.asc()).all()
        if model_name and not model_versions:
            # The UI can display models aggregated across project versions.
            # Fall back to the latest run in the same project that contains the requested model.
            fallback_row = (
                db.query(ModelVersion, TrainingRun)
                .join(TrainingRun, ModelVersion.training_run_id == TrainingRun.id)
                .filter(
                    TrainingRun.project_id == training_run.project_id,
                    ModelVersion.model_name == model_name,
                )
                .order_by(TrainingRun.version_number.desc(), TrainingRun.created_at.desc())
                .first()
            )
            if fallback_row is not None:
                fallback_model_version, fallback_training_run = fallback_row
                model_versions = [fallback_model_version]
                source_training_run = fallback_training_run

        if not model_versions:
            raise HTTPException(
                status_code=404,
                detail=(
                    f"No model artifacts found for model '{model_name}' in this project."
                    if model_name
                    else "No model artifacts found"
                ),
            )

        project = db.query(Project).filter(Project.id == source_training_run.project_id).first()
        if project is None:
            raise HTTPException(status_code=404, detail="Project not found for training run")

        file_path = UPLOAD_DIR / project.file_id
        if not file_path.exists():
            raise HTTPException(status_code=404, detail="Dataset file not found for training run")

        df = pd.read_csv(file_path)
        validate_dataset_limits(df)

        target_column = project.target_column
        if target_column not in df.columns:
            raise HTTPException(status_code=400, detail=f"Target column '{target_column}' not found in dataset")

        X = pd.get_dummies(df.drop(columns=[target_column]), drop_first=True)
        y = df[target_column]

        def _build_model(model_name_local: str, params: Dict[str, Any]) -> Any:
            safe_params = params if isinstance(params, dict) else {}
            if model_name_local == "LinearRegression":
                return LinearRegression(**safe_params)
            if model_name_local == "Ridge":
                return Ridge(**safe_params)
            if model_name_local == "Lasso":
                return Lasso(**safe_params)
            if model_name_local == "ElasticNet":
                return ElasticNet(**safe_params)
            if model_name_local == "RandomForestRegressor":
                base_params = {"random_state": 42}
                base_params.update(safe_params)
                return RandomForestRegressor(**base_params)
            if model_name_local == "XGBRegressor":
                base_params = {"random_state": 42, "verbosity": 0}
                base_params.update(safe_params)
                return XGBRegressor(**base_params)
            raise HTTPException(status_code=400, detail=f"Unsupported model '{model_name_local}'")

        def _build_pickle_payload(mv: ModelVersion, source_run: TrainingRun) -> bytes:
            params = mv.hyperparameters if isinstance(mv.hyperparameters, dict) else {}
            model = _build_model(mv.model_name, params)
            model.fit(X, y)

            artifact = {
                "model": model,
                "model_name": mv.model_name,
                "hyperparameters": params,
                "feature_columns": X.columns.tolist(),
                "target_column": target_column,
                "training_run_id": str(source_run.id),
                "version_number": source_run.version_number,
            }
            return pickle.dumps(artifact)

        if model_name:
            selected_model = model_versions[0]
            data = _build_pickle_payload(selected_model, source_training_run)
            model_slug = re.sub(r"[^a-zA-Z0-9_-]", "_", selected_model.model_name)
            filename = f"model_artifact_{source_training_run.id}_{model_slug}.pkl"
            return StreamingResponse(
                io.BytesIO(data),
                media_type="application/octet-stream",
                headers={"Content-Disposition": f'attachment; filename="{filename}"'},
            )

        zip_buffer = io.BytesIO()
        with zipfile.ZipFile(zip_buffer, mode="w", compression=zipfile.ZIP_DEFLATED) as zf:
            for mv in model_versions:
                model_slug = re.sub(r"[^a-zA-Z0-9_-]", "_", mv.model_name)
                artifact_name = f"{model_slug}.pkl"
                zf.writestr(artifact_name, _build_pickle_payload(mv, source_training_run))

        zip_buffer.seek(0)
        zip_filename = f"model_artifacts_{training_run_id}.zip"
        return StreamingResponse(
            zip_buffer,
            media_type="application/zip",
            headers={"Content-Disposition": f'attachment; filename="{zip_filename}"'},
        )
    finally:
        db.close()


def _synthesize_native_training_script(
    target_column: str,
    model_versions: list,
    training_run_id: str,
    file_id: Optional[str] = None,
) -> str:
    """Generate a runnable Python training script from native-mode ModelVersion records."""
    import json as _json

    model_entries = []
    for mv in model_versions:
        name = mv.model_name or "RandomForestRegressor"
        hp = mv.hyperparameters or {}
        if isinstance(hp, str):
            try:
                hp = _json.loads(hp)
            except Exception:
                hp = {}
        model_entries.append((name, hp, mv.rmse, mv.mae, mv.r2, mv.rank_position))

    # Build model instantiation lines
    model_init_lines = []
    for name, hp, *_ in model_entries:
        if hp:
            hp_str = ", ".join(f"{k}={repr(v)}" for k, v in hp.items())
            model_init_lines.append(f'    ("{name}", {name}({hp_str})),')
        else:
            model_init_lines.append(f'    ("{name}", {name}()),')

    models_block = "\n".join(model_init_lines) if model_init_lines else '    ("RandomForestRegressor", RandomForestRegressor()),'

    dataset_comment = f"# Dataset file_id: {file_id}" if file_id else "# Replace DATA_PATH with the path to your CSV file."
    data_path_line = f'DATA_PATH = "{file_id}"  # update this path' if file_id else 'DATA_PATH = "dataset.csv"  # update this path'

    script = f'''\
"""
AutoAI Builder — Training Script
Generated for training run: {training_run_id}
Target column: {target_column}
"""
import numpy as np
import pandas as pd
from sklearn.model_selection import train_test_split
from sklearn.metrics import mean_squared_error, mean_absolute_error, r2_score
from sklearn.linear_model import LinearRegression, Ridge, Lasso, ElasticNet
from sklearn.ensemble import RandomForestRegressor
from xgboost import XGBRegressor

{dataset_comment}
{data_path_line}
TARGET_COLUMN = {repr(target_column)}

# Load data
df = pd.read_csv(DATA_PATH)

# --- Preprocessing ---
X = df.drop(columns=[TARGET_COLUMN])
y = df[TARGET_COLUMN]

# One-hot encode categorical columns
X = pd.get_dummies(X, drop_first=True)

# Fill any remaining NaN values
X = X.replace([float("inf"), float("-inf")], float("nan")).fillna(0.0)

X_train, X_test, y_train, y_test = train_test_split(
    X, y, test_size=0.2, random_state=42
)

# --- Models ---
models = [
{models_block}
]

# --- Training & Evaluation ---
results = []
for model_name, model in models:
    try:
        model.fit(X_train, y_train)
        preds = model.predict(X_test)
        rmse = float(np.sqrt(mean_squared_error(y_test, preds)))
        mae = float(mean_absolute_error(y_test, preds))
        r2 = float(r2_score(y_test, preds))
        results.append({{"model": model_name, "rmse": rmse, "mae": mae, "r2": r2}})
        print(f"{{model_name:35s}}  RMSE={{rmse:12.4f}}  MAE={{mae:12.4f}}  R2={{r2:.4f}}")
    except Exception as exc:
        print(f"{{model_name}} failed: {{exc}}")

# --- Summary ---
if results:
    best = min(results, key=lambda x: x["rmse"])
    print(f"\\nBest model: {{best[\'model\']}} (RMSE={{best[\'rmse\']:.4f}})")
'''
    return script


@app.get("/models/code/{training_run_id}")
def download_model_code(training_run_id: uuid.UUID, model_name: Optional[str] = None):
    db = SessionLocal()
    try:
        training_run = db.query(TrainingRun).filter(TrainingRun.id == training_run_id).first()
        if training_run is None:
            raise HTTPException(status_code=404, detail="Training run not found")

        source_training_run = training_run
        ordered_agent_run_ids: List[str] = []
        sandbox_job = None

        # If this training run has an agent_run_id, try to find sandbox code
        if training_run.agent_run_id is not None:
            # training_run.version_number is project-global, while SandboxJob.iteration_number
            # is agent-local. Build the agent-local iteration index for this run.
            agent_runs_for_project = (
                db.query(TrainingRun.id)
                .filter(TrainingRun.agent_run_id == training_run.agent_run_id)
                .order_by(TrainingRun.version_number.asc(), TrainingRun.created_at.asc())
                .all()
            )
            ordered_agent_run_ids = [str(row[0]) for row in agent_runs_for_project]
            expected_iteration_number: Optional[int] = None
            current_training_run_id = str(training_run.id)
            if current_training_run_id in ordered_agent_run_ids:
                expected_iteration_number = ordered_agent_run_ids.index(current_training_run_id) + 1

            if expected_iteration_number is not None:
                sandbox_job = (
                    db.query(SandboxJob)
                    .filter(
                        SandboxJob.agent_id == training_run.agent_run_id,
                        SandboxJob.iteration_number == expected_iteration_number,
                    )
                    .order_by(SandboxJob.created_at.desc())
                    .first()
                )

            # Fallback in case of legacy runs where agent_id linkage was not preserved.
            if sandbox_job is None:
                sandbox_job = (
                    db.query(SandboxJob)
                    .filter(
                        SandboxJob.project_id == training_run.project_id,
                        SandboxJob.iteration_number == (
                            expected_iteration_number if expected_iteration_number is not None else training_run.version_number
                        ),
                    )
                    .order_by(SandboxJob.created_at.desc())
                    .first()
                )

            # Final fallback: latest sandbox job for this agent run.
            if sandbox_job is None:
                sandbox_job = (
                    db.query(SandboxJob)
                    .filter(SandboxJob.agent_id == training_run.agent_run_id)
                    .order_by(SandboxJob.created_at.desc())
                    .first()
                )

        if sandbox_job is not None and isinstance(sandbox_job.script_content, str) and sandbox_job.script_content.strip():
            llm_code = sandbox_job.script_content
            model_slug = re.sub(r"[^a-zA-Z0-9_-]", "_", model_name) if model_name else "all_models"
            filename = f"training_code_{training_run_id}_{model_slug}.py"
            return StreamingResponse(
                io.BytesIO(llm_code.encode("utf-8")),
                media_type="text/x-python",
                headers={"Content-Disposition": f'attachment; filename="{filename}"'},
            )

        # No SandboxJob found (native execution mode) — synthesize a runnable script
        # from the ModelVersion records persisted for this training run.
        model_versions_query = (
            db.query(ModelVersion)
            .filter(ModelVersion.training_run_id == training_run.id)
        )
        # If a specific model is requested, filter to just that model
        if model_name:
            model_versions_query = model_versions_query.filter(ModelVersion.model_name == model_name)
        model_versions = model_versions_query.order_by(ModelVersion.rank_position.asc()).all()

        # If this specific run has no models, collect from all runs in this agent session
        # so the user always gets a script with the full model list.
        if not model_versions:
            all_run_ids = [uuid.UUID(rid) for rid in ordered_agent_run_ids]
            fallback_query = (
                db.query(ModelVersion)
                .filter(ModelVersion.training_run_id.in_(all_run_ids))
            )
            if model_name:
                fallback_query = fallback_query.filter(ModelVersion.model_name == model_name)
            model_versions = fallback_query.order_by(ModelVersion.rmse.asc()).all()

        # Secondary fallback: search across all project training runs for the requested model
        if not model_versions and model_name and training_run.project_id:
            project_fallback_row = (
                db.query(ModelVersion, TrainingRun)
                .join(TrainingRun, ModelVersion.training_run_id == TrainingRun.id)
                .filter(
                    TrainingRun.project_id == training_run.project_id,
                    ModelVersion.model_name == model_name,
                )
                .order_by(TrainingRun.version_number.desc(), TrainingRun.created_at.desc())
                .first()
            )
            if project_fallback_row is not None:
                fallback_model_version, fallback_training_run = project_fallback_row
                model_versions = [fallback_model_version]
                source_training_run = fallback_training_run

        if not model_versions:
            raise HTTPException(
                status_code=404,
                detail=(
                    f"No code data found for model '{model_name}' in this project."
                    if model_name
                    else "No model data found for this training run."
                ),
            )

        # Fetch project for target_column / file_id (use source_training_run in case of fallback)
        project = db.query(Project).filter(Project.id == source_training_run.project_id).first()
        target_col = (project.target_column if project and project.target_column else "target")
        file_id_val = (project.file_id if project else None)

        synthesized = _synthesize_native_training_script(
            target_column=target_col,
            model_versions=model_versions,
            training_run_id=str(source_training_run.id),
            file_id=file_id_val,
        )
        model_slug = re.sub(r"[^a-zA-Z0-9_-]", "_", model_name) if model_name else "all_models"
        filename = f"training_code_{training_run_id}_{model_slug}.py"
        return StreamingResponse(
            io.BytesIO(synthesized.encode("utf-8")),
            media_type="text/x-python",
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )
    finally:
        db.close()


def _canonical_regression_model_name(model_name: str) -> str:
    normalized = re.sub(r"[^a-z0-9]", "", str(model_name).lower())
    if normalized in {"linearregression", "linear"}:
        return "LinearRegression"
    if normalized == "ridge":
        return "Ridge"
    if normalized == "lasso":
        return "Lasso"
    if normalized in {"elasticnet", "elastic"}:
        return "ElasticNet"
    if normalized in {"randomforestregressor", "randomforest", "rf"}:
        return "RandomForestRegressor"
    if normalized in {"xgbregressor", "xgboost", "xgb"}:
        return "XGBRegressor"
    raise ValueError(f"Unknown model type: {model_name}")


def _apply_prompt_hyperparameter_overrides(model_name: str, prompt: Optional[str], params: Dict[str, Any]) -> Dict[str, Any]:
    if not prompt:
        return dict(params)

    updated = dict(params)
    prompt_text = prompt.lower()
    speed_hint = any(token in prompt_text for token in ["faster", "speed", "latency", "quick"])
    regularization_hint = any(token in prompt_text for token in ["overfit", "regularization", "generalization", "stability"])
    quality_hint = any(token in prompt_text for token in ["improve", "better", "accuracy", "performance", "quality"])

    if model_name == "RandomForestRegressor":
        if speed_hint:
            updated["n_estimators"] = min(int(updated.get("n_estimators", 150)), 120)
            updated["max_depth"] = min(int(updated.get("max_depth", 10)), 8)
        if regularization_hint:
            updated["min_samples_split"] = max(int(updated.get("min_samples_split", 2)), 4)
            updated["min_samples_leaf"] = max(int(updated.get("min_samples_leaf", 1)), 2)
        if quality_hint:
            updated["n_estimators"] = max(int(updated.get("n_estimators", 150)), 220)

    if model_name == "XGBRegressor":
        if speed_hint:
            updated["n_estimators"] = min(int(updated.get("n_estimators", 250)), 200)
            updated["learning_rate"] = max(float(updated.get("learning_rate", 0.05)), 0.08)
        if regularization_hint:
            updated["max_depth"] = min(int(updated.get("max_depth", 6)), 5)
            updated["subsample"] = min(float(updated.get("subsample", 1.0)), 0.85)
        if quality_hint:
            updated["n_estimators"] = max(int(updated.get("n_estimators", 250)), 300)

    if model_name in {"Ridge", "Lasso", "ElasticNet"} and regularization_hint:
        updated["alpha"] = max(float(updated.get("alpha", 1.0)), 1.0)
    if model_name in {"Ridge", "Lasso", "ElasticNet"} and quality_hint:
        updated["alpha"] = min(float(updated.get("alpha", 0.1)), 0.5)
    if model_name in {"Lasso", "ElasticNet"}:
        updated["max_iter"] = max(int(updated.get("max_iter", 5000)), 5000)

    return updated


def _derive_fine_tune_hyperparameters(
    canonical_model_name: str,
    llm_provider: str,
    baseline_metrics: Dict[str, float],
    prompt: Optional[str],
) -> Dict[str, Any]:
    llm_response = get_hyperparameter_suggestions(canonical_model_name, baseline_metrics, llm_provider)
    suggested = llm_response.get("hyperparameters") if isinstance(llm_response, dict) else {}
    if not isinstance(suggested, dict):
        suggested = {}
    prompt_adjusted = _apply_prompt_hyperparameter_overrides(canonical_model_name, prompt, suggested)
    return validate_hyperparameters(canonical_model_name, prompt_adjusted)


def _build_fine_tune_sandbox_script(target_column: str, model_name: str, hyperparameters: Dict[str, Any]) -> str:
    model_initializers = {
        "LinearRegression": "LinearRegression()",
        "Ridge": "Ridge(**HYPERPARAMETERS)",
        "Lasso": "Lasso(**HYPERPARAMETERS)",
        "ElasticNet": "ElasticNet(**HYPERPARAMETERS)",
        "RandomForestRegressor": "RandomForestRegressor(random_state=42, **HYPERPARAMETERS)",
        "XGBRegressor": "XGBRegressor(random_state=42, verbosity=0, **HYPERPARAMETERS)",
    }
    if model_name not in model_initializers:
        raise ValueError(f"Unsupported model for fine-tune sandbox run: {model_name}")

    return f'''import json
import numpy as np
import pandas as pd
from sklearn.model_selection import train_test_split
from sklearn.metrics import mean_squared_error, mean_absolute_error, r2_score
from sklearn.linear_model import LinearRegression, Ridge, Lasso, ElasticNet
from sklearn.ensemble import RandomForestRegressor
from xgboost import XGBRegressor

TARGET_COLUMN = {target_column!r}
HYPERPARAMETERS = {repr(hyperparameters)}

df = pd.read_csv("/app/dataset.csv")
if TARGET_COLUMN not in df.columns:
    raise ValueError(f"Target column '{{TARGET_COLUMN}}' not found in dataset")

X = df.drop(columns=[TARGET_COLUMN])
y = df[TARGET_COLUMN]
if not pd.api.types.is_numeric_dtype(y):
    raise ValueError("Target column must be numeric for regression")

X = pd.get_dummies(X, drop_first=True)
X = X.replace([float("inf"), float("-inf")], float("nan")).fillna(0.0)
X_train, X_test, y_train, y_test = train_test_split(X, y, test_size=0.2, random_state=42)

model = {model_initializers[model_name]}
model.fit(X_train, y_train)
preds = model.predict(X_test)

rmse_holdout = float(np.sqrt(mean_squared_error(y_test, preds)))
mae = float(mean_absolute_error(y_test, preds))
r2 = float(r2_score(y_test, preds))

result_json = {{
    "rmse": rmse_holdout,
    "rmse_holdout": rmse_holdout,
    "rmse_cv": rmse_holdout,
    "mae": mae,
    "r2": r2,
    "model_name": {model_name!r},
    "hyperparameters": HYPERPARAMETERS,
}}
print(json.dumps({{"result_json": result_json}}))
'''


def _poll_sandbox_job_for_fine_tune(job_id: uuid.UUID, timeout_seconds: int) -> Dict[str, Any]:
    started_at = time.time()
    last_status: Optional[str] = None
    last_progress_at = started_at

    while True:
        now = time.time()
        if now - started_at > timeout_seconds:
            raise RuntimeError(f"Fine-tune sandbox polling timed out after {timeout_seconds}s")

        db = SessionLocal()
        try:
            job = db.query(SandboxJob).filter(SandboxJob.id == job_id).first()
        finally:
            db.close()

        if job is None:
            raise RuntimeError("Fine-tune sandbox job not found")

        if job.status != last_status:
            last_status = job.status
            last_progress_at = now

        if job.status in {"queued", "running"}:
            if now - last_progress_at > FINE_TUNE_SANDBOX_INACTIVITY_TIMEOUT_SECONDS:
                raise RuntimeError(
                    "Fine-tune sandbox job made no status progress and appears stalled. "
                    "Ensure backend/sandbox_worker.py is running."
                )

            if not _is_sandbox_worker_running() and now - last_progress_at > 10:
                raise RuntimeError(
                    "Sandbox worker appears offline while fine-tune job is waiting. "
                    "Start backend/sandbox_worker.py and retry."
                )

            time.sleep(2)
            continue
        if job.status == "timeout":
            raise RuntimeError("Fine-tune sandbox execution timeout")
        if job.status != "completed":
            raise RuntimeError(job.error_log or "Fine-tune sandbox execution failed")

        parsed = job.result_json
        if isinstance(parsed, str):
            parsed = json.loads(parsed)
        if not isinstance(parsed, dict):
            raise RuntimeError("Fine-tune sandbox result_json is invalid")

        result_json = parsed.get("result_json")
        if not isinstance(result_json, dict):
            raise RuntimeError("Fine-tune sandbox result missing result_json payload")
        return result_json


def fine_tune_best_model(file_path: Path, target_column: str, best_model_name: str, llm_provider: str, hyperparameters: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """
    Fine-tune the best model with improved hyperparameters.
    
    Args:
        file_path: Path to the CSV file
        target_column: Name of the target column
        best_model_name: Name of the best model to fine-tune
        llm_provider: LLM provider for hyperparameter suggestions
        hyperparameters: Optional pre-derived hyperparameters to use
        
    Returns:
        Dictionary containing model metrics
    """
    # Load dataset
    df = pd.read_csv(file_path)
    validate_dataset_limits(df)
    
    # Validate target column exists
    if target_column not in df.columns:
        raise ValueError(f"Target column '{target_column}' not found in dataset.")
    
    # Validate target is numeric
    if not pd.api.types.is_numeric_dtype(df[target_column]):
        raise ValueError("Target column must be numeric for regression.")
    
    # Split features and target
    X = df.drop(columns=[target_column])
    y = df[target_column]
    
    # Handle categorical columns with one-hot encoding
    X = pd.get_dummies(X, drop_first=True)
    
    # Split into train and test sets
    X_train, X_test, y_train, y_test = train_test_split(
        X, y, test_size=0.2, random_state=42
    )
    
    normalized_model_name = re.sub(r"[^a-z0-9]", "", str(best_model_name).lower())
    hp = hyperparameters or {}

    # Create model with improved hyperparameters based on model name
    if normalized_model_name in {"linearregression", "linear"}:
        model = LinearRegression(**{k: v for k, v in hp.items() if k in []})
    elif normalized_model_name == "ridge":
        alpha_val = hp.get("alpha", 10)
        model = Ridge(alpha=alpha_val)
    elif normalized_model_name == "lasso":
        alpha_val = hp.get("alpha", 0.01)
        max_iter_val = hp.get("max_iter", 5000)
        model = Lasso(alpha=alpha_val, max_iter=max_iter_val)
    elif normalized_model_name in {"elasticnet", "elastic"}:
        alpha_val = hp.get("alpha", 0.01)
        l1_ratio_val = hp.get("l1_ratio", 0.5)
        max_iter_val = hp.get("max_iter", 5000)
        model = ElasticNet(alpha=alpha_val, l1_ratio=l1_ratio_val, max_iter=max_iter_val)
    elif normalized_model_name in {"randomforestregressor", "randomforest", "rf"}:
        n_estimators_val = hp.get("n_estimators", 200)
        max_depth_val = hp.get("max_depth", 10)
        min_samples_split_val = hp.get("min_samples_split", 2)
        min_samples_leaf_val = hp.get("min_samples_leaf", 1)
        model = RandomForestRegressor(
            n_estimators=n_estimators_val,
            max_depth=max_depth_val,
            min_samples_split=min_samples_split_val,
            min_samples_leaf=min_samples_leaf_val,
            random_state=42,
        )
    elif normalized_model_name in {"xgbregressor", "xgboost", "xgb"}:
        n_estimators_val = hp.get("n_estimators", 300)
        learning_rate_val = hp.get("learning_rate", 0.05)
        max_depth_val = hp.get("max_depth", 6)
        subsample_val = hp.get("subsample", 1.0)
        model = XGBRegressor(
            n_estimators=n_estimators_val,
            learning_rate=learning_rate_val,
            max_depth=max_depth_val,
            subsample=subsample_val,
            random_state=42,
            verbosity=0,
        )
    else:
        raise ValueError(f"Unknown model type: {best_model_name}")
    
    # Train the model
    model.fit(X_train, y_train)
    
    # Make predictions
    y_pred = model.predict(X_test)
    
    # Calculate metrics
    rmse = np.sqrt(mean_squared_error(y_test, y_pred))
    mae = mean_absolute_error(y_test, y_pred)
    r2 = r2_score(y_test, y_pred)
    
    return {
        "name": best_model_name,
        "rmse": float(rmse),
        "mae": float(mae),
        "r2": float(r2)
    }


@app.post("/training/fine-tune")
async def fine_tune_model(request: FineTuneRequest):
    """
    Fine-tune the best model from the latest training version.
    
    Improves the best model with better hyperparameters and creates a new version.
    """
    try:
        # Validate llm_provider
        allowed_providers = ["openai", "claude", "gemini", "mistral", "groq"]
        if request.llm_provider not in allowed_providers:
            raise HTTPException(
                status_code=400,
                detail=f"Invalid llm_provider '{request.llm_provider}'. Allowed values: {', '.join(allowed_providers)}"
            )
        
        # Check if training history exists for this file
        if request.file_id not in TRAINING_HISTORY or len(TRAINING_HISTORY[request.file_id]) == 0:
            db = SessionLocal()
            try:
                project = db.query(Project).filter(Project.file_id == request.file_id).first()
                if project is not None:
                    training_runs = (
                        db.query(TrainingRun)
                        .filter(TrainingRun.project_id == project.id)
                        .order_by(TrainingRun.version_number.asc())
                        .all()
                    )

                    rebuilt_versions: List[Dict[str, Any]] = []
                    for run in training_runs:
                        model_versions = (
                            db.query(ModelVersion)
                            .filter(ModelVersion.training_run_id == run.id)
                            .order_by(ModelVersion.rank_position.asc(), ModelVersion.created_at.asc())
                            .all()
                        )

                        models_payload: List[Dict[str, Any]] = []
                        for mv in model_versions:
                            models_payload.append(
                                {
                                    "name": mv.model_name,
                                    "model_name": mv.model_name,
                                    "rmse": mv.rmse,
                                    "mae": mv.mae,
                                    "r2": mv.r2,
                                    "hyperparameters": mv.hyperparameters,
                                }
                            )

                        best_model_payload: Dict[str, Any] = {
                            "name": run.best_model_name,
                            "model_name": run.best_model_name,
                            "rmse": run.rmse,
                            "mae": run.mae,
                            "r2": run.r2,
                        }

                        rebuilt_versions.append(
                            {
                                "version": int(run.version_number),
                                "models": models_payload,
                                "best_model": best_model_payload,
                            }
                        )

                    if rebuilt_versions:
                        TRAINING_HISTORY[request.file_id] = rebuilt_versions
            finally:
                db.close()

        if request.file_id not in TRAINING_HISTORY or len(TRAINING_HISTORY[request.file_id]) == 0:
            raise HTTPException(
                status_code=404,
                detail=f"No training history found for file '{request.file_id}'. Please run initial training first."
            )
        
        # Construct file path
        file_path = UPLOAD_DIR / request.file_id
        
        # Check if file exists
        if not file_path.exists():
            raise HTTPException(
                status_code=404,
                detail=f"File '{request.file_id}' not found. Please upload a CSV file first."
            )
        
        # Get latest usable version (skip malformed/empty version records)
        history_versions = TRAINING_HISTORY[request.file_id]
        latest_version_data = history_versions[-1]
        for candidate in reversed(history_versions):
            if not isinstance(candidate, dict):
                continue
            has_models = isinstance(candidate.get("models"), list) and len(candidate.get("models")) > 0
            has_best = isinstance(candidate.get("best_model"), dict) and bool(candidate.get("best_model", {}).get("name"))
            if has_models or has_best:
                latest_version_data = candidate
                break

        previous_version = latest_version_data.get("version", len(history_versions))
        best_model_before = latest_version_data.get("best_model") or {}

        selected_model_name = request.model_name.strip() if isinstance(request.model_name, str) else None
        if selected_model_name:
            candidate_models = latest_version_data.get("models", [])
            matched_model = None
            if isinstance(candidate_models, list):
                for model_entry in candidate_models:
                    if not isinstance(model_entry, dict):
                        continue
                    model_entry_name = model_entry.get("name") or model_entry.get("model_name")
                    if model_entry_name == selected_model_name:
                        matched_model = model_entry
                        break

            if matched_model is None:
                # Fallback: search prior versions for the requested model
                for version_entry in reversed(history_versions):
                    if not isinstance(version_entry, dict):
                        continue
                    version_models = version_entry.get("models", [])
                    if not isinstance(version_models, list):
                        continue
                    for model_entry in version_models:
                        if not isinstance(model_entry, dict):
                            continue
                        model_entry_name = model_entry.get("name") or model_entry.get("model_name")
                        if model_entry_name == selected_model_name:
                            matched_model = model_entry
                            break
                    if matched_model is not None:
                        break

            if matched_model is None:
                raise HTTPException(
                    status_code=400,
                    detail=f"Selected model '{selected_model_name}' not found in latest version."
                )

            best_model_before = {
                "name": matched_model.get("name") or matched_model.get("model_name"),
                "rmse": matched_model.get("rmse"),
                "mae": matched_model.get("mae"),
                "r2": matched_model.get("r2"),
            }
        
        # Fine-tune the best model
        try:
            selected_model = best_model_before.get("name")
            if not isinstance(selected_model, str) or not selected_model.strip():
                raise HTTPException(status_code=400, detail="No valid model selected for fine-tuning.")

            canonical_model_name = _canonical_regression_model_name(selected_model)
            baseline_rmse = float(best_model_before.get("rmse")) if best_model_before.get("rmse") is not None else 0.0
            baseline_mae = float(best_model_before.get("mae")) if best_model_before.get("mae") is not None else 0.0
            baseline_r2 = float(best_model_before.get("r2")) if best_model_before.get("r2") is not None else 0.0

            new_version = len(TRAINING_HISTORY[request.file_id]) + 1
            persisted_training_run_id: Optional[str] = None
            fine_tuned_metrics: Dict[str, Any]
            tuned_hyperparameters: Dict[str, Any] = {}

            if FINE_TUNE_USE_SANDBOX:
                _ensure_sandbox_worker_running()

                sandbox_job_id: Optional[uuid.UUID] = None
                fine_tuned_run_id: Optional[uuid.UUID] = None

                db = SessionLocal()
                try:
                    project = db.query(Project).filter(Project.file_id == request.file_id).first()
                    if project is None:
                        raise HTTPException(
                            status_code=404,
                            detail=f"Project not found for file '{request.file_id}'."
                        )

                    latest_db_version = (
                        db.query(func.max(TrainingRun.version_number))
                        .filter(TrainingRun.project_id == project.id)
                        .scalar()
                    )
                    new_version = int(latest_db_version or 0) + 1

                    latest_agent_run = (
                        db.query(AgentRun)
                        .filter(AgentRun.project_id == project.id)
                        .order_by(AgentRun.created_at.desc())
                        .first()
                    )

                    if latest_agent_run is None:
                        now_utc = datetime.utcnow()
                        latest_agent_run = AgentRun(
                            project_id=project.id,
                            status="completed",
                            current_iteration=1,
                            max_iterations=1,
                            improvement_threshold=0.0,
                            started_at=now_utc,
                            completed_at=now_utc,
                        )
                        db.add(latest_agent_run)
                        db.flush()

                    tuned_hyperparameters = _derive_fine_tune_hyperparameters(
                        canonical_model_name,
                        request.llm_provider,
                        {
                            "rmse": baseline_rmse,
                            "mae": baseline_mae,
                            "r2": baseline_r2,
                            "improvement": 0.0,
                        },
                        request.prompt,
                    )

                    sandbox_script = _build_fine_tune_sandbox_script(
                        target_column=request.target_column,
                        model_name=canonical_model_name,
                        hyperparameters=tuned_hyperparameters,
                    )

                    fine_tuned_run = TrainingRun(
                        project_id=project.id,
                        agent_run_id=latest_agent_run.id,  # Link to agent for code download
                        version_number=new_version,
                        status="running",
                        stage="fine_tuning",
                        progress=30,
                        started_at=datetime.utcnow(),
                    )
                    db.add(fine_tuned_run)
                    db.flush()

                    sandbox_job = SandboxJob(
                        agent_id=latest_agent_run.id,
                        project_id=project.id,
                        iteration_number=new_version,
                        script_content=sandbox_script,
                        status="queued",
                        timeout_seconds=FINE_TUNE_SANDBOX_TIMEOUT_SECONDS,
                    )
                    db.add(sandbox_job)
                    db.commit()

                    sandbox_job_id = sandbox_job.id
                    fine_tuned_run_id = fine_tuned_run.id
                    persisted_training_run_id = str(fine_tuned_run.id)
                except HTTPException:
                    db.rollback()
                    raise
                except Exception as init_exc:
                    db.rollback()
                    raise HTTPException(
                        status_code=500,
                        detail=f"Failed to initialize fine-tune sandbox run: {init_exc}",
                    )
                finally:
                    db.close()

                if sandbox_job_id is None or fine_tuned_run_id is None:
                    raise HTTPException(status_code=500, detail="Failed to queue fine-tune sandbox job.")

                try:
                    sandbox_metrics = _poll_sandbox_job_for_fine_tune(
                        sandbox_job_id,
                        timeout_seconds=FINE_TUNE_SANDBOX_TIMEOUT_SECONDS + 120,
                    )

                    fine_tuned_metrics = {
                        "name": canonical_model_name,
                        "rmse": float(sandbox_metrics.get("rmse")),
                        "mae": float(sandbox_metrics.get("mae")),
                        "r2": float(sandbox_metrics.get("r2")),
                    }

                    finalize_db = SessionLocal()
                    try:
                        run_row = finalize_db.query(TrainingRun).filter(TrainingRun.id == fine_tuned_run_id).first()
                        if run_row is None:
                            raise RuntimeError("Fine-tune training run not found during finalize")

                        run_row.status = "completed"
                        run_row.stage = "fine_tuned"
                        run_row.progress = 100
                        run_row.best_model_name = fine_tuned_metrics["name"]
                        run_row.rmse = fine_tuned_metrics["rmse"]
                        run_row.mae = fine_tuned_metrics["mae"]
                        run_row.r2 = fine_tuned_metrics["r2"]
                        run_row.completed_at = datetime.utcnow()

                        finalize_db.add(
                            ModelVersion(
                                training_run_id=run_row.id,
                                model_name=fine_tuned_metrics["name"],
                                rmse=fine_tuned_metrics["rmse"],
                                mae=fine_tuned_metrics["mae"],
                                r2=fine_tuned_metrics["r2"],
                                hyperparameters=tuned_hyperparameters,
                                rank_position=1,
                            )
                        )
                        finalize_db.commit()
                    except Exception as finalize_exc:
                        finalize_db.rollback()
                        raise RuntimeError(f"Fine-tune finalize failed: {finalize_exc}")
                    finally:
                        finalize_db.close()
                except Exception as sandbox_exc:
                    failure_db = SessionLocal()
                    try:
                        run_row = failure_db.query(TrainingRun).filter(TrainingRun.id == fine_tuned_run_id).first()
                        if run_row is not None:
                            run_row.status = "failed"
                            run_row.stage = "fine_tuned"
                            run_row.progress = 100
                            run_row.error_message = str(sandbox_exc)
                            run_row.completed_at = datetime.utcnow()
                            failure_db.commit()
                    except Exception:
                        failure_db.rollback()
                    finally:
                        failure_db.close()

                    raise HTTPException(status_code=500, detail=str(sandbox_exc))
            else:
                # Non-sandbox path: derive hyperparameters and run training
                tuned_hyperparameters = _derive_fine_tune_hyperparameters(
                    canonical_model_name,
                    request.llm_provider,
                    {
                        "rmse": baseline_rmse,
                        "mae": baseline_mae,
                        "r2": baseline_r2,
                        "improvement": 0.0,
                    },
                    request.prompt,
                )

                # Capture tuned_hyperparameters in closure
                hp_copy = dict(tuned_hyperparameters)
                fine_tuned_metrics = execute_training_with_timeout(
                    lambda: fine_tune_best_model(
                        file_path,
                        request.target_column,
                        canonical_model_name,
                        request.llm_provider,
                        hp_copy,
                    )
                )

                db = SessionLocal()
                try:
                    project = db.query(Project).filter(Project.file_id == request.file_id).first()
                    if project is None:
                        raise HTTPException(
                            status_code=404,
                            detail=f"Project not found for file '{request.file_id}'."
                        )

                    latest_db_version = (
                        db.query(func.max(TrainingRun.version_number))
                        .filter(TrainingRun.project_id == project.id)
                        .scalar()
                    )
                    new_version = int(latest_db_version or 0) + 1

                    now_utc = datetime.utcnow()
                    fine_tuned_run = TrainingRun(
                        project_id=project.id,
                        version_number=new_version,
                        status="completed",
                        stage="fine_tuned",
                        progress=100,
                        best_model_name=fine_tuned_metrics["name"],
                        rmse=float(fine_tuned_metrics["rmse"]),
                        mae=float(fine_tuned_metrics["mae"]),
                        r2=float(fine_tuned_metrics["r2"]),
                        started_at=now_utc,
                        completed_at=now_utc,
                    )
                    db.add(fine_tuned_run)
                    db.flush()

                    db.add(
                        ModelVersion(
                            training_run_id=fine_tuned_run.id,
                            model_name=fine_tuned_metrics["name"],
                            rmse=float(fine_tuned_metrics["rmse"]),
                            mae=float(fine_tuned_metrics["mae"]),
                            r2=float(fine_tuned_metrics["r2"]),
                            hyperparameters=tuned_hyperparameters,
                            rank_position=1,
                        )
                    )
                    db.commit()
                    persisted_training_run_id = str(fine_tuned_run.id)
                except HTTPException:
                    db.rollback()
                    raise
                except Exception as persist_exc:
                    db.rollback()
                    raise HTTPException(
                        status_code=500,
                        detail=f"Failed to persist fine-tuned version: {persist_exc}",
                    )
                finally:
                    db.close()

            # Create version entry with only the fine-tuned model
            fine_tuned_metrics["hyperparameters"] = tuned_hyperparameters
            version_entry = {
                "version": new_version,
                "models": [fine_tuned_metrics],
                "best_model": fine_tuned_metrics,
                "training_run_id": persisted_training_run_id,
            }

            # Store in history
            TRAINING_HISTORY[request.file_id].append(version_entry)

            # Calculate improvements
            rmse_change = fine_tuned_metrics["rmse"] - baseline_rmse
            r2_change = fine_tuned_metrics["r2"] - baseline_r2

            # Return comparison response
            response = {
                "previous_version": previous_version,
                "new_version": new_version,
                "before": {
                    "name": canonical_model_name,
                    "rmse": baseline_rmse,
                    "mae": baseline_mae,
                    "r2": baseline_r2,
                },
                "after": {
                    "name": fine_tuned_metrics["name"],
                    "rmse": fine_tuned_metrics["rmse"],
                    "mae": fine_tuned_metrics["mae"],
                    "r2": fine_tuned_metrics["r2"],
                },
                "improvement": {
                    "rmse_change": float(rmse_change),
                    "r2_change": float(r2_change),
                },
                "training_run_id": persisted_training_run_id,
            }

            return JSONResponse(status_code=200, content=response)
            
        except HTTPException:
            raise
        except ValueError as e:
            # Validation errors
            raise HTTPException(
                status_code=400,
                detail=str(e)
            )
        except Exception as e:
            # Model training errors
            raise HTTPException(
                status_code=500,
                detail=f"Fine-tune execution failed: {e}"
            )
            
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Fine-tune request failed: {e}"
        )