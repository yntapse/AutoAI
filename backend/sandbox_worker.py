import multiprocessing
import os
import shutil
import subprocess
import tempfile
import time
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from database import SessionLocal
from models.agent_run import AgentRun
from models.project import Project
from models.sandbox_job import SandboxJob
from models.training_run import TrainingRun
from services.sandbox_jobs_service import claim_next_sandbox_job


POLL_INTERVAL_SECONDS = int(os.getenv("SANDBOX_POLL_INTERVAL_SECONDS", "2"))
RECOVER_RUNNING_ON_STARTUP = os.getenv("SANDBOX_RECOVER_ON_STARTUP", "true").lower() in {"true", "1", "yes"}
SANDBOX_IMAGE = os.getenv("SANDBOX_IMAGE", "pyrun-sandbox-base")
SANDBOX_WORKER_CONCURRENCY = int(os.getenv("SANDBOX_WORKER_CONCURRENCY", "2"))
BASE_DIR = Path(__file__).resolve().parent
UPLOADS_DIR = BASE_DIR / "uploads"


def recover_running_jobs() -> int:
    recovery_db = SessionLocal()
    try:
        reclaimed_count = (
            recovery_db.query(SandboxJob)
            .filter(SandboxJob.status == "running")
            .update(
                {
                    SandboxJob.status: "queued",
                    SandboxJob.started_at: None,
                },
                synchronize_session=False,
            )
        )
        recovery_db.commit()
        return reclaimed_count
    except Exception:
        recovery_db.rollback()
        raise
    finally:
        recovery_db.close()


def _resolve_dataset_source(file_id: str) -> Path:
    source_path = (UPLOADS_DIR / file_id).resolve()
    if not source_path.exists() or not source_path.is_file():
        raise FileNotFoundError(f"Dataset file not found for file_id='{file_id}' at {source_path}")
    return source_path


def _execute_job_in_sandbox(job: SandboxJob) -> dict:
    db = SessionLocal()
    try:
        project = db.query(Project).filter(Project.id == job.project_id).first()
        if project is None:
            raise ValueError(f"Project not found for job_id='{job.id}'")

        dataset_source = _resolve_dataset_source(project.file_id)
    finally:
        db.close()

    temp_dir = Path(tempfile.mkdtemp(prefix=f"job_{job.id}_"))
    script_path = temp_dir / "script.py"
    dataset_path = temp_dir / "dataset.csv"

    try:
        script_path.write_text(job.script_content, encoding="utf-8")
        shutil.copy2(dataset_source, dataset_path)

        command = [
            "docker",
            "run",
            "--rm",
            "--memory=512m",
            "--cpus=1",
            "--pids-limit=64",
            "--network=none",
            "--read-only",
            "--cap-drop=ALL",
            "-v",
            f"{script_path.resolve()}:/app/script.py:ro",
            "-v",
            f"{dataset_path.resolve()}:/app/dataset.csv:ro",
            SANDBOX_IMAGE,
            "/app/script.py",
        ]

        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=max(1, int(job.timeout_seconds or 60)),
            check=False,
        )

        stdout_text = (result.stdout or "").strip()
        stderr_text = (result.stderr or "").strip()

        if result.returncode != 0:
            raise RuntimeError(stderr_text or stdout_text or f"Docker run failed with exit code {result.returncode}")

        parsed_output = None
        try:
            parsed_output = json.loads(stdout_text)
        except json.JSONDecodeError:
            # Allow non-JSON noise lines, but require one valid JSON object at the end.
            for line in reversed(stdout_text.splitlines()):
                candidate = line.strip()
                if not candidate:
                    continue
                try:
                    parsed_output = json.loads(candidate)
                    break
                except json.JSONDecodeError:
                    continue

        if not isinstance(parsed_output, dict):
            raise ValueError(stderr_text or stdout_text or "Invalid JSON output")
        if not isinstance(parsed_output.get("result_json"), dict):
            raise ValueError("Sandbox output missing required system-generated 'result_json' object")

        return parsed_output
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)


