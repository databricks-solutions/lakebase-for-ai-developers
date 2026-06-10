"""DEPRECATED shim — the eval code now lives in the `agent_server.evaluation` package.

Kept so existing imports (`from agent_server.evaluate_agent import evaluate, ...`) and docs
references keep working. New code should import from `agent_server.evaluation`. The `agent-evaluate`
console script points at `agent_server.evaluation.cli:main`, which also runs the new flywheel
(`--layer graph|server`, `--fast/--full`); see that package's README/cli docstring.
"""

from __future__ import annotations

from agent_server.evaluation.cli import evaluate, evaluate_direct, main, run_flywheel
from agent_server.evaluation.dataset import EVAL_RECORDS
from agent_server.evaluation.runners import run_agent
from agent_server.evaluation.scorers import _gate_correct, _scorers, gate_correctness

__all__ = [
    "evaluate",
    "evaluate_direct",
    "run_flywheel",
    "run_agent",
    "EVAL_RECORDS",
    "_scorers",
    "_gate_correct",
    "gate_correctness",
    "main",
]


if __name__ == "__main__":
    main()
