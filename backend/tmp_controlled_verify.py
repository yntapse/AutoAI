"""
Controlled single-run wrapper for tmp_reflection_6iter_test.
Captures all output (stdout + stderr) to verify_run_output.txt.
All prints inside run_agent_loop() stay in the harness's StringIO buffer;
the final REFLECTION_TEST_RESULT print goes to our file.
"""
import sys
import os
import traceback

OUT_FILE = os.path.join(os.path.dirname(__file__), "verify_run_output.txt")

# Open file for writing before anything else so we capture all output
_out = open(OUT_FILE, "w", encoding="utf-8", buffering=1)
sys.stdout = _out
sys.stderr = _out

try:
    import tmp_reflection_6iter_test as harness
    harness.main()
except Exception as exc:
    print(f"\n[VERIFY-WRAPPER ERROR] {exc}", flush=True)
    traceback.print_exc()
finally:
    sys.stdout.flush()
    _out.close()
