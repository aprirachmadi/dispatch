"""Real CLI agent: leave the seeded FastAPI backend running; MCP starts automatically.

poetry run python examples/run_triage.py --ticket-id 31
Preview is the default. --apply additionally requires trusted write-mode configuration.
"""

from dispatch.agent.cli import triage_main

if __name__ == "__main__":
    triage_main()
