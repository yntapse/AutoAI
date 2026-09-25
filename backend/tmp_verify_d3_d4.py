import sys
import json
sys.path.insert(0, '.')
from sqlalchemy import text
from database import engine

with engine.connect() as conn:
    latest_agent = conn.execute(text(
        "SELECT id, project_id, created_at FROM agent_runs ORDER BY created_at DESC LIMIT 1"
    )).fetchone()
    if not latest_agent:
        print("NO_AGENT")
        raise SystemExit(0)

    agent_id = latest_agent[0]
    project_id = latest_agent[1]
    print(f"AGENT_ID={agent_id}")
    print(f"PROJECT_ID={project_id}")

    profile = conn.execute(text(
        "SELECT profile_json FROM dataset_profiles WHERE project_id = :pid"
    ), {"pid": project_id}).fetchone()

    has_profile = bool(profile and isinstance(profile[0], dict) and profile[0])
    print(f"HAS_DATASET_PROFILE={has_profile}")
    if has_profile:
        p = profile[0]
        print("PROFILE_KEYS=", sorted(list(p.keys())))
        print("PROFILE_ROWS=", p.get("rows"))
        print("PROFILE_FEATURES=", p.get("num_features"))
        print("PROFILE_MISSING_RATIO=", p.get("missing_value_ratio"))

    job_rows = conn.execute(text(
        "SELECT iteration_number, status, result_json FROM sandbox_jobs WHERE agent_id = :aid ORDER BY iteration_number"
    ), {"aid": agent_id}).fetchall()
    print(f"SANDBOX_JOBS={len(job_rows)}")

    for r in job_rows:
        payload = r[2]
        if isinstance(payload, str):
            try:
                payload = json.loads(payload)
            except Exception:
                payload = None
        system_result = payload.get('result_json') if isinstance(payload, dict) else None
        item = {
            "iteration": r[0],
            "status": r[1],
            "has_result_json": isinstance(system_result, dict),
            "has_rmse_holdout": isinstance(system_result, dict) and ("rmse_holdout" in system_result),
            "has_rmse_cv": isinstance(system_result, dict) and ("rmse_cv" in system_result),
        }
        print(json.dumps(item))

    script_rows = conn.execute(text(
        "SELECT iteration_number, script_content FROM sandbox_jobs WHERE agent_id = :aid ORDER BY iteration_number"
    ), {"aid": agent_id}).fetchall()
    fs_markers = {
        "feature_selection_method": 0,
        "select_k_best": 0,
        "variance_filter": 0,
        "tree_importance": 0,
    }
    for r in script_rows:
        script = r[1] or ""
        for marker in list(fs_markers.keys()):
            if marker in script:
                fs_markers[marker] += 1
    print("FS_MARKERS=", fs_markers)
