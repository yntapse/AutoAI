import re
import sys

sys.path.insert(0, '.')
from database import engine
from sqlalchemy import text


def extract_model_family(script_content: str) -> str:
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

    return selected or "unknown"


with engine.connect() as conn:
    agent_row = conn.execute(text("SELECT id FROM agent_runs ORDER BY created_at DESC LIMIT 1")).fetchone()
    if not agent_row:
        print("NO_AGENT")
        raise SystemExit(0)

    aid = str(agent_row[0])
    job_rows = conn.execute(
        text("SELECT iteration_number, script_content FROM sandbox_jobs WHERE agent_id=:aid ORDER BY iteration_number"),
        {"aid": aid},
    ).fetchall()

    model_families = [extract_model_family((row[1] or "")) for row in job_rows]
    print(f"AGENT_ID={aid}")
    print(f"ITERATIONS={len(job_rows)}")
    print(f"MODEL_FAMILIES={model_families}")
