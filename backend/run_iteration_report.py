"""
run_iteration_report.py
=======================
Uploads house_price_demo.csv, starts a 6-iteration autonomous agent run,
polls until complete, then prints a detailed per-iteration report showing:
  - Which model improved / what RMSE
  - Context shared by the Architect (blueprint, error type)
  - Telemetry: architect OK / generator OK / fallback used
  - What changed vs previous iteration
"""

import json
import sys
import time
from pathlib import Path

import requests

BASE = "http://127.0.0.1:8000"
DATASET = Path(__file__).parent / "uploads" / "house_price_demo.csv"
TARGET_COLUMN = "price"
MAX_ITERATIONS = 6
POLL_INTERVAL = 4  # seconds


# ── 1. Create project (upload CSV) ──────────────────────────────────────────

print("=" * 70)
print("STEP 1 — Uploading dataset and creating project")
print("=" * 70)

with open(DATASET, "rb") as f:
    resp = requests.post(
        f"{BASE}/projects/create",
        data={"project_name": "HousePrice-Test-Run", "target_column": TARGET_COLUMN},
        files={"file": ("house_price_demo.csv", f, "text/csv")},
        timeout=30,
    )

if resp.status_code not in (200, 201):
    print(f"ERROR: project create failed {resp.status_code}: {resp.text}")
    sys.exit(1)

project = resp.json()
file_id = project.get("file_id") or project.get("id")
print(f"  Project created: file_id={file_id}")
print(f"  Rows: {project.get('num_rows')}  Columns: {project.get('num_features')}")
print(f"  Target: {TARGET_COLUMN}\n")


# ── 2. Start agent ──────────────────────────────────────────────────────────

print("=" * 70)
print("STEP 2 — Starting autonomous agent (6 iterations)")
print("=" * 70)

resp = requests.post(
    f"{BASE}/agent/start-by-file",
    json={
        "file_id": file_id,
        "target_column": TARGET_COLUMN,
        "max_iterations": MAX_ITERATIONS,
        "improvement_threshold": 0.001,
    },
    timeout=30,
)

if resp.status_code not in (200, 201):
    print(f"ERROR: agent start failed {resp.status_code}: {resp.text}")
    sys.exit(1)

agent_id = resp.json().get("agent_run_id") or resp.json().get("job_id")
print(f"  Agent started: {agent_id}\n")


# ── 3. Poll until done ───────────────────────────────────────────────────────

print("=" * 70)
print("STEP 3 — Polling agent (live summary every iteration change)")
print("=" * 70)

# Track state across polls
prev_iteration = 0
seen_log_count = 0
iteration_snapshots: dict = {}  # iteration → {models, logs}
final_status = None

while True:
    try:
        resp = requests.get(
            f"{BASE}/agent/status/{agent_id}?ts={int(time.time()*1000)}",
            headers={"Cache-Control": "no-cache"},
            timeout=15,
        )
    except requests.RequestException as e:
        print(f"  (poll error: {e}) — retrying in {POLL_INTERVAL}s")
        time.sleep(POLL_INTERVAL)
        continue

    if resp.status_code != 200:
        print(f"  Status poll error {resp.status_code}, retrying…")
        time.sleep(POLL_INTERVAL)
        continue

    status = resp.json()
    current_iter = status.get("current_iteration", 0)
    agent_status = status.get("status", "unknown")
    logs: list = status.get("logs", [])
    models: list = status.get("models_in_progress", [])

    # Print new log lines
    new_logs = logs[seen_log_count:]
    for line in new_logs:
        print(f"  LOG | {line}")
    seen_log_count = len(logs)

    # Snapshot when iteration advances
    if current_iter > prev_iteration:
        iteration_snapshots[current_iter] = {
            "models": [dict(m) for m in models],
            "log_slice": list(logs),
        }
        prev_iteration = current_iter
        best_rmse = status.get("best_rmse")
        print(f"\n  ── Iteration {current_iter} / {MAX_ITERATIONS} snapshot "
              f"| best_rmse={best_rmse} ──\n")

    if agent_status in ("completed", "failed"):
        final_status = status
        break

    time.sleep(POLL_INTERVAL)

