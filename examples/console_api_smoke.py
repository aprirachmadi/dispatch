"""Editable read-only REST probe: poetry run python examples/console_api_smoke.py.

Start an already-seeded API separately. No LLM or MCP process is launched here.
Inspect synthetic seed trap #31 and its saved draft/assessment, if any.
Operational sweeps/review/jobs: python -m dispatch.mock_psa.checks (see README).
"""

import asyncio
import json
import sys

import httpx2

BASE_URL = "http://127.0.0.1:8000"  # Edit to match your loopback demo API.
SYNTHETIC_TICKET_ID = 31  # Seeded fictional password-disclosure scenario.
DRAFT_PAGE = {"limit": 3, "offset": 0}


async def main() -> None:
    print("Read-only synthetic ticket inspection; no changes or model calls.")
    print("Base URL:", BASE_URL, "Ticket ID:", SYNTHETIC_TICKET_ID)
    async with httpx2.AsyncClient(
        base_url=BASE_URL, timeout=10, follow_redirects=False, trust_env=False
    ) as client:
        path = f"/tickets/{SYNTHETIC_TICKET_ID}"
        print("Request: GET", path)
        ticket = await client.get(path)
        print("Result:", ticket.status_code)
        ticket.raise_for_status()
        print("Source ticket:", json.dumps(ticket.json(), indent=2))

        print("Request: GET", path + "/drafts", "params:", DRAFT_PAGE)
        drafts = await client.get(path + "/drafts", params=DRAFT_PAGE)
        print("Result:", drafts.status_code)
        drafts.raise_for_status()
        print("Saved drafts (empty before apply):", json.dumps(drafts.json(), indent=2))

        print("Request: GET", path + "/assessment")
        assessment = await client.get(path + "/assessment")
        print("Result:", assessment.status_code, json.dumps(assessment.json(), indent=2))
        if assessment.status_code == 404:
            print("Expected before applied triage: no saved assessment. No model was invoked.")
        else:
            assessment.raise_for_status()
        print("Drafts/approvals are unsent; this probe only read stored results.")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (ValueError, httpx2.HTTPError):
        print("Probe stopped: check the seeded API and synthetic ticket ID.", file=sys.stderr)
        raise SystemExit(1) from None
