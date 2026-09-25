"""Clear all stale queued/running/timeout jobs before controlled verification run."""
import sys
sys.path.insert(0, '.')
from database import engine
from sqlalchemy import text

with engine.connect() as conn:
    r1 = conn.execute(text(
        "UPDATE sandbox_jobs SET status='failed'"
        " WHERE status IN ('queued','running','timeout')"
    ))
    r2 = conn.execute(text(
        "UPDATE training_runs SET status='failed' WHERE status IN ('queued','running')"
    ))
    conn.commit()
    print(f'Cleared {r1.rowcount} stale sandbox jobs, {r2.rowcount} stale training runs')
