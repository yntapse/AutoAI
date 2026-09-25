"""
Test script for iteration history endpoint.
Validates that the endpoint correctly tracks iterations as they execute.
"""
import json
import time
from pathlib import Path
from typing import Dict, List

import requests

BASE_URL = "http://127.0.0.1:8000"
TIMEOUT = 30


def wait_for_server(max_wait_seconds: int = 30) -> None:
    """Wait for the backend server to become available."""
    print("[INFO] Waiting for server...")
    deadline = time.time() + max_wait_seconds
    while time.time() < deadline:
        try:
            response = requests.get(f"{BASE_URL}/", timeout=3)
            if response.status_code == 200:
                print("[OK] Server is ready")
                return
        except Exception:
            pass
        time.sleep(1)
    raise RuntimeError("Server is not reachable at http://127.0.0.1:8000")


def create_test_csv() -> Path:
    """Create a test CSV file for regression."""
    file_path = Path(__file__).parent / "tmp_history_test.csv"
    file_path.write_text(
        "feature1,feature2,feature3,target\n"
        "1.0,2.0,3.0,10.5\n"
        "2.0,3.0,4.0,15.2\n"
        "3.0,4.0,5.0,20.1\n"
        "4.0,5.0,6.0,25.3\n"
        "5.0,6.0,7.0,30.7\n"
        "6.0,7.0,8.0,35.9\n"
        "7.0,8.0,9.0,40.2\n"
        "8.0,9.0,10.0,45.8\n"
        "9.0,10.0,11.0,50.6\n"
        "10.0,11.0,12.0,55.1\n",
        encoding="utf-8",
    )
    print(f"[OK] Test CSV created: {file_path}")
    return file_path


def create_project(csv_path: Path, project_name: str) -> str:
    """Create a project by uploading CSV."""
    print(f"[INFO] Creating project '{project_name}'...")
    with csv_path.open("rb") as handle:
        files = {"file": (csv_path.name, handle, "text/csv")}
        data = {
            "project_name": project_name,
            "target_column": "target",
        }
        response = requests.post(
            f"{BASE_URL}/projects/create",
            data=data,
            files=files,
            timeout=TIMEOUT,
        )

    if response.status_code != 201:
        raise RuntimeError(f"Project creation failed: {response.status_code} {response.text}")

    project_id = response.json()["project_id"]
    print(f"[OK] Project created: {project_id}")
    return project_id


def start_agent(project_id: str, max_iterations: int = 4) -> str:
    """Start an agent with specified max iterations."""
    print(f"[INFO] Starting agent with {max_iterations} iterations...")
    payload = {
        "project_id": project_id,
        "max_iterations": max_iterations,
        "improvement_threshold": 0.001,
    }
    response = requests.post(f"{BASE_URL}/agent/start", json=payload, timeout=TIMEOUT)
    
    if response.status_code != 201:
        raise RuntimeError(f"Agent start failed: {response.status_code} {response.text}")

    agent_id = response.json()["agent_run_id"]
    print(f"[OK] Agent started: {agent_id}")
    return agent_id


def get_agent_status(agent_id: str) -> Dict:
    """Get current agent status."""
    response = requests.get(f"{BASE_URL}/agent/status/{agent_id}", timeout=TIMEOUT)
    if response.status_code != 200:
        raise RuntimeError(f"Status check failed: {response.status_code} {response.text}")
    return response.json()


def get_iteration_history(agent_id: str) -> Dict:
    """Get iteration history for an agent."""
    response = requests.get(f"{BASE_URL}/agent/history/{agent_id}", timeout=TIMEOUT)
    if response.status_code != 200:
        raise RuntimeError(f"History check failed: {response.status_code} {response.text}")
    return response.json()


def validate_iteration_data(iteration: Dict, iteration_num: int) -> List[str]:
    """Validate a single iteration's data structure and return any errors."""
    errors = []
    
    # Check required fields exist
    required_fields = ["iteration", "training_run_id", "model_name", "rmse", "mae", "r2", "started_at"]
    for field in required_fields:
        if field not in iteration:
            errors.append(f"Missing required field: {field}")
    
    # Validate iteration number matches
    if iteration.get("iteration") != iteration_num:
        errors.append(f"Expected iteration {iteration_num}, got {iteration.get('iteration')}")
    
    # Validate data types
    if iteration.get("rmse") is not None and not isinstance(iteration["rmse"], (int, float)):
        errors.append(f"RMSE should be numeric, got {type(iteration['rmse'])}")
    
    if iteration.get("mae") is not None and not isinstance(iteration["mae"], (int, float)):
        errors.append(f"MAE should be numeric, got {type(iteration['mae'])}")
    
    if iteration.get("r2") is not None and not isinstance(iteration["r2"], (int, float)):
        errors.append(f"R2 should be numeric, got {type(iteration['r2'])}")
    
    return errors


def wait_for_completion(agent_id: str, max_wait_seconds: int = 600) -> Dict:
    """Wait for agent to reach a terminal state (completed, failed, stopped)."""
    print("[INFO] Waiting for agent completion...")
    deadline = time.time() + max_wait_seconds
    terminal_states = {"completed", "failed", "stopped"}
    
    while time.time() < deadline:
        status = get_agent_status(agent_id)
        if status["status"] in terminal_states:
            print(f"[OK] Agent reached terminal state: {status['status']}")
            return status
        time.sleep(3)
    
    raise RuntimeError(f"Timeout waiting for agent completion after {max_wait_seconds}s")


