from datetime import datetime, timezone
from database import SessionLocal
from models.project import Project
from models.agent_run import AgentRun
from models.training_run import TrainingRun
from models.sandbox_job import SandboxJob

db=SessionLocal()
try:
    now=datetime.now(timezone.utc)
    n=0
    for j in db.query(SandboxJob).filter(SandboxJob.status.in_(['queued','running'])).all():
        j.status='failed'
        if not j.error_log:
            j.error_log='Cancelled stale job before rerun'
        j.completed_at=now
        n+=1
    db.commit()
    print('cleared', n)
finally:
    db.close()
