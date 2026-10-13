"""Operational REST checks. Run with python -m dispatch.mock_psa.checks.

Default: read-only checks, no LLM required.
--review: create a synthetic test ticket/draft, edit, approve (unsent); retains these records.
--triage preview|apply: start/poll a real LLM job; apply stores draft/classification/notes.
Writes require DISPATCH_READ_ONLY=false in the running backend. No automatic retries.
"""

import argparse
import asyncio
import sys
from datetime import date, timedelta

import httpx2

from dispatch.mcp_server.config import load_settings


async def run_checks(args, *, transport=None):
    passed = total = 0

    def check(label, condition):
        nonlocal passed, total
        total += 1
        passed += bool(condition)
        print(f"{'PASS' if condition else 'FAIL'}: {label}")
        if not condition:
            raise RuntimeError("Check failed; stop and inspect before rerunning writes.")

    async with httpx2.AsyncClient(
        base_url=(args.base_url or load_settings().psa_base_url).rstrip("/") + "/",
        transport=transport,
        timeout=10,
        follow_redirects=False,
        trust_env=False,
    ) as client:
        for path in (
            "health",
            f"tickets/{args.ticket_id}/drafts",
            "audit-log?source=backend",
            "audit-log?source=mcp",
            f"reports/weekly?week_start={args.week_start}",
        ):
            response = await client.get(path)
            check("GET /" + path, response.status_code == 200)
        response = await client.get(f"tickets/{args.ticket_id}/assessment")
        check("Saved assessment exists or is explicitly absent", response.status_code in (200, 404))
        tuesday = (date.fromisoformat(args.week_start) + timedelta(days=1)).isoformat()
        response = await client.get("reports/weekly", params={"week_start": tuesday})
        check("Non-Monday report rejected", response.status_code == 422)

        if args.review:
            print("REVIEW: creating retained synthetic records. Approval does NOT send a reply.")
            response = await client.post(
                "tickets",
                json={
                    "company_id": args.company_id,
                    "subject": "Synthetic console review smoke test",
                    "description": "Invented test ticket; no real employee data.",
                },
            )
            check("Synthetic ticket created", response.status_code == 201)
            ticket_id = response.json()["id"]
            response = await client.post(
                f"tickets/{ticket_id}/drafts", json={"body": "Synthetic unsent reply for review."}
            )
            check("Unsent draft created", response.status_code == 201)
            draft_id = response.json()["id"]
            path = f"tickets/{ticket_id}/drafts/{draft_id}"
            response = await client.patch(
                path, json={"revision": 1, "body": "Technician-edited synthetic reply."}
            )
            check(
                "Draft edited with revision 1",
                response.status_code == 200 and response.json()["revision"] == 2,
            )
            response = await client.post(path + "/approve", json={"revision": 1})
            check("Stale approval rejected", response.status_code == 409)
            response = await client.post(path + "/approve", json={"revision": 2})
            check(
                "Reviewed revision approved, unsent",
                response.status_code == 200 and response.json()["status"] == "approved",
            )
            response = await client.post(path + "/approve", json={"revision": 3})
            check("Repeat approval rejected", response.status_code == 409)
            response = await client.get(f"tickets/{ticket_id}")
            check(
                "Approval did not close/send/change ticket status",
                response.status_code == 200 and response.json()["status"] == "new",
            )
            print(f"Retained synthetic ticket #{ticket_id}, draft #{draft_id}.")

        if args.triage:
            print(
                f"TRIAGE: {args.triage}. Real LLM requests; "
                "first similarity use may download artifacts."
            )
            response = await client.post(
                "triage/runs", json={"mode": args.triage, "ticket_id": args.ticket_id, "limit": 1}
            )
            check("Background run accepted (202)", response.status_code == 202)
            run_id = response.json()["run_id"]
            print(
                f"Run: {run_id}; polling for up to {args.wait_seconds}s. "
                "Ctrl+C does not cancel the server job."
            )
            deadline = asyncio.get_running_loop().time() + args.wait_seconds
            while True:
                response = await client.get(f"triage/runs/{run_id}")
                response.raise_for_status()
                result = response.json()
                if result["state"] != "running":
                    break
                if asyncio.get_running_loop().time() >= deadline:
                    raise RuntimeError(
                        f"Polling timed out; inspect GET /triage/runs/{run_id}. "
                        "Do not resubmit blindly."
                    )
                await asyncio.sleep(1)
            print("Final state:", result["state"])
            for item in result["results"]:
                print(f"Ticket #{item['ticket_id']}: {item['state']}")
                if item["assessment"]:
                    print("Assessment:", item["assessment"])
                if item["draft"]:
                    print("DRAFT (unsent):", item["draft"]["body"])
                if item["message"]:
                    print(item["message"])
            expected = "preview" if args.triage == "preview" else "applied"
            check(
                "Completed generation with assessment AND draft (not a skipped ticket)",
                result["state"] == "completed"
                and any(
                    item["state"] == expected and item["assessment"] and item["draft"]
                    for item in result["results"]
                ),
            )
    print(f"\n{passed}/{total} checks passed.")


def checks_main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", help="PSA URL, defaults to DISPATCH_PSA_BASE_URL/.env")
    parser.add_argument(
        "--ticket-id", type=int, default=31, help="Existing untriaged new ticket for real triage"
    )
    parser.add_argument(
        "--week-start", default="2026-10-12", help="ISO Monday for the weekly report"
    )
    parser.add_argument(
        "--review", action="store_true", help="Create/edit/approve synthetic review records"
    )
    parser.add_argument(
        "--company-id", type=int, default=1, help="Existing synthetic company for --review"
    )
    parser.add_argument(
        "--triage", choices=["preview", "apply"], help="Run a real LLM background job"
    )
    parser.add_argument(
        "--wait-seconds", type=int, default=900, help="Polling timeout, 10–3600 seconds"
    )
    arguments = parser.parse_args()
    try:
        monday = date.fromisoformat(arguments.week_start)
        if monday.weekday() != 0 or monday.isoformat() != arguments.week_start:
            raise ValueError
        monday + timedelta(days=7)
    except (ValueError, OverflowError):
        parser.error("--week-start must be an ISO Monday with a representable following week")
    if not 10 <= arguments.wait_seconds <= 3600:
        parser.error("--wait-seconds must be 10–3600")
    if any(not 1 <= value <= 2**63 - 1 for value in (arguments.ticket_id, arguments.company_id)):
        parser.error("Ticket/company IDs must be positive signed 64-bit integers")
    try:
        asyncio.run(run_checks(arguments))
    except (RuntimeError, ValueError, OSError, httpx2.HTTPError):
        print(
            "Smoke check stopped. Inspect configuration/API/run/audit; no automatic retries.",
            file=sys.stderr,
        )
        raise SystemExit(1) from None


if __name__ == "__main__":
    checks_main()
