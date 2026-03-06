"""
Monitor parallel execution in real-time
"""
import time
from database import SessionLocal
from models.sandbox_job import SandboxJob
from models.agent_run import AgentRun
from sqlalchemy import desc
from datetime import datetime, timezone, timedelta

def monitor_parallel_execution():
    print("\n" + "="*80)
    print("PARALLEL EXECUTION MONITOR")
    print("="*80)
    
    db = SessionLocal()
    try:
        # Get the most recent agent run
        recent_agent = db.query(AgentRun).order_by(desc(AgentRun.started_at)).first()
        
        if not recent_agent:
            print("\n✗ No agent runs found")
            return
        
        print(f"\nAgent Run ID: {recent_agent.id}")
        print(f"Status: {recent_agent.status}")
        print(f"Current Iteration: {recent_agent.current_iteration}/{recent_agent.max_iterations}")
        print(f"Started: {recent_agent.started_at}")
        
        # Get recent sandbox jobs for this agent
        recent_time = datetime.now(timezone.utc) - timedelta(minutes=30)
        jobs = (
            db.query(SandboxJob)
            .filter(
                SandboxJob.agent_id == recent_agent.id,
                SandboxJob.created_at >= recent_time
            )
            .order_by(SandboxJob.created_at.desc())
            .limit(20)
            .all()
        )
        
        if not jobs:
            print("\n✗ No recent sandbox jobs found")
            return
        
        print(f"\n{'='*80}")
        print(f"RECENT SANDBOX JOBS (Last {len(jobs)} jobs)")
        print(f"{'='*80}\n")
        
        # Group by iteration
        jobs_by_iteration = {}
        for job in reversed(jobs):
            iter_num = job.iteration_number
            if iter_num not in jobs_by_iteration:
                jobs_by_iteration[iter_num] = []
            jobs_by_iteration[iter_num].append(job)
        
        # Display each iteration
        for iter_num in sorted(jobs_by_iteration.keys()):
            iter_jobs = jobs_by_iteration[iter_num]
            print(f"\n{'─'*80}")
            print(f"ITERATION {iter_num} - {len(iter_jobs)} models")
            print(f"{'─'*80}")
            
            statuses = {
                'queued': 0,
                'running': 0,
                'completed': 0,
                'failed': 0,
                'timeout': 0
            }
            
            for job in iter_jobs:
                status = job.status
                statuses[status] = statuses.get(status, 0) + 1
                
                # Extract model name from script if possible
                model_name = "Unknown"
                if job.script_content:
                    for line in job.script_content.split('\n')[:50]:
                        if 'model = ' in line:
                            if 'LinearRegression()' in line:
                                model_name = 'LinearRegression'
                            elif 'Ridge(' in line:
                                model_name = 'Ridge'
                            elif 'Lasso(' in line:
                                model_name = 'Lasso'
                            elif 'ElasticNet(' in line:
                                model_name = 'ElasticNet'
                            elif 'RandomForestRegressor(' in line:
                                model_name = 'RandomForestRegressor'
                            elif 'XGBRegressor(' in line:
                                model_name = 'XGBRegressor'
                            break
                
                # Get RMSE if available
                rmse_str = "N/A"
                if job.result_json and isinstance(job.result_json, dict):
                    result = job.result_json.get('result_json', {})
                    if isinstance(result, dict):
                        rmse = result.get('rmse_cv') or result.get('rmse_holdout') or result.get('rmse')
                        if rmse is not None:
                            rmse_str = f"{float(rmse):.4f}"
                
                status_icon = {
                    'queued': '⏳',
                    'running': '🔄',
                    'completed': '✓',
                    'failed': '✗',
                    'timeout': '⏱'
                }.get(status, '?')
                
                status_color = {
                    'queued': '\033[93m',      # Yellow
                    'running': '\033[94m',     # Blue
                    'completed': '\033[92m',   # Green
                    'failed': '\033[91m',      # Red
                    'timeout': '\033[91m'      # Red
                }.get(status, '\033[0m')
                
                reset_color = '\033[0m'
                
                print(f"  {status_icon} {status_color}{status:10s}{reset_color} | "
                      f"{model_name:25s} | RMSE: {rmse_str:10s} | "
                      f"Job: {str(job.id)[:8]}...")
            
            # Summary
            print(f"\n  Summary: ", end="")
            summary_parts = []
            if statuses['completed']:
                summary_parts.append(f"\033[92m{statuses['completed']} completed\033[0m")
            if statuses['running']:
                summary_parts.append(f"\033[94m{statuses['running']} running\033[0m")
            if statuses['queued']:
                summary_parts.append(f"\033[93m{statuses['queued']} queued\033[0m")
            if statuses['failed']:
                summary_parts.append(f"\033[91m{statuses['failed']} failed\033[0m")
            if statuses['timeout']:
                summary_parts.append(f"\033[91m{statuses['timeout']} timeout\033[0m")
            
            print(" | ".join(summary_parts))
        
        print(f"\n{'='*80}\n")
        
    finally:
        db.close()

if __name__ == "__main__":
    monitor_parallel_execution()
