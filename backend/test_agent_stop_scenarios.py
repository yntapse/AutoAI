import json
import time
from pathlib import Path

import requests

BASE_URL = "http://127.0.0.1:8000"
TIMEOUT = 30


def wait_for_server(max_wait_seconds: int = 30) -> None:
    deadline = time.time() + max_wait_seconds
    while time.time() < deadline:
        try:
            response = requests.get(f"{BASE_URL}/", timeout=3)
            if response.status_code == 200:
                print("[OK] Server reachable")
                return
        except Exception:
            pass
        time.sleep(1)
    raise RuntimeError("Server is not reachable at http://127.0.0.1:8000")


def create_test_csv() -> Path:
    file_path = Path(__file__).parent / "tmp_stop_test.csv"
    file_path.write_text(
        "f1,f2,target\n"
        "1,2,3\n"
        "2,3,5\n"
        "3,4,7\n"
        "4,5,9\n"
        "5,6,11\n"
        "6,7,13\n"
        "7,8,15\n"
        "8,9,17\n",
        encoding="utf-8",
    )
    return file_path


def create_project(csv_path: Path, project_name: str) -> str:
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

    payload = response.json()
    project_id = payload["project_id"]
    print(f"[OK] Project created: {project_id}")
    return project_id


def start_agent(project_id: str, max_iterations: int = 6, threshold: float = 0.001) -> str:
    payload = {
        "project_id": project_id,
        "max_iterations": max_iterations,
        "improvement_threshold": threshold,
    }
    response = requests.post(f"{BASE_URL}/agent/start", json=payload, timeout=TIMEOUT)
    if response.status_code != 201:
        raise RuntimeError(f"Agent start failed: {response.status_code} {response.text}")

    agent_id = response.json()["agent_run_id"]
    print(f"[OK] Agent started: {agent_id}")
    return agent_id


def get_status(agent_id: str) -> dict:
    response = requests.get(f"{BASE_URL}/agent/status/{agent_id}", timeout=TIMEOUT)
    if response.status_code != 200:
        raise RuntimeError(f"Status failed: {response.status_code} {response.text}")
    return response.json()


def stop_agent(agent_id: str) -> dict:
    response = requests.post(f"{BASE_URL}/agent/stop/{agent_id}", timeout=TIMEOUT)
    if response.status_code != 200:
        raise RuntimeError(f"Stop failed: {response.status_code} {response.text}")
    return response.json()


def wait_for_iteration_at_least(agent_id: str, min_iteration: int, max_wait_seconds: int = 120) -> dict:
    deadline = time.time() + max_wait_seconds
    latest = {}
    while time.time() < deadline:
        latest = get_status(agent_id)
        if latest["current_iteration"] >= min_iteration:
            return latest
        time.sleep(2)
    raise RuntimeError(
        f"Timed out waiting for iteration >= {min_iteration}. Last status: {json.dumps(latest)}"
    )


def observe_no_increment(agent_id: str, baseline_iteration: int, seconds: int = 12) -> dict:
    time.sleep(seconds)
    status = get_status(agent_id)
    return {
        "baseline_iteration": baseline_iteration,
        "after_wait_iteration": status["current_iteration"],
        "status": status,
        "unchanged": status["current_iteration"] == baseline_iteration,
    }


def test_a_mid_iteration_stop(project_id: str) -> dict:
    print("\n=== Test A: Mid Iteration Stop ===")
    agent_id = start_agent(project_id, max_iterations=6)
    status_before = wait_for_iteration_at_least(agent_id, min_iteration=2)
    print(f"[INFO] Reached iteration: {status_before['current_iteration']}")

    stop_response = stop_agent(agent_id)
    status_after_stop = get_status(agent_id)

    freeze_check = observe_no_increment(agent_id, status_after_stop["current_iteration"], seconds=12)

    return {
        "agent_id": agent_id,
        "stop_response": stop_response,
        "status_after_stop": status_after_stop,
        "freeze_check": freeze_check,
    }


def test_b_rapid_stop(project_id: str) -> dict:
    print("\n=== Test B: Rapid Stop ===")
    agent_id = start_agent(project_id, max_iterations=6)
    stop_response = stop_agent(agent_id)
    time.sleep(4)
    status_after = get_status(agent_id)
    return {
        "agent_id": agent_id,
        "stop_response": stop_response,
        "status_after": status_after,
    }


def test_c_double_stop(project_id: str) -> dict:
    print("\n=== Test C: Double Stop ===")
    agent_id = start_agent(project_id, max_iterations=6)
    time.sleep(3)
    first_stop = stop_agent(agent_id)
    second_stop = stop_agent(agent_id)
    status = get_status(agent_id)
    return {
        "agent_id": agent_id,
        "first_stop": first_stop,
        "second_stop": second_stop,
        "status": status,
    }


def wait_for_terminal_status(agent_id: str, max_wait_seconds: int = 240) -> dict:
    deadline = time.time() + max_wait_seconds
    terminal = {"completed", "failed", "stopped"}
    latest = {}
    while time.time() < deadline:
        latest = get_status(agent_id)
        if latest["status"] in terminal:
            return latest
        time.sleep(3)
    raise RuntimeError(f"Timed out waiting for terminal state. Last status: {json.dumps(latest)}")


def test_d_stop_after_completion(project_id: str) -> dict:
    print("\n=== Test D: Stop After Completion ===")
    agent_id = start_agent(project_id, max_iterations=1)
    terminal_status = wait_for_terminal_status(agent_id, max_wait_seconds=180)
    stop_response = stop_agent(agent_id)
    final_status = get_status(agent_id)
    return {
        "agent_id": agent_id,
        "terminal_status": terminal_status,
        "stop_response": stop_response,
        "final_status": final_status,
    }


def main() -> None:
    wait_for_server(45)
    csv_path = create_test_csv()

    try:
        result_a = None
        result_b = None
        result_c = None
        result_d = None

        try:
            project_id_a = create_project(csv_path, "stop-test-a")
            result_a = test_a_mid_iteration_stop(project_id_a)
        except Exception as exc:
            result_a = {"error": str(exc)}

        try:
            project_id_b = create_project(csv_path, "stop-test-b")
            result_b = test_b_rapid_stop(project_id_b)
        except Exception as exc:
            result_b = {"error": str(exc)}

        try:
            project_id_c = create_project(csv_path, "stop-test-c")
            result_c = test_c_double_stop(project_id_c)
        except Exception as exc:
            result_c = {"error": str(exc)}

        try:
            project_id_d = create_project(csv_path, "stop-test-d")
            result_d = test_d_stop_after_completion(project_id_d)
        except Exception as exc:
            result_d = {"error": str(exc)}

        output = {
            "test_a": result_a,
            "test_b": result_b,
            "test_c": result_c,
            "test_d": result_d,
        }

        print("\n=== Combined Results ===")
        print(json.dumps(output, indent=2))
    finally:
        if csv_path.exists():
            csv_path.unlink()


if __name__ == "__main__":
    main()
