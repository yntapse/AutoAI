"""
run_demo_report.py
==================
Uploads house_price_demo.csv, starts the AutoAI agent for 6 iterations,
polls until complete, then prints a full per-iteration report showing:
  - which models ran
  - what error context the Architect used
  - architect blueprint (steps / hyperparams)
  - generator / validator outcome
  - RMSE results
  - what improved vs previous iteration

Usage:  python run_demo_report.py [--base-url http://127.0.0.1:8000]
"""

import argparse
import json
import os
import sys
import time
from pathlib import Path

import requests

# --------------------------------------------------------------------------
BASE_URL = "http://127.0.0.1:8000"
DATASET_PATH = Path(__file__).parent / "uploads" / "house_price_demo.csv"
TARGET_COLUMN = "price"
MAX_ITERATIONS = 6
POLL_INTERVAL = 5   # seconds between status polls
MAX_POLL_SEC = 900  # 15 min hard cap
# --------------------------------------------------------------------------


def upload_file(session: requests.Session) -> str:
    print(">>> Uploading dataset …")
    with open(DATASET_PATH, "rb") as fh:
        r = session.post(
            f"{BASE_URL}/upload",
            files={"file": (DATASET_PATH.name, fh, "text/csv")},
            timeout=30,
        )
    r.raise_for_status()
    data = r.json()
    fid = data.get("file_id") or data.get("id") or data.get("upload_id")
    if not fid:
        # try nested
        fid = (data.get("data") or {}).get("file_id")
    if not fid:
        print("Upload response:", json.dumps(data, indent=2))
        sys.exit("Could not extract file_id from upload response")
    print(f"    file_id = {fid}")
    return str(fid)


def create_project(session: requests.Session, file_id: str) -> str:
    print(">>> Creating project …")
    payload = {
        "name": "HousePriceDemo",
        "file_id": file_id,
        "target_column": TARGET_COLUMN,
    }
    r = session.post(f"{BASE_URL}/projects", json=payload, timeout=30)
    r.raise_for_status()
    data = r.json()
    pid = data.get("id") or data.get("project_id") or (data.get("data") or {}).get("id")
    if not pid:
        print("Project response:", json.dumps(data, indent=2))
        sys.exit("Could not extract project_id")
    print(f"    project_id = {pid}")
    return str(pid)


def start_agent(session: requests.Session, project_id: str) -> str:
    print(">>> Starting agent …")
    # Try /agent/start or /projects/{id}/train
    for endpoint in [
        f"{BASE_URL}/agent/start",
        f"{BASE_URL}/projects/{project_id}/train",
    ]:
        try:
            r = session.post(
                endpoint,
                json={"project_id": project_id, "max_iterations": MAX_ITERATIONS},
                timeout=30,
            )
            if r.status_code < 400:
                data = r.json()
                aid = (
                    data.get("agent_id")
                    or data.get("id")
                    or data.get("run_id")
                    or (data.get("data") or {}).get("agent_id")
                )
                if aid:
                    print(f"    agent_id = {aid}  (via {endpoint})")
                    return str(aid)
        except Exception:
            pass
    sys.exit("Could not start agent — is the backend running?")


def poll_until_done(session: requests.Session, agent_id: str) -> dict:
    print(f">>> Polling agent {agent_id}  (up to {MAX_POLL_SEC}s) …\n")

    seen_log_count = 0
    deadline = time.time() + MAX_POLL_SEC
    last_iter = 0

    while time.time() < deadline:
        try:
            r = session.get(
                f"{BASE_URL}/agent/status/{agent_id}",
                params={"ts": int(time.time() * 1000)},
                headers={"Cache-Control": "no-cache"},
                timeout=15,
            )
            r.raise_for_status()
        except Exception as exc:
            print(f"  [poll error: {exc}]  retrying …")
            time.sleep(POLL_INTERVAL)
            continue

        status = r.json()
        cur_iter = status.get("current_iteration", 0)
        logs: list = status.get("logs") or []

        # Print any new log lines
        new_logs = logs[seen_log_count:]
        for line in new_logs:
            print(f"  LOG | {line}")
        seen_log_count = len(logs)

        # Iteration boundary marker
        if cur_iter > last_iter:
            print(f"\n{'='*70}")
            print(f"  ITERATION {cur_iter} / {status.get('max_iterations', MAX_ITERATIONS)}")
            print(f"{'='*70}")
            last_iter = cur_iter

        agent_status = status.get("status", "")
        if agent_status in ("completed", "failed", "stopped"):
            print(f"\n>>> Agent finished with status: {agent_status}")
            return status

        time.sleep(POLL_INTERVAL)

    print("\n>>> TIMEOUT — agent did not finish in time")
    return {}


def fetch_history(session: requests.Session, agent_id: str) -> list:
    """Try to get iteration history if endpoint exists."""
    try:
        r = session.get(f"{BASE_URL}/agent/{agent_id}/history", timeout=15)
        if r.status_code == 200:
            return r.json()
    except Exception:
        pass
    return []


