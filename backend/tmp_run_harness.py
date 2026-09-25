"""Wrapper that runs the reflection harness and writes full output to a result file."""
import sys
import traceback

# Force unbuffered output
sys.stdout = open("verify_run_output.txt", "w", buffering=1)
sys.stderr = sys.stdout

try:
    # Patch redirect_stdout so run_agent_loop output goes to our file too
    import tmp_reflection_6iter_test as harness
    harness.main()
except Exception as exc:
    print(f"\n[WRAPPER ERROR] {exc}", flush=True)
    traceback.print_exc()
finally:
    sys.stdout.flush()
    sys.stdout.close()
