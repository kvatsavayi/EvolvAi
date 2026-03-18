import sys
from pathlib import Path

from apps.api.dependencies import get_state

if len(sys.argv) < 2:
    raise SystemExit("usage: replay_run.py <run_id>")

run_id = sys.argv[1]
state = get_state()
row = state.db.fetchone("SELECT request_id, pod_id FROM runs WHERE run_id = ?", (run_id,))
if not row:
    raise SystemExit("run not found")
print(f"Found run for request={row['request_id']} pod={row['pod_id']}")
