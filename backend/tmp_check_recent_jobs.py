from database import SessionLocal
from models.sandbox_job import SandboxJob
from sqlalchemy import desc

db = SessionLocal()
try:
    rows = db.query(SandboxJob).order_by(desc(SandboxJob.created_at)).limit(10).all()
    for row in rows:
        print(f"{row.id} | status={row.status} | iter={row.iteration_number} | started_at={row.started_at} | completed_at={row.completed_at}")
finally:
    db.close()
