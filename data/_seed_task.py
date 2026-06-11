"""Serverless seed-task launcher.

A DABs serverless `spark_python_task` `exec`s the target file WITHOUT defining `__file__` and
WITHOUT the bundle root on `sys.path`, so the data scripts can't `import agent_server` / `import
data`. This launcher's own path *is* reliable (`sys.argv[0]`), so it:

  1. puts the bundle root (its grandparent dir) on `sys.path`,
  2. applies the config the bundle passes as a JSON blob to `os.environ`
     (serverless tasks can't take env vars; this is how the catalog/Lakebase coords arrive), and
  3. runs the real target via `runpy` with a proper `__file__` so the target's own repo-root
     bootstrap works unchanged.

Invoked as: `_seed_task.py <target-rel-path-under-repo-root> <config-json>`
e.g. `_seed_task.py data/00_bootstrap_schemas.py '{"UC_CATALOG":"main",...}'`
"""

import json
import os
import runpy
import sys
from pathlib import Path

# _seed_task.py lives at <root>/data/_seed_task.py → parents[1] is the bundle root (has agent_server/ + data/).
ROOT = Path(sys.argv[0]).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

if len(sys.argv) < 2:
    raise SystemExit("usage: _seed_task.py <target-rel-path> [config-json]")

target_rel = sys.argv[1]

# Apply config (JSON) to the environment so agent_server.config.settings resolves it.
if len(sys.argv) > 2 and sys.argv[2].strip().startswith("{"):
    for key, value in json.loads(sys.argv[2]).items():
        if value not in (None, ""):
            os.environ[key] = str(value)

target_path = str(ROOT / target_rel)
print(f"[_seed_task] root={ROOT} running {target_rel}")
sys.argv = [target_path]  # the target sees a clean argv (its config comes from os.environ)
try:
    runpy.run_path(target_path, run_name="__main__")
except SystemExit as exc:
    # Scripts that end with sys.exit(0) (e.g. 04_verify) raise SystemExit through runpy; under the
    # serverless IPython kernel even a clean exit(0) is flagged as a task failure. Swallow success
    # codes; re-raise real failures (non-zero int, or a string message → exit code 1).
    if exc.code not in (0, None):
        raise
