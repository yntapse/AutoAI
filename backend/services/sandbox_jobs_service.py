from datetime import datetime, timezone
from typing import Optional

from sqlalchemy.orm import Session

from models.sandbox_job import SandboxJob


def claim_next_sandbox_job(db: Session) -> Optional[SandboxJob]:
    try:
        job = (
            db.query(SandboxJob)
            .filter(SandboxJob.status == "queued")
            .order_by(SandboxJob.created_at.asc())
            .with_for_update(skip_locked=True)
            .first()
        )

        if job is None:
            return None

        job.status = "running"
        job.started_at = datetime.now(timezone.utc)
        db.flush()
        db.expunge(job)
        db.commit()
        return job
    except Exception:
        db.rollback()
        raise