def run_worker(worker_id: int = 0) -> None:
    """Run a single sandbox worker loop.
    
    Args:
        worker_id: Unique identifier for this worker (for logging).
    """
    worker_tag = f"[sandbox-worker-{worker_id}]"
    print(f"{worker_tag} Worker started")

    while True:
        try:
            claim_db = SessionLocal()
            try:
                job = claim_next_sandbox_job(claim_db)
            finally:
                claim_db.close()

            if job is None:
                time.sleep(POLL_INTERVAL_SECONDS)
                continue

            print(f"{worker_tag} Job claimed: {job.id}")

            try:
                parsed_result = _execute_job_in_sandbox(job)

                complete_db = SessionLocal()
                try:
                    db_job = complete_db.query(SandboxJob).filter(SandboxJob.id == job.id).first()
                    if db_job is None:
                        print(f"{worker_tag} Job missing before completion: {job.id}")
                        continue

                    db_job.status = "completed"
                    db_job.result_json = parsed_result
                    db_job.error_log = None
                    db_job.completed_at = datetime.now(timezone.utc)
                    complete_db.commit()

                    print(f"{worker_tag} Job completed: {job.id}")
                except Exception:
                    complete_db.rollback()
                    raise
                finally:
                    complete_db.close()

            except subprocess.TimeoutExpired as timeout_exc:
                timeout_db = SessionLocal()
                try:
                    timeout_job = timeout_db.query(SandboxJob).filter(SandboxJob.id == job.id).first()
                    if timeout_job is not None:
                        timeout_job.status = "timeout"
                        timeout_job.error_log = "Execution timeout"
                        timeout_job.result_json = None
                        timeout_job.completed_at = datetime.now(timezone.utc)
                        timeout_db.commit()

                    print(f"{worker_tag} Job timed out: {job.id}")
                except Exception as update_exc:
                    timeout_db.rollback()
                    print(f"{worker_tag} Job timeout update error: {job.id} | error: {update_exc}")
                finally:
                    timeout_db.close()

            except Exception as exc:
                fail_db = SessionLocal()
                try:
                    failed_job = fail_db.query(SandboxJob).filter(SandboxJob.id == job.id).first()
                    if failed_job is not None:
                        failed_job.status = "failed"
                        failed_job.error_log = str(exc)
                        failed_job.completed_at = datetime.now(timezone.utc)
                        fail_db.commit()

                    print(f"{worker_tag} Job failed: {job.id} | error: {exc}")
                except Exception as update_exc:
                    fail_db.rollback()
                    print(
                        f"{worker_tag} Job failed update error: {job.id} | "
                        f"update_error: {update_exc} | original_error: {exc}"
                    )
                finally:
                    fail_db.close()

        except KeyboardInterrupt:
            print(f"{worker_tag} KeyboardInterrupt received, shutting down gracefully")
            break
        except Exception as loop_exc:
            print(f"{worker_tag} Worker loop error: {loop_exc}")
            time.sleep(POLL_INTERVAL_SECONDS)

    print(f"{worker_tag} Worker stopped")


def run_worker_pool(concurrency: Optional[int] = None) -> None:
    """Launch multiple sandbox workers as separate processes.
    
    Args:
        concurrency: Number of worker processes to spawn. Defaults to SANDBOX_WORKER_CONCURRENCY.
    """
    num_workers = concurrency if concurrency is not None else SANDBOX_WORKER_CONCURRENCY
    num_workers = max(1, num_workers)
    
    print(f"[sandbox-pool] Starting sandbox worker pool with {num_workers} worker(s)")
    
    # Recover running jobs once before spawning workers
    if RECOVER_RUNNING_ON_STARTUP:
        reclaimed = recover_running_jobs()
        if reclaimed:
            print(f"[sandbox-pool] Re-queued {reclaimed} running job(s) on startup")
    
    if num_workers == 1:
        # Single worker mode - run directly without spawning a subprocess
        print("[sandbox-pool] Running in single-worker mode")
        run_worker(worker_id=0)
        return
    
    # Multi-worker mode - spawn separate processes
    processes: list[multiprocessing.Process] = []
    
    try:
        for worker_id in range(num_workers):
            process = multiprocessing.Process(
                target=run_worker,
                args=(worker_id,),
                name=f"sandbox-worker-{worker_id}",
                daemon=False,
            )
            process.start()
            processes.append(process)
            print(f"[sandbox-pool] Spawned worker process {worker_id} (PID: {process.pid})")
        
        print(f"[sandbox-pool] All {num_workers} workers running. Press Ctrl+C to stop.")
        
        # Wait for all processes to complete (they run indefinitely until interrupted)
        for process in processes:
            process.join()
            
    except KeyboardInterrupt:
        print("\n[sandbox-pool] Received shutdown signal, stopping all workers...")
        for process in processes:
            if process.is_alive():
                process.terminate()
        
        # Give workers time to clean up
        for process in processes:
            process.join(timeout=5)
            if process.is_alive():
                print(f"[sandbox-pool] Force killing worker {process.name}")
                process.kill()
        
        print("[sandbox-pool] All workers stopped")


if __name__ == "__main__":
    run_worker_pool()
