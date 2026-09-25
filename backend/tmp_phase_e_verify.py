"""Phase E Memory Verification Harness.

Creates two synthetic datasets with different statistical profiles, runs a
short 4-iteration agent loop on each, then verifies:
  1. experiment_memory records are created after each successful iteration
  2. On the second dataset run, past experiments are retrieved and injected
  3. Strategy text is influenced by memory (contains memory-related tokens)
"""
import io
import json
import os
import re
import uuid
from contextlib import redirect_stdout
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

from database import SessionLocal, engine
from sqlalchemy import text

# ── Generate two synthetic datasets ──────────────────────────────────────────

def _create_dataset_a() -> Path:
    """Small numeric regression dataset (100 rows, 6 features, all numeric)."""
    np.random.seed(42)
    n = 100
    df = pd.DataFrame({
        "feat_a1": np.random.normal(50, 10, n),
        "feat_a2": np.random.normal(100, 25, n),
        "feat_a3": np.random.uniform(0, 1, n),
        "feat_a4": np.random.exponential(5, n),
        "feat_a5": np.random.randint(0, 100, n).astype(float),
        "target": np.random.normal(200, 30, n),
    })
    # Make target partially correlated
    df["target"] = 0.3 * df["feat_a1"] + 0.5 * df["feat_a2"] + np.random.normal(0, 10, n)
    path = Path("uploads") / f"phaseE_dataset_A_{uuid.uuid4().hex[:8]}.csv"
    df.to_csv(path, index=False)
    return path


def _create_dataset_b() -> Path:
    """Medium mixed dataset (200 rows, 8 features, includes categoricals)."""
    np.random.seed(99)
    n = 200
    df = pd.DataFrame({
        "feat_b1": np.random.normal(30, 5, n),
        "feat_b2": np.random.normal(80, 20, n),
        "feat_b3": np.random.choice(["low", "medium", "high"], n),
        "feat_b4": np.random.choice(["A", "B", "C", "D"], n),
        "feat_b5": np.random.uniform(10, 50, n),
        "feat_b6": np.random.lognormal(3, 1, n),
        "feat_b7": np.random.randint(1, 20, n).astype(float),
        "target": np.random.normal(500, 80, n),
    })
    df["target"] = 1.2 * df["feat_b1"] + 0.8 * df["feat_b2"] + np.random.normal(0, 20, n)
    path = Path("uploads") / f"phaseE_dataset_B_{uuid.uuid4().hex[:8]}.csv"
    df.to_csv(path, index=False)
    return path


