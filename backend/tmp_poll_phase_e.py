"""Poll agent status for the latest two agents."""
from database import engine
from sqlalchemy import text

with engine.connect() as c:
    rows = c.execute(text(
        "SELECT id, status, current_iteration, max_iterations "
        "FROM agent_runs ORDER BY created_at DESC LIMIT 2"
    )).fetchall()
    for r in rows:
        print(f"AGENT: id={str(r[0])[:8]}... status={r[1]} iter={r[2]}/{r[3]}")

    mem_count = c.execute(text("SELECT count(*) FROM experiment_memory")).fetchone()[0]
    print(f"EXPERIMENT_MEMORY_COUNT: {mem_count}")

    # Check recent sandbox jobs
    jobs = c.execute(text(
        "SELECT agent_id, iteration_number, status "
        "FROM sandbox_jobs ORDER BY created_at DESC LIMIT 10"
    )).fetchall()
    for j in jobs:
        print(f"  JOB: agent={str(j[0])[:8]}... iter={j[1]} status={j[2]}")
