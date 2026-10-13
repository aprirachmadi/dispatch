"""Editable LLM-stage probe: poetry run python examples/run_triage.py.

Requires configured agent model; two real requests, potentially billable.
No API, MCP, retrieval, persistence, approval or sending. For the full workflow use
python -m dispatch.agent.cli (see README). Keep all edited inputs synthetic.
"""

import asyncio
import sys
from datetime import UTC, datetime

from dispatch.agent.config import load_model_settings
from dispatch.agent.llm import LLMError, OpenAICompatibleClient
from dispatch.agent.models import Labels, Problem
from dispatch.agent.prompts import classify, draft_response

PROBLEM = Problem(
    subject="sent my password to a fake support page",
    description="I entered my work password on a helpdesk link ten minutes ago. "
    "Now someone else seems to be signing in. Email still works though.",
    company_name="Synthetic Maple Studio",
    sla_tier="gold",
    created_at=datetime(2026, 10, 12, 9, tzinfo=UTC),
)
EXPECTED = Labels(category="security", priority="urgent", urgency="immediate", security_flag=True)
HISTORY = []  # Editable frozen prior resolutions; this probe does NOT retrieve via MCP.
ASSERT_EXPECTED = False  # Optional regression assertion, not a promise of model accuracy.


async def main() -> None:
    print("LLM stages only; no MCP workflow or saved records. Synthetic input:")
    print(PROBLEM.model_dump_json(indent=2))
    print("Expected classification (not sent to the model):")
    print(EXPECTED.model_dump_json(indent=2))
    print("Frozen draft history:", [item.model_dump(mode="json") for item in HISTORY])
    async with OpenAICompatibleClient(load_model_settings()) as model:
        assessment = await classify(model, PROBLEM)
        print("Generated classification / intermediate draft input:")
        print(assessment.model_dump_json(indent=2))
        print("Attention required (derived by code):", assessment.requires_attention)
        matches = all(
            getattr(assessment, field) == getattr(EXPECTED, field) for field in Labels.model_fields
        )
        print("Matches expected fields:", matches)
        draft = await draft_response(model, PROBLEM, assessment, HISTORY)
        print("Generated draft (unsent; human review required):\n" + draft.body)
        if ASSERT_EXPECTED:
            assert matches, "Classification regression: inspect expected/generated fields."


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (ValueError, LLMError):
        print("Probe stopped: check model configuration/output. No retries.", file=sys.stderr)
        raise SystemExit(1) from None