# Final snapshot
iteration_snapshots[prev_iteration] = {
    "models": [dict(m) for m in final_status.get("models_in_progress", [])],
    "log_slice": final_status.get("logs", []),
}

print(f"\n  Agent finished with status: {final_status.get('status')}")
print(f"  Best RMSE: {final_status.get('best_rmse')}")
print(f"  Best model: {final_status.get('best_model_name')}")


# ── 4. Build per-iteration report ────────────────────────────────────────────

def extract_architect_lines(logs: list, iteration: int) -> list:
    """Pull Architect/Telemetry/Error-context/Compile-gate lines for this iteration."""
    keywords = ["architect:", "blueprint", "telemetry [", "error context", "compile gate",
                 "single-model gate", "fallback"]
    results = []
    in_iter = False
    for line in logs:
        line_lower = line.lower()
        # crude iteration boundary detection
        if f"iteration {iteration}" in line_lower or f"iter {iteration}" in line_lower:
            in_iter = True
        if in_iter and any(k in line_lower for k in keywords):
            results.append(line)
    return results


def parse_model_rmse(models: list) -> dict:
    return {m["name"]: m.get("rmse") for m in models if m.get("rmse") is not None}


print("\n\n")
print("=" * 70)
print("FULL ITERATION REPORT")
print("=" * 70)

all_logs = final_status.get("logs", [])
prev_rmse_map: dict = {}

for it in range(1, MAX_ITERATIONS + 1):
    snap = iteration_snapshots.get(it)
    print(f"\n{'─'*70}")
    print(f"  ITERATION {it}")
    print(f"{'─'*70}")

    if snap is None:
        print("  (no snapshot captured)")
        continue

    # Model results
    rmse_map = parse_model_rmse(snap["models"])
    print("  Model Results:")
    for name, rmse in rmse_map.items():
        prev = prev_rmse_map.get(name)
        delta = ""
        if prev is not None and rmse is not None:
            diff = prev - rmse
            delta = f"  ({'↓ improved' if diff > 0 else '↑ degraded'} by {abs(diff):.4f})"
        print(f"    {name:30s}  RMSE={rmse:.4f if rmse else 'N/A'}{delta}")
    prev_rmse_map = {**prev_rmse_map, **rmse_map}

    # Pluck architect/telemetry/error lines from the full log
    arch_lines = [
        l for l in all_logs
        if any(k in l.lower() for k in [
            "architect:", "blueprint", "telemetry [", "error context captured",
            "compile gate", "single-model gate", "fallback blueprint"
        ])
    ]
    if arch_lines:
        print("\n  Architect / Telemetry Events:")
        for l in arch_lines:
            print(f"    {l}")

    # RMSE-change summary for this iteration
    if rmse_map:
        best_this = min(
            (v for v in rmse_map.values() if v is not None), default=None
        )
        if best_this:
            print(f"\n  Best RMSE this iteration: {best_this:.4f}")


# Summary table
print(f"\n\n{'='*70}")
print("SUMMARY TABLE")
print(f"{'='*70}")
print(f"{'Iteration':<12} {'Best RMSE':>12} {'Best Model':<30}")
print("-" * 55)

all_models_final = final_status.get("models_in_progress", [])
model_rmses = sorted(
    [(m["name"], m["rmse"]) for m in all_models_final if m.get("rmse") is not None],
    key=lambda x: x[1],
)
for name, rmse in model_rmses:
    print(f"  {'final':<10} {rmse:>12.4f}  {name}")

print(f"\nOverall Best: {final_status.get('best_model_name')} "
      f"| RMSE={final_status.get('best_rmse')}")
print("=" * 70)
