"""
Quick diagnostic script to check sandbox worker status
"""
import os
import psutil
import sys

def check_sandbox_worker():
    print("=" * 70)
    print("SANDBOX WORKER STATUS CHECK")
    print("=" * 70)
    
    expected_concurrency = int(os.getenv("SANDBOX_WORKER_CONCURRENCY", "2"))
    print(f"\nExpected concurrency (SANDBOX_WORKER_CONCURRENCY): {expected_concurrency}")
    
    current_pid = psutil.Process().pid
    workers_found = []
    
    for proc in psutil.process_iter(['pid', 'name', 'cmdline']):
        try:
            if proc.info['pid'] == current_pid:
                continue  # Skip this script
            
            cmdline = proc.info.get('cmdline')
            if cmdline:
                cmdline_str = ' '.join(str(arg) for arg in cmdline)
                # Must contain sandbox_worker.py and python, but not check_sandbox_worker
                if 'sandbox_worker.py' in cmdline_str and 'check_sandbox_worker' not in cmdline_str:
                    workers_found.append({
                        'pid': proc.info['pid'],
                        'cmdline': cmdline_str,
                    })
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
    
    if workers_found:
        print(f"\n✓ {len(workers_found)} Sandbox worker(s) RUNNING")
        for idx, worker in enumerate(workers_found):
            print(f"  Worker {idx}: PID={worker['pid']}")
        
        if len(workers_found) < expected_concurrency:
            print(f"\n⚠ Warning: Only {len(workers_found)} of {expected_concurrency} expected workers are running")
    else:
        print("\n✗ Sandbox worker is NOT RUNNING")
        print("\nTo start the sandbox worker pool, run:")
        print("  cd backend")
        print("  python sandbox_worker.py")
        print("\nTo adjust concurrency, set SANDBOX_WORKER_CONCURRENCY environment variable:")
        print("  $env:SANDBOX_WORKER_CONCURRENCY = 4; python sandbox_worker.py")
    
    print("\n" + "=" * 70)
    return len(workers_found) > 0

if __name__ == "__main__":
    is_running = check_sandbox_worker()
    sys.exit(0 if is_running else 1)