def build_report(final_status: dict, history: list, all_logs: list) -> str:
    lines = []
    lines.append("=" * 80)
    lines.append("  AUTOAI BUILDER — 6-ITERATION RUN REPORT")
    lines.append("  Dataset : house_price_demo.csv (300 rows, 7 features → price)")
    lines.append("=" * 80)

    # ── Per-iteration log analysis ──
    iter_buckets: dict[int, list] = {}
    current_i = 0
    for log in all_logs:
        low = log.lower()
        # detect iteration markers from log text
        import re as _re
        m = _re.search(r"iteration[:\s]+(\d+)", low)
        if m:
            current_i = int(m.group(1))
        if current_i not in iter_buckets:
            iter_buckets[current_i] = []
        iter_buckets[current_i].append(log)

    for it in sorted(iter_buckets.keys()):
        if it == 0:
            continue
        bucket = iter_buckets[it]
        lines.append(f"\n{'─'*80}")
        lines.append(f"  ITERATION {it}")
        lines.append(f"{'─'*80}")

        # Classify log lines into categories
        architect_lines = [l for l in bucket if "architect" in l.lower() or "blueprint" in l.lower()]
        telemetry_lines = [l for l in bucket if "telemetry" in l.lower()]
        error_lines     = [l for l in bucket if "error context" in l.lower() or "compile gate" in l.lower() or "failed" in l.lower()]
        rmse_lines      = [l for l in bucket if "rmse" in l.lower()]
        stage_lines     = [l for l in bucket if "stage:" in l.lower()]
        gate_lines      = [l for l in bucket if "gate" in l.lower() or "single-model" in l.lower()]

        if stage_lines:
            lines.append("\n  [Pipeline Stages]")
            for l in stage_lines:
                lines.append(f"    {l}")

        if architect_lines:
            lines.append("\n  [Architect Agent Activity]")
            for l in architect_lines:
                lines.append(f"    {l}")

        if error_lines:
            lines.append("\n  [Error Context Fed to Architect]")
            for l in error_lines:
                lines.append(f"    {l}")

        if gate_lines:
            lines.append("\n  [Single-Model Gate / Compile Gate]")
            for l in gate_lines:
                lines.append(f"    {l}")

        if rmse_lines:
            lines.append("\n  [Results / RMSE]")
            for l in rmse_lines:
                lines.append(f"    {l}")

        if telemetry_lines:
            lines.append("\n  [Architect Telemetry]")
            for l in telemetry_lines:
                lines.append(f"    {l}")

    # ── Final summary ──
    lines.append(f"\n{'='*80}")
    lines.append("  FINAL SUMMARY")
    lines.append(f"{'='*80}")
    best_rmse = final_status.get("best_rmse")
    best_model = final_status.get("best_model_name")
    total_iter = final_status.get("current_iteration", "?")
    lines.append(f"  Total iterations run : {total_iter}")
    lines.append(f"  Best model           : {best_model or 'N/A'}")
    lines.append(f"  Best RMSE            : {best_rmse or 'N/A'}")

    # Per-model RMSE ranking from last status
    models_in_progress = final_status.get("models_in_progress") or []
    if models_in_progress:
        ranked = sorted(
            [m for m in models_in_progress if m.get("rmse") is not None],
            key=lambda m: m["rmse"],
        )
        lines.append("\n  Model RMSE Ranking (lower = better):")
        for rank, m in enumerate(ranked, 1):
            lines.append(f"    {rank}. {m['name']:<28}  RMSE = {m['rmse']:.4f}")

    lines.append("")
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default=BASE_URL)
    args = parser.parse_args()
    global BASE_URL
    BASE_URL = args.base_url.rstrip("/")

    session = requests.Session()

    # ── 1. Upload ──
    file_id = upload_file(session)

    # ── 2. Create project ──
    project_id = create_project(session, file_id)

    # ── 3. Start agent ──
    agent_id = start_agent(session, project_id)

    # ── 4. Poll + stream logs ──
    final_status = poll_until_done(session, agent_id)

    # ── 5. Fetch all logs for report ──
    all_logs = final_status.get("logs") or []
    # Refetch for completeness
    try:
        r = session.get(
            f"{BASE_URL}/agent/status/{agent_id}",
            params={"ts": int(time.time() * 1000)},
            headers={"Cache-Control": "no-cache"},
            timeout=15,
        )
        all_logs = r.json().get("logs") or all_logs
        final_status = r.json()
    except Exception:
        pass

    history = fetch_history(session, agent_id)

    # ── 6. Build and print report ──
    report = build_report(final_status, history, all_logs)
    print("\n\n" + report)

    # Save to file
    report_path = Path(__file__).parent / "demo_run_report.txt"
    report_path.write_text(report, encoding="utf-8")
    print(f"\n>>> Report saved to: {report_path}")


if __name__ == "__main__":
    main()
