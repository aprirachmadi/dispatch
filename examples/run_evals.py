"""One editable labeled probe: poetry run python examples/run_evals.py.

Two real generator requests and, by default, two separate judge requests.
Requires agent/judge configuration and poetry install --with evals for judging.
Set JUDGE_ENABLED=False for generation only. No API/MCP/PSA access or report files.
For dataset runs/calibration use python -m dispatch.evaluation.cli (see README).
"""

import asyncio
import json
import sys
from datetime import UTC, datetime

from dispatch.agent.config import load_model_settings
from dispatch.agent.llm import LLMError, OpenAICompatibleClient
from dispatch.agent.models import Draft, Labels, Problem
from dispatch.evaluation.dataset import EvalCase
from dispatch.evaluation.runner import evaluate_cases, summarize

CASE = EvalCase(
    case_id="synthetic_vpn_blockage",
    problem=Problem(
        subject="VPN stopped working for me",
        description="Only my laptop cannot connect to the office VPN today. "
        "My internet works and colleagues can connect. I cannot access work files.",
        company_name="Synthetic Brook Design",
        sla_tier="silver",
        created_at=datetime(2026, 10, 12, 9, tzinfo=UTC),
    ),
    history=[],  # Frozen context, not live retrieval; prior resolutions can be added here.
    expected=Labels(category="network", priority="medium", urgency="same_day", security_flag=False),
    reference_draft=Draft(
        body="Sorry the VPN is blocking your work. Please share the error message and when "
        "it started, without passwords or codes, so a technician can investigate today."
    ),
    response_requirements=[
        "Acknowledge the single-user VPN blockage; ask for nonsecret diagnostic information.",
        "Do not claim a site outage, completed fix or request passwords/MFA codes.",
    ],
)
JUDGE_ENABLED = True


async def main() -> None:
    print("One synthetic labeled case; expected/reference fields never enter generation:")
    print(CASE.model_dump_json(indent=2))
    agent_settings = load_model_settings()
    judge_settings = load_model_settings(judge=True) if JUDGE_ENABLED else None
    async with OpenAICompatibleClient(agent_settings) as model:
        if JUDGE_ENABLED:
            from dispatch.evaluation.judge import DraftJudge

            async with DraftJudge(judge_settings) as judge:
                rows = await evaluate_cases([CASE], model, judge=judge)
        else:
            rows = await evaluate_cases([CASE], model)
    row = rows[0]
    print("Expected fields:", json.dumps(row["expected"], indent=2))
    print("Generated assessment:", json.dumps(row["assessment"], indent=2))
    print("Generated draft (unsent):", json.dumps(row["draft"], indent=2))
    print("Stage states:", row["classification_state"], row["draft_state"])
    if row["error"]:
        print("Generation error:", row["error"])
    summary = summarize([CASE], rows, judge_requested=JUDGE_ENABLED)
    print("Classification scores:", json.dumps(summary["classification"], indent=2))
    print("Draft completion:", json.dumps(summary["draft_completion"], indent=2))
    print(
        "Separate judge scores/reasons (empty when disabled):", json.dumps(row["judge"], indent=2)
    )
    print("Judge coverage:", json.dumps(summary["draft_quality"], indent=2))
    print("One case is not an accuracy estimate; all-class macro-F1 includes absent classes.")
    if row["draft_state"] != "valid" or any(
        score["state"] != "assessed" for score in row["judge"].values()
    ):
        raise SystemExit(1)  # Missing outputs/judge failures, not low quality scores.


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (ImportError, ValueError, LLMError):
        print(
            "Probe stopped: check model/judge configuration and evals dependencies.",
            file=sys.stderr,
        )
        raise SystemExit(1) from None
