from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core_runtime.eval_runner import run_replay_eval_pack, run_smoke_eval_pack


def main() -> int:
    parser = argparse.ArgumentParser(description="Run agent-pods eval packs")
    parser.add_argument("suite", choices=["smoke", "replay"])
    parser.add_argument("path")
    args = parser.parse_args()

    if args.suite == "smoke":
        result = run_smoke_eval_pack(args.path)
    else:
        result = run_replay_eval_pack(args.path)

    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if int(result.get("failed", 0)) == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
