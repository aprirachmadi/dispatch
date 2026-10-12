"""Offline real-model evals: poetry run python examples/run_evals.py [--limit 2].

No FastAPI/MCP servers are needed. --validate-only requires neither model nor DeepEval.
Default runs include GEval; --no-judge skips only judging, not generation.
"""

from dispatch.evaluation.cli import eval_main

if __name__ == "__main__":
    eval_main()
