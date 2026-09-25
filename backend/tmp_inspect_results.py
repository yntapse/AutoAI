"""Post-run DB inspection for verification table."""
import sys
sys.path.insert(0, '.')
from database import engine
from sqlalchemy import text
import json

AGENT_ID = "70925e7f-430a-41a3-bcd3-2057cb4767d5"

with engine.connect() as conn:
    # Check 6: sandbox job states
    jobs = conn.execute(text(
        "SELECT id, iteration_number, status, created_at, started_at, completed_at "
        "FROM sandbox_jobs WHERE agent_id = :aid ORDER BY iteration_number"
    ), {"aid": AGENT_ID}).fetchall()

    print("=== SANDBOX JOBS ===")
    for j in jobs:
        created = str(j[3])[:19] if j[3] else "None"
        started = str(j[4])[:19] if j[4] else "None"
        completed = str(j[5])[:19] if j[5] else "None"
        print(f"  iter={j[1]:>2}  status={j[2]:<12}  created={created}  started={started}  completed={completed}")
    print(f"  TOTAL: {len(jobs)} jobs")

    # Check 7: contract validation fallback - look at training runs for error/fallback markers
    runs = conn.execute(text(
        "SELECT version_number, status, rmse, mae, r2, best_model_name "
        "FROM training_runs WHERE agent_run_id = :aid ORDER BY version_number"
    ), {"aid": AGENT_ID}).fetchall()

    print("\n=== TRAINING RUNS ===")
    for r in runs:
        print(f"  v={r[0]:>2}  status={r[1]:<12}  rmse={r[2]}  mae={r[3]}  r2={r[4]}  model={r[5]}")
    print(f"  TOTAL: {len(runs)} runs")

    # Check 8: best RMSE in training_runs
    rmse_values = [r[2] for r in runs if r[2] is not None]
    best_rmse = min(rmse_values) if rmse_values else None
    print(f"\n=== BEST RMSE: {best_rmse} ===")

    # Check: agent_run final state with best_training_run_id
    agent = conn.execute(text(
        "SELECT a.status, a.current_iteration, t.rmse as best_rmse, t.version_number as best_version "
        "FROM agent_runs a "
        "LEFT JOIN training_runs t ON t.id = a.best_training_run_id "
        "WHERE a.id = :aid"
    ), {"aid": AGENT_ID}).fetchone()
    if agent:
        print(f"\n=== AGENT RUN ===")
        print(f"  status={agent[0]}  current_iteration={agent[1]}  best_rmse={agent[2]}  best_version={agent[3]}")

    # Check 7: fallback indicators — training runs with error messages or non-standard path
    runs_with_errors = [r for r in runs if r[1] == 'failed' or (len(r) > 6 and r[6])]
    print(f"\n=== CONTRACT/FALLBACK CHECK ===")
    print(f"  Failed training runs: {sum(1 for r in runs if r[1] == 'failed')}")
    completed_runs = sum(1 for r in runs if r[1] == 'completed')
    print(f"  Completed training runs: {completed_runs}")

    # Check 6 details: verify state transitions
    completed_count = sum(1 for j in jobs if j[2] == 'completed')
    failed_count = sum(1 for j in jobs if j[2] == 'failed')
    timeout_count = sum(1 for j in jobs if j[2] == 'timeout')
    print(f"\n=== JOB STATE SUMMARY ===")
    print(f"  completed={completed_count}  failed={failed_count}  timeout={timeout_count}")
    print(f"  All jobs with started_at set: {sum(1 for j in jobs if j[4] is not None)}/{len(jobs)}")
    print(f"  All jobs with completed_at set: {sum(1 for j in jobs if j[5] is not None)}/{len(jobs)}")
