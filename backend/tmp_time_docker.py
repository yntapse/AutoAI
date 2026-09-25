"""Time a single Docker sandbox run to calibrate timeout values."""
import subprocess
import time
import tempfile
from pathlib import Path

script_code = """
import json, numpy as np
data = np.random.rand(100)
result = {"rmse": float(np.std(data)), "mae": float(np.mean(np.abs(data))), "r2": 0.5}
result_json = result
print(json.dumps({"result_json": result_json}))
"""

tmp = Path(tempfile.mkdtemp())
script_path = tmp / "script.py"
script_path.write_text(script_code.strip())

cmd = [
    "docker", "run", "--rm",
    "--memory=512m", "--cpus=1", "--network=none",
    "-v", f"{script_path.resolve()}:/app/script.py:ro",
    "pyrun-sandbox-base",
    "/app/script.py",
]

t0 = time.time()
result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
elapsed = time.time() - t0

print(f"Elapsed: {elapsed:.2f}s")
print(f"Return code: {result.returncode}")
print(f"Stdout: {result.stdout.strip()}")
if result.stderr.strip():
    print(f"Stderr: {result.stderr.strip()[:200]}")
