from typing import Optional

from sqlalchemy import text
from sqlalchemy.orm import Session

from models.sandbox_job import SandboxJob


def claim_next_sandbox_job(db: Session) -> Optional[SandboxJob]:
    try:
        claim_result = db.execute(
            text(
                """
                WITH next_job AS (
                    SELECT id
                    FROM sandbox_jobs
                    WHERE status = 'queued'
                    ORDER BY created_at ASC
                    FOR UPDATE SKIP LOCKED
                    LIMIT 1
                )
                UPDATE sandbox_jobs AS sj
                SET
                    status = 'running',
                    started_at = timezone('utc', now())
                FROM next_job
                WHERE sj.id = next_job.id
                RETURNING sj.id
                """
            )
        ).first()

        if claim_result is None:
            db.commit()
            return None

        claimed_job = (
            db.query(SandboxJob)
            .filter(SandboxJob.id == claim_result.id)
            .first()
        )
        if claimed_job is not None:
            db.expunge(claimed_job)

        db.commit()
        return claimed_job
    except Exception:
        db.rollback()
        raise