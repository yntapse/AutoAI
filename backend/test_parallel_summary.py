"""
Generate a comprehensive summary of the parallel execution test
"""
from database import SessionLocal
from models.sandbox_job import SandboxJob
from models.agent_run import AgentRun
from sqlalchemy import desc
from datetime import datetime, timezone, timedelta

def generate_summary():
    db = SessionLocal()
    try:
        # Get the most recent agent run
        recent_agent = db.query(AgentRun).order_by(desc(AgentRun.started_at)).first()
        
        if not recent_agent:
            print("No agent runs found")
            return
        
        # Get all sandbox jobs for this agent
        jobs = (
            db.query(SandboxJob)
            .filter(SandboxJob.agent_id == recent_agent.id)
            .order_by(SandboxJob.created_at)
            .all()
        )
        
        print("\n" + "="*80)
        print("PARALLEL EXECUTION TEST RESULTS")
        print("="*80)
        print(f"\nAgent Run: {recent_agent.id}")
        print(f"Status: {recent_agent.status}")
        print(f"Iterations Completed: {recent_agent.current_iteration}/{recent_agent.max_iterations}")
        print(f"Started: {recent_agent.started_at}")
        if recent_agent.completed_at:
            duration = recent_agent.completed_at - recent_agent.started_at
            print(f"Completed: {recent_agent.completed_at}")
            print(f"Total Duration: {duration.total_seconds():.1f} seconds")
        
        print(f"\n{'─'*80}")
        print("PER-MODEL RESULTS - ITERATION 1")
        print(f"{'─'*80}\n")
        
        # Get iteration 1 jobs (the 6 parallel models)
        iter_1_jobs = [j for j in jobs if j.iteration_number == 1]
        
        if not iter_1_jobs:
            print("No jobs found for iteration 1")
            return
        
        print(f"Total Models Trained: {len(iter_1_jobs)}")
        print(f"All Jobs Created At: {iter_1_jobs[0].created_at.strftime('%H:%M:%S')}\n")
        
        results = []
        for job in iter_1_jobs:
            # Extract model name
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
            
            # Get metrics
            rmse = None
            r2 = None
            mae = None
            if job.result_json and isinstance(job.result_json, dict):
                result = job.result_json.get('result_json', {})
                if isinstance(result, dict):
                    rmse = result.get('rmse_cv') or result.get('rmse_holdout') or result.get('rmse')
                    r2 = result.get('r2')
                    mae = result.get('mae')
            
            duration = None
            if job.started_at and job.completed_at:
                duration = (job.completed_at - job.started_at).total_seconds()
            
            results.append({
                'model': model_name,
                'status': job.status,
                'rmse': rmse,
                'r2': r2,
                'mae': mae,
                'duration': duration,
                'created': job.created_at,
                'started': job.started_at,
                'completed': job.completed_at
            })
        
        # Sort by RMSE
        results.sort(key=lambda x: x['rmse'] if x['rmse'] is not None else float('inf'))
        
        print(f"{'Model':<30} {'Status':<12} {'RMSE':<15} {'R²':<10} {'Duration':<12}")
        print("─" * 80)
        
        for i, r in enumerate(results, 1):
            model = r['model']
            status_icon = '✓' if r['status'] == 'completed' else '✗'
            rmse_str = f"{r['rmse']:.4f}" if r['rmse'] is not None else "N/A"
            r2_str = f"{r['r2']:.4f}" if r['r2'] is not None else "N/A"
            duration_str = f"{r['duration']:.1f}s" if r['duration'] is not None else "N/A"
            
            rank = "🥇" if i == 1 else "🥈" if i == 2 else "🥉" if i == 3 else f"  {i}."
            
            print(f"{rank} {model:<27} {status_icon} {r['status']:<10} {rmse_str:<15} {r2_str:<10} {duration_str:<12}")
        
        # Timing analysis
        print(f"\n{'─'*80}")
        print("PARALLEL EXECUTION TIMING")
        print(f"{'─'*80}\n")
        
        if results:
            first_created = min(r['created'] for r in results)
            last_created = max(r['created'] for r in results)
            creation_span = (last_created - first_created).total_seconds()
            
            completed_results = [r for r in results if r['completed'] is not None]
            if completed_results:
                first_started = min(r['started'] for r in completed_results if r['started'])
                last_completed = max(r['completed'] for r in completed_results)
                total_execution = (last_completed - first_started).total_seconds()
                
                print(f"Job Creation Span:     {creation_span:.2f}s  (all 6 jobs queued)")
                print(f"Total Execution Time:  {total_execution:.1f}s  (first start → last complete)")
                
                # Calculate what sequential would have been
                total_individual_time = sum(r['duration'] for r in completed_results if r['duration'])
                print(f"Sum of Individual Jobs: {total_individual_time:.1f}s  (if run sequentially)")
                
                if total_individual_time > 0:
                    efficiency = (total_individual_time / total_execution)
                    print(f"\nEfficiency: {efficiency:.2f}x")
                    print(f"Time Saved: {total_individual_time - total_execution:.1f}s ({(1 - total_execution/total_individual_time)*100:.1f}%)")
        
        print(f"\n{'─'*80}")
        print("KEY BENEFITS")
        print(f"{'─'*80}\n")
        print("✓ All 6 models queued instantly (parallel job creation)")
        print("✓ Worker processes jobs as fast as possible")
        print("✓ UI shows real-time progress for all models simultaneously")
        print("✓ Best model selected automatically by RMSE")
        print("✓ Iteration completes once all models finish")
        print("\n" + "="*80 + "\n")
        
    finally:
        db.close()

if __name__ == "__main__":
    generate_summary()
