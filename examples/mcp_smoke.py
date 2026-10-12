"""Make real read-only MCP calls against a running, seeded FastAPI backend.

Run: poetry run python examples/mcp_smoke.py
Add --analytics to include real local embedding inference (first use downloads a model).
No AI API/key is needed. The script launches/stops MCP automatically; leave FastAPI running.
"""

import argparse
import asyncio
import json
import os
import sys
from datetime import date, timedelta
from pathlib import Path

from mcp import Client
from mcp.client.stdio import StdioServerParameters


def monday(value: str) -> str:
    """Validate the example's report date before making any calls."""
    try:
        parsed = date.fromisoformat(value)
        if parsed.isoformat() != value or parsed.weekday() != 0:
            raise ValueError
        parsed + timedelta(days=7)
    except (ValueError, OverflowError) as error:
        raise argparse.ArgumentTypeError("Use an ISO Monday, for example 2026-10-12") from error
    return value


async def main(arguments: argparse.Namespace) -> int:
    checks = [
        ("search_tickets", {"limit": 3}, False),
        ("get_ticket", {"ticket_id": arguments.ticket_id}, False),
        ("search_tickets", {"limit": 101}, True),
    ]
    if arguments.analytics:
        print("Analytics enabled: first similarity use may download public model artifacts.")
        tuesday = (date.fromisoformat(arguments.week_start) + timedelta(days=1)).isoformat()
        checks.extend(
            [
                ("find_similar_tickets", {"ticket_id": arguments.ticket_id, "limit": 3}, False),
                ("weekly_report", {"week_start": arguments.week_start}, False),
                ("find_similar_tickets", {"ticket_id": arguments.ticket_id, "limit": 11}, True),
                ("weekly_report", {"week_start": tuesday}, True),
            ]
        )

    environment = {**os.environ, "DISPATCH_READ_ONLY": "true"}
    if arguments.base_url is not None:
        environment["DISPATCH_PSA_BASE_URL"] = arguments.base_url
    server = StdioServerParameters(
        command=sys.executable,
        args=["-m", "dispatch.mcp_server.server"],
        cwd=Path(__file__).resolve().parents[1],
        env=environment,
    )
    failures = 0
    async with Client(server, read_timeout_seconds=180) as client:
        for name, parameters, expected_error in checks:
            result = await client.call_tool(name, parameters)
            passed = bool(result.is_error) == expected_error
            failures += not passed
            expectation = "expected rejection" if expected_error else "expected success"
            print(f"\n{'PASS' if passed else 'FAIL'}: {name} {parameters} ({expectation})")
            if result.structured_content is not None:
                print(json.dumps(result.structured_content, indent=2))
            else:
                for item in result.content:
                    if item.type == "text":
                        print(item.text)

    print(f"\n{len(checks) - failures}/{len(checks)} checks passed.")
    return 1 if failures else 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", help="Override the backend URL from .env/environment")
    parser.add_argument(
        "--ticket-id", type=int, default=21, help="Existing source ticket (default: 21)"
    )
    parser.add_argument("--analytics", action="store_true", help="Also test similarity and reports")
    parser.add_argument(
        "--week-start", type=monday, default="2026-10-12", help="Report's ISO Monday"
    )
    arguments = parser.parse_args()
    if arguments.ticket_id <= 0 or arguments.ticket_id > 2**63 - 1:
        parser.error("--ticket-id must be a positive signed 64-bit integer")
    raise SystemExit(asyncio.run(main(arguments)))
