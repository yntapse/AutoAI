"""Verify experiment_memory table exists and has expected columns."""
from database import engine
from sqlalchemy import text

with engine.connect() as c:
    rows = c.execute(text(
        "SELECT column_name, data_type FROM information_schema.columns "
        "WHERE table_name = 'experiment_memory' ORDER BY ordinal_position"
    )).fetchall()
    if rows:
        print("experiment_memory TABLE EXISTS")
        for r in rows:
            print(f"  {r[0]:30s} {r[1]}")
    else:
        print("experiment_memory TABLE MISSING")
