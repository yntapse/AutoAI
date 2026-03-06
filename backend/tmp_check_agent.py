from database import SessionLocal
from models.agent_run import AgentRun
from models.training_run import TrainingRun
from models.sandbox_job import SandboxJob


def main() -> None:
    db = SessionLocal()
    try:
        agent = (
            db.query(AgentRun)
            .filter(AgentRun.status == "completed", AgentRun.current_iteration >= 6)
            .order_by(AgentRun.started_at.desc())
            .first()
        )
        if not agent:
            print("agent", None)
            return

        aid = str(agent.id)
        print("agent", aid, agent.status, agent.current_iteration, agent.error_message)

        runs = (
            db.query(TrainingRun)
            .filter(TrainingRun.agent_run_id == aid)
            .order_by(TrainingRun.version_number.asc())
            .all()
        )
        print("training_runs", len(runs))
        for run in runs:
            print("run", run.version_number, run.status, run.rmse, (run.error_message or "")[:260])

        jobs = (
            db.query(SandboxJob)
            .filter(SandboxJob.agent_id == aid)
            .order_by(SandboxJob.iteration_number.asc())
            .all()
        )
        print("jobs", len(jobs))
        for job in jobs:
            print("job", job.iteration_number, job.status, (job.error_log or "")[:220], "result_json=", job.result_json)
    finally:
        db.close()


if __name__ == "__main__":
    main()
from database import SessionLocal
from models.project import Project
from models.agent_run import AgentRun
from models.training_run import TrainingRun
from models.sandbox_job import SandboxJob

aid='67ae8a0f-1bf5-4fbf-aecf-6e1412ad24f8'
db=SessionLocal()
try:
    a=db.query(AgentRun).filter(AgentRun.id==aid).first()
    print('agent', a.status if a else None, a.current_iteration if a else None, a.error_message if a else None)
    trs=db.query(TrainingRun).filter(TrainingRun.agent_run_id==aid).order_by(TrainingRun.version_number.asc()).all()
    print('training_runs', len(trs))
    for t in trs:
        print('run', t.version_number, t.status, t.rmse, (t.error_message or '')[:220])
    jobs=db.query(SandboxJob).filter(SandboxJob.agent_id==aid).order_by(SandboxJob.iteration_number.asc()).all()
    print('jobs', len(jobs))
    for j in jobs:
        print('job', j.iteration_number, j.status, (j.error_log or '')[:180], 'result_json=', j.result_json)
finally:
    db.close()
