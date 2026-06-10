"""Agent evaluation package — the development flywheel.

Run quality + trace-shape (span structure, per-node latency, token budget) checks over the agent,
in-process (`--layer graph`) or through the live local server (`--layer server`), with a warn-only
baseline diff. Entry point: `agent-evaluate` → `cli.main`.

Public API (also preserves `agent_server.evaluate_agent` back-compat via that module's shim):
"""

from __future__ import annotations

from agent_server.evaluation.cli import evaluate, evaluate_direct, run_flywheel
from agent_server.evaluation.dataset import EVAL_RECORDS
from agent_server.evaluation.runners import run_agent

__all__ = ["evaluate", "evaluate_direct", "run_flywheel", "EVAL_RECORDS", "run_agent"]
