"""Run metrics aggregation. One implementation, three consumers.

The Pipeline Dashboard, the Pipeline List and the per-pipeline metrics endpoint
all report the same figures, so the arithmetic lives here once rather than
three times in the router.

PURE. Takes plain dicts, not ORM rows, so it tests without a database and the
router stays the only thing that knows about SQLAlchemy.
"""

from typing import Any, Dict, List, Optional, Sequence

# Terminal states, as routers/etl_copilot.py and the execution engine write them.
SUCCESS_STATES = ("completed", "succeeded", "success")
FAILURE_STATES = ("failed", "error", "cancelled")


def _is_success(status: Optional[str]) -> bool:
    return str(status or "").lower() in SUCCESS_STATES


def _is_failure(status: Optional[str]) -> bool:
    return str(status or "").lower() in FAILURE_STATES


def percentile(values: Sequence[float], fraction: float) -> Optional[float]:
    """Nearest-rank percentile. No numpy, no interpolation surprises."""
    numbers = sorted(value for value in values if value is not None)
    if not numbers:
        return None
    if len(numbers) == 1:
        return round(float(numbers[0]), 3)
    rank = max(0, min(len(numbers) - 1, round(fraction * (len(numbers) - 1))))
    return round(float(numbers[int(rank)]), 3)


def summarise(runs: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    """Aggregate one pipeline's runs.

    Each run: {status, started_at, completed_at, row_count, execution_time}.
    Runs are expected newest-first; `last_run` is taken from position 0.
    """
    rows = list(runs or [])
    total = len(rows)
    succeeded = sum(1 for run in rows if _is_success(run.get("status")))
    failed = sum(1 for run in rows if _is_failure(run.get("status")))
    running = total - succeeded - failed

    durations = [
        float(run["execution_time"])
        for run in rows
        if run.get("execution_time") is not None
    ]
    row_counts = [
        int(run["row_count"]) for run in rows if run.get("row_count") is not None
    ]

    return {
        "total_runs": total,
        "succeeded": succeeded,
        "failed": failed,
        "running": running,
        # Success rate counts only runs that finished. A pipeline with one
        # success and one still running is 100%, not 50% - otherwise the
        # number drops every time a run starts.
        "success_rate": (
            round(100.0 * succeeded / (succeeded + failed), 2)
            if (succeeded + failed)
            else None
        ),
        "avg_duration_seconds": (
            round(sum(durations) / len(durations), 3) if durations else None
        ),
        "p95_duration_seconds": percentile(durations, 0.95),
        "max_duration_seconds": round(max(durations), 3) if durations else None,
        "total_rows_processed": sum(row_counts) if row_counts else 0,
        "last_row_count": row_counts[0] if row_counts else None,
        "last_run": rows[0] if rows else None,
        "last_status": rows[0].get("status") if rows else None,
        "last_run_at": rows[0].get("started_at") if rows else None,
    }


def trend(runs: Sequence[Dict[str, Any]], limit: int = 20) -> List[Dict[str, Any]]:
    """Oldest-first series for the sparkline. Input is newest-first."""
    window = list(runs or [])[:limit]
    window.reverse()
    return [
        {
            "run_id": run.get("id"),
            "status": run.get("status"),
            "started_at": run.get("started_at"),
            "duration_seconds": run.get("execution_time"),
            "row_count": run.get("row_count"),
            "succeeded": _is_success(run.get("status")),
        }
        for run in window
    ]


def portfolio(per_pipeline: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    """Dashboard-level totals across every pipeline.

    Each entry: {"pipeline_id", "pipeline_name", "metrics": summarise(...)}.
    """
    entries = list(per_pipeline or [])
    totals = {
        "pipelines": len(entries),
        "total_runs": 0,
        "succeeded": 0,
        "failed": 0,
        "running": 0,
        "total_rows_processed": 0,
        "pipelines_never_run": 0,
        "pipelines_failing": 0,
    }
    durations: List[float] = []

    for entry in entries:
        metrics = entry.get("metrics") or {}
        totals["total_runs"] += metrics.get("total_runs") or 0
        totals["succeeded"] += metrics.get("succeeded") or 0
        totals["failed"] += metrics.get("failed") or 0
        totals["running"] += metrics.get("running") or 0
        totals["total_rows_processed"] += metrics.get("total_rows_processed") or 0
        if not metrics.get("total_runs"):
            totals["pipelines_never_run"] += 1
        if _is_failure(metrics.get("last_status")):
            totals["pipelines_failing"] += 1
        if metrics.get("avg_duration_seconds") is not None:
            durations.append(metrics["avg_duration_seconds"])

    finished = totals["succeeded"] + totals["failed"]
    totals["success_rate"] = (
        round(100.0 * totals["succeeded"] / finished, 2) if finished else None
    )
    totals["avg_duration_seconds"] = (
        round(sum(durations) / len(durations), 3) if durations else None
    )
    return totals
