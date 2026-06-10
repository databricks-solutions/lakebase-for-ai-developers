"""DEPRECATED shim — the memory/state validation suite now lives in the evaluation package at
`agent_server.evaluation.memory_validation`.

Kept so `uv run python -m agent_server.validate_memory [--drop|--no-clean]` keeps working. The
preferred entry is `uv run agent-evaluate --memory`.
"""

from __future__ import annotations

from agent_server.evaluation.memory_validation import (  # noqa: F401
    main,
    run_memory_validation,
)

__all__ = ["main", "run_memory_validation"]


if __name__ == "__main__":
    main()