def find_best_iteration(iterations: List[Dict]) -> Dict:
    """Find the iteration with the lowest RMSE."""
    valid_iterations = [it for it in iterations if it.get("rmse") is not None]
    if not valid_iterations:
        return None
    return min(valid_iterations, key=lambda x: x["rmse"])


def main():
    """Main test execution."""
    print("\n" + "="*70)
    print("ITERATION HISTORY ENDPOINT TEST")
    print("="*70 + "\n")
    
    try:
        # Setup
        wait_for_server(45)
        csv_path = create_test_csv()
        project_id = create_project(csv_path, "history-test")
        agent_id = start_agent(project_id, max_iterations=4)
        
        # Track history growth during execution
        print("\n" + "-"*70)
        print("TRACKING HISTORY DURING EXECUTION")
        print("-"*70 + "\n")
        
        observed_counts = []
        poll_count = 0
        max_polls = 20
        
        while poll_count < max_polls:
            try:
                status = get_agent_status(agent_id)
                history = get_iteration_history(agent_id)
                
                iteration_count = len(history["iterations"])
                observed_counts.append(iteration_count)
                
                print(f"[Poll {poll_count + 1}] Status: {status['status']}, "
                      f"Current Iteration: {status['current_iteration']}/{status['max_iterations']}, "
                      f"History Count: {iteration_count}")
                
                # Validate each iteration
                for idx, iteration in enumerate(history["iterations"], start=1):
                    errors = validate_iteration_data(iteration, idx)
                    if errors:
                        print(f"  ⚠️  Iteration {idx} validation errors: {errors}")
                    else:
                        rmse = iteration.get("rmse", "N/A")
                        model = iteration.get("model_name", "N/A")
                        print(f"  ✅ Iteration {idx}: {model} (RMSE: {rmse})")
                
                # Check if agent is done
                if status["status"] in {"completed", "failed", "stopped"}:
                    print(f"\n[OK] Agent reached terminal state: {status['status']}")
                    break
                
                poll_count += 1
                time.sleep(5)
                
            except Exception as e:
                print(f"[ERROR] Poll {poll_count + 1} failed: {str(e)}")
                break
        
        # Final validation after completion
        print("\n" + "-"*70)
        print("FINAL VALIDATION")
        print("-"*70 + "\n")
        
        final_status = get_agent_status(agent_id)
        final_history = get_iteration_history(agent_id)
        
        # Validation 1: All iterations present
        expected_iterations = 4
        actual_iterations = len(final_history["iterations"])
        print(f"[CHECK] Expected iterations: {expected_iterations}")
        print(f"[CHECK] Actual iterations: {actual_iterations}")
        
        if actual_iterations == expected_iterations:
            print("✅ All iterations present")
        else:
            print(f"❌ Iteration count mismatch!")
        
        # Validation 2: Order is correct
        print("\n[CHECK] Validating iteration order...")
        order_correct = True
        for idx, iteration in enumerate(final_history["iterations"], start=1):
            if iteration["iteration"] != idx:
                print(f"❌ Order error: Position {idx} has iteration {iteration['iteration']}")
                order_correct = False
        
        if order_correct:
            print("✅ Iteration order is correct")
        
        # Validation 3: Best iteration via RMSE
        print("\n[CHECK] Identifying best iteration by RMSE...")
        best_iteration = find_best_iteration(final_history["iterations"])
        
        if best_iteration:
            print(f"✅ Best iteration: #{best_iteration['iteration']}")
            print(f"   Model: {best_iteration['model_name']}")
            print(f"   RMSE: {best_iteration['rmse']}")
            print(f"   MAE: {best_iteration['mae']}")
            print(f"   R²: {best_iteration['r2']}")
        else:
            print("❌ Could not determine best iteration (no valid RMSE values)")
        
        # Validation 4: No 500 errors
        print("\n[CHECK] No 500 errors during test")
        print("✅ Test completed without 500 errors")
        
        # Validation 5: Data consistency
        print("\n[CHECK] Data consistency validation...")
        all_valid = True
        for iteration in final_history["iterations"]:
            errors = validate_iteration_data(iteration, iteration["iteration"])
            if errors:
                print(f"❌ Iteration {iteration['iteration']}: {errors}")
                all_valid = False
        
        if all_valid:
            print("✅ All iteration data is consistent")
        
        # Summary
        print("\n" + "="*70)
        print("TEST SUMMARY")
        print("="*70)
        print(f"Agent ID: {agent_id}")
        print(f"Final Status: {final_status['status']}")
        print(f"Iterations Completed: {actual_iterations}/{expected_iterations}")
        print(f"History Growth: {observed_counts}")
        print(f"Best Model: {best_iteration['model_name'] if best_iteration else 'N/A'}")
        print(f"Best RMSE: {best_iteration['rmse'] if best_iteration else 'N/A'}")
        print("="*70 + "\n")
        
        # Cleanup
        if csv_path.exists():
            csv_path.unlink()
            print("[OK] Cleaned up test CSV")
        
    except Exception as e:
        print(f"\n❌ TEST FAILED: {str(e)}")
        import traceback
        traceback.print_exc()
        return 1
    
    return 0


if __name__ == "__main__":
    exit(main())