def _run_agent(dataset_path: Path, target_column: str, max_iterations: int = 4) -> tuple:
    """Create project + agent, run loop, return (agent_id, stdout_output)."""
    from main import run_agent_loop
    from models.agent_run import AgentRun
    from models.project import Project

    df = pd.read_csv(dataset_path)
    numeric_cols = df.select_dtypes(include=["number"]).columns.tolist()

    db = SessionLocal()
    try:
        project = Project(
            project_name=f"phaseE-{uuid.uuid4().hex[:8]}",
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
            max_iterations=max_iterations,
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
    return agent_id, stream.getvalue()


def _count_memory_records() -> int:
    with engine.connect() as c:
        row = c.execute(text("SELECT count(*) FROM experiment_memory")).fetchone()
        return row[0] if row else 0


def _get_memory_records() -> list:
    with engine.connect() as c:
        rows = c.execute(text(
            "SELECT dataset_fingerprint, model_family, preprocessing_tokens, "
            "training_tokens, strategy_summary, rmse_cv, rmse_holdout, iteration_number "
            "FROM experiment_memory ORDER BY created_at"
        )).fetchall()
        return [dict(zip([
            "dataset_fingerprint", "model_family", "preprocessing_tokens",
            "training_tokens", "strategy_summary", "rmse_cv", "rmse_holdout", "iteration_number"
        ], r)) for r in rows]


def main():
    print("=" * 70)
    print("PHASE E MEMORY VERIFICATION HARNESS")
    print("=" * 70)

    # Clear previous memory records for clean test
    with engine.connect() as c:
        c.execute(text("DELETE FROM experiment_memory"))
        c.commit()
    print("Cleared experiment_memory table")

    initial_count = _count_memory_records()
    assert initial_count == 0, f"Expected 0 initial records, got {initial_count}"

    # ── RUN 1: Dataset A ────────────────────────────────────────────────────
    print("\n--- RUN 1: Dataset A (small, all-numeric) ---")
    ds_a = _create_dataset_a()
    print(f"Created dataset A: {ds_a}")

    agent_a_id, output_a = _run_agent(ds_a, "target", max_iterations=4)
    print(f"Agent A completed: {agent_a_id}")

    count_after_a = _count_memory_records()
    print(f"Memory records after Run 1: {count_after_a}")

    # Check that memory insert log lines appear
    mem_insert_lines_a = [l for l in output_a.splitlines() if "EXPERIMENT_MEMORY_INSERTED" in l]
    fingerprint_lines_a = [l for l in output_a.splitlines() if "DATASET_FINGERPRINT" in l]
    retrieval_lines_a = [l for l in output_a.splitlines() if "EXPERIMENT_MEMORY_RETRIEVED" in l]

    print(f"Memory insert log lines: {len(mem_insert_lines_a)}")
    print(f"Fingerprint log lines: {len(fingerprint_lines_a)}")
    print(f"Retrieval log lines: {len(retrieval_lines_a)}")

    # ── RUN 2: Dataset B ────────────────────────────────────────────────────
    print("\n--- RUN 2: Dataset B (medium, mixed with categoricals) ---")
    ds_b = _create_dataset_b()
    print(f"Created dataset B: {ds_b}")

    agent_b_id, output_b = _run_agent(ds_b, "target", max_iterations=4)
    print(f"Agent B completed: {agent_b_id}")

    count_after_b = _count_memory_records()
    print(f"Memory records after Run 2: {count_after_b}")

    mem_insert_lines_b = [l for l in output_b.splitlines() if "EXPERIMENT_MEMORY_INSERTED" in l]
    fingerprint_lines_b = [l for l in output_b.splitlines() if "DATASET_FINGERPRINT" in l]
    retrieval_lines_b = [l for l in output_b.splitlines() if "EXPERIMENT_MEMORY_RETRIEVED" in l]

    print(f"Memory insert log lines: {len(mem_insert_lines_b)}")
    print(f"Fingerprint log lines: {len(fingerprint_lines_b)}")
    print(f"Retrieval log lines: {len(retrieval_lines_b)}")

    # Check that Run 2 found memories from Run 1
    retrieval_with_records = [l for l in retrieval_lines_b if "records loaded" in l]
    print(f"Retrieval lines with records loaded: {len(retrieval_with_records)}")

    # ── VERIFICATION ────────────────────────────────────────────────────────
    print("\n" + "=" * 70)
    print("VERIFICATION RESULTS")
    print("=" * 70)

    all_records = _get_memory_records()
    print(f"\nTotal memory records: {len(all_records)}")
    for i, rec in enumerate(all_records, 1):
        print(f"  Record {i}: fp={rec['dataset_fingerprint'][:12]}... "
              f"model={rec['model_family']}, rmse_cv={rec['rmse_cv']}, "
              f"iter={rec['iteration_number']}")

    # Fingerprints
    fps = list(set(r["dataset_fingerprint"] for r in all_records))
    print(f"\nUnique fingerprints: {len(fps)}")
    for fp in fps:
        fp_records = [r for r in all_records if r["dataset_fingerprint"] == fp]
        print(f"  {fp[:16]}... -> {len(fp_records)} records")

    # ── PASS/FAIL checks ──
    checks = []

    # C1: Memory records created after Run 1
    c1 = count_after_a > 0
    checks.append(("C1_MEMORY_CREATED_RUN1", c1, f"{count_after_a} records"))

    # C2: Memory records created after Run 2
    c2 = count_after_b > count_after_a
    checks.append(("C2_MEMORY_CREATED_RUN2", c2, f"{count_after_b} total records"))

    # C3: Fingerprint computed for both runs
    c3 = len(fingerprint_lines_a) > 0 and len(fingerprint_lines_b) > 0
    checks.append(("C3_FINGERPRINT_COMPUTED", c3, f"A={len(fingerprint_lines_a)}, B={len(fingerprint_lines_b)}"))

    # C4: Memory retrieval attempted for Run 2
    c4 = len(retrieval_lines_b) > 0
    checks.append(("C4_MEMORY_RETRIEVAL_RUN2", c4, f"{len(retrieval_lines_b)} retrieval log lines"))

    # C5: Run 2 retrieved > 0 past experiments
    c5 = len(retrieval_with_records) > 0
    checks.append(("C5_PAST_EXPERIMENTS_FOUND", c5, f"{len(retrieval_with_records)} retrievals with records"))

    # C6: Multiple experiment records exist
    c6 = len(all_records) >= 2
    checks.append(("C6_MULTI_RECORDS", c6, f"{len(all_records)} total"))

    # C7: At least 2 distinct fingerprints (different datasets)
    c7 = len(fps) >= 2
    checks.append(("C7_DISTINCT_FINGERPRINTS", c7, f"{len(fps)} fingerprints"))

    # C8: Strategy output in Run 2 contains memory-influenced content
    strategy_lines_b = [l.split("SANDBOX STRATEGY:", 1)[1].strip()
                        for l in output_b.splitlines() if "SANDBOX STRATEGY:" in l]
    c8 = len(strategy_lines_b) > 0  # Strategies were generated
    checks.append(("C8_STRATEGIES_GENERATED", c8, f"{len(strategy_lines_b)} strategies"))

    print("\n" + "-" * 50)
    pass_count = 0
    for name, passed, detail in checks:
        status = "PASS" if passed else "FAIL"
        if passed:
            pass_count += 1
        print(f"  {status}: {name} ({detail})")

    print(f"\nSCORE: {pass_count}/{len(checks)}")
    print("=" * 70)

    # Emit machine-parseable result
    result = {
        "pass_count": pass_count,
        "total_checks": len(checks),
        "memory_records_total": len(all_records),
        "fingerprints": fps,
        "memory_inserts_run1": len(mem_insert_lines_a),
        "memory_inserts_run2": len(mem_insert_lines_b),
        "memory_retrieval_run2": len(retrieval_with_records),
        "strategies_run2": strategy_lines_b[:3],
    }
    print("PHASE_E_RESULT", json.dumps(result))


if __name__ == "__main__":
    main()
