"""CLI composition; reusable workflow stays separate from terminal/process concerns."""

import argparse
import asyncio
import os
import sys
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from mcp import Client
from mcp.client.stdio import StdioServerParameters
from mcp.shared.exceptions import MCPError
from pydantic import ValidationError

from dispatch.agent.config import load_model_settings
from dispatch.agent.llm import LLMError, OpenAICompatibleClient
from dispatch.agent.prompts import PROMPT_VERSION
from dispatch.agent.reporting import RunFile
from dispatch.agent.workflow import MCPGateway, WorkflowError, run_batch
from dispatch.mcp_server.config import load_settings


def triage_main() -> None:
    parser = argparse.ArgumentParser(
        description="Run real LLM triage; defaults to read-only preview."
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Persist allowed writes; requires DISPATCH_READ_ONLY=false",
    )
    parser.add_argument(
        "--ticket-id", type=int, help="One existing new ticket instead of the queue"
    )
    parser.add_argument(
        "--limit", type=int, default=5, help="Process at most 1–20 untriaged tickets"
    )
    parser.add_argument("--base-url", help="Override the PSA REST URL (not the model endpoint)")
    parser.add_argument(
        "--output", type=Path, help="New local JSON run file; never overwrite existing files"
    )
    arguments = parser.parse_args()
    if not 1 <= arguments.limit <= 20:
        parser.error("--limit must be 1–20")
    if arguments.ticket_id is not None and not 1 <= arguments.ticket_id <= 2**63 - 1:
        parser.error("--ticket-id must be a positive signed 64-bit integer")
    try:
        code = asyncio.run(_triage(arguments))
    except (
        ValueError,
        ValidationError,
        WorkflowError,
        LLMError,
        OSError,
        MCPError,
        ExceptionGroup,
    ):
        parser.exit(
            1,
            "Triage stopped: check model/backend configuration, audit and run report. "
            "No automatic retries.\n",
        )
    raise SystemExit(code)


async def _triage(arguments) -> int:
    model_settings = load_model_settings()
    mcp_settings = load_settings()
    if arguments.base_url:
        mcp_settings = type(mcp_settings)(
            **{**vars(mcp_settings), "psa_base_url": arguments.base_url}
        )
    if arguments.apply and mcp_settings.read_only:
        raise WorkflowError(
            "Apply requires DISPATCH_READ_ONLY=false; restart the backend in that mode too."
        )
    run_id = str(uuid4())
    report = RunFile(arguments.output or Path(f"data/triage-{run_id}.json"))
    payload = {
        "run_id": run_id,
        "started_at": datetime.now(UTC).isoformat(),
        "state": "running",
        "mode": "apply" if arguments.apply else "preview",
        "model": model_settings.model,
        "prompt_version": PROMPT_VERSION,
        "results": [],
    }
    report.write(payload)
    print(f"Mode: {payload['mode']}. Local run report: {report.path}")

    def progress(result):
        records = {item["ticket_id"]: item for item in payload["results"]}
        records[result.ticket_id] = result.model_dump(mode="json")
        payload["results"] = list(records.values())
        report.write(payload)

    environment = {
        **os.environ,
        "DISPATCH_READ_ONLY": "false" if arguments.apply else "true",
        "DISPATCH_PSA_BASE_URL": mcp_settings.psa_base_url,
    }
    parameters = StdioServerParameters(
        command=sys.executable,
        args=["-m", "dispatch.mcp_server.server"],
        env=environment,
    )
    try:
        async with (
            OpenAICompatibleClient(model_settings) as model,
            Client(parameters, read_timeout_seconds=180) as client,
        ):
            results = await run_batch(
                MCPGateway(client, writes_allowed=arguments.apply),
                model,
                limit=arguments.limit,
                ticket_id=arguments.ticket_id,
                apply=arguments.apply,
                progress=progress,
            )
        for result in results:
            print(f"\nTicket #{result.ticket_id}: {result.state}")
            if result.assessment:
                print(result.assessment.model_dump_json(indent=2))
                if result.assessment.requires_attention:
                    print("ATTENTION: urgent/security assessment — human review required.")
            if result.draft:
                print("DRAFT (unsent):\n" + result.draft.body)
            if result.message:
                print(result.message)
        failed = any(result.state in {"failed", "partial"} for result in results)
        payload["state"] = "incomplete" if failed else "completed"
        return 1 if failed else 0
    finally:
        if payload["state"] == "running":
            payload["state"] = "interrupted_or_failed"
        payload["finished_at"] = datetime.now(UTC).isoformat()
        report.write(payload)
