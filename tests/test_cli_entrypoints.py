"""Application commands, not examples; no live models or user database access."""

import json
import subprocess
import sys
from argparse import Namespace
from pathlib import Path

import httpx2
import pytest

from dispatch.agent import cli as triage_cli
from dispatch.agent.config import ModelSettings
from dispatch.agent.llm import LLMError
from dispatch.agent.models import Assessment, Draft, TriageResult
from dispatch.evaluation import cli as eval_cli
from dispatch.mcp_server.config import MCPSettings
from dispatch.mock_psa import checks

ROOT = Path(__file__).resolve().parents[1]
MODULES = ("dispatch.agent.cli", "dispatch.evaluation.cli", "dispatch.mock_psa.checks")


@pytest.mark.parametrize("module", MODULES)
def test_module_help_is_executable_without_configuration_or_files(module, tmp_path):
    result = subprocess.run(
        [sys.executable, "-m", module, "--help"],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=20,
    )
    assert result.returncode == 0, result.stderr
    assert "usage:" in result.stdout and "--help" in result.stdout
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize(
    "entrypoint,arguments",
    [
        (triage_cli.triage_main, ["--limit", "0"]),
        (triage_cli.triage_main, ["--ticket-id", str(2**63)]),
        (triage_cli.triage_main, ["--all", "--ticket-id", "31"]),
        (triage_cli.triage_main, ["--all", "--limit", "20"]),
        (eval_cli.eval_main, ["--limit", "101"]),
        (eval_cli.eval_main, ["--calibrate-judge", "--no-judge"]),
        (checks.checks_main, ["--week-start", "2026-10-13"]),
        (checks.checks_main, ["--week-start", "9999-12-27"]),
        (checks.checks_main, ["--wait-seconds", "9"]),
        (checks.checks_main, ["--company-id", "0"]),
    ],
)
def test_cli_validation_before_execution(entrypoint, arguments, monkeypatch):
    monkeypatch.setattr(sys, "argv", ["dispatch", *arguments])
    with pytest.raises(SystemExit) as error:
        entrypoint()
    assert error.value.code == 2


def test_eval_module_validates_dataset_without_models(tmp_path):
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "dispatch.evaluation.cli",
            "--validate-only",
            "--dataset",
            str(ROOT / "evals/triage.jsonl"),
        ],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=20,
    )
    assert result.returncode == 0, result.stderr
    assert "VALID: 40 cases" in result.stdout
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("apply", [False, True])
@pytest.mark.parametrize("state", ["success", "failed", "partial"])
def test_triage_command_uses_shared_service_and_checkpoints(
    tmp_path, monkeypatch, capsys, apply, state
):
    settings = ModelSettings(base_url="http://127.0.0.1:9999/v1", model="synthetic-test")
    monkeypatch.setattr(triage_cli, "load_model_settings", lambda: settings)
    monkeypatch.setattr(triage_cli, "load_settings", lambda: MCPSettings(read_only=not apply))
    assessment = Assessment(
        category="security",
        priority="urgent",
        urgency="immediate",
        security_flag=True,
        rationale="Synthetic credential exposure.",
    )
    result = TriageResult(
        ticket_id=31,
        state=("applied" if apply else "preview") if state == "success" else state,
        assessment=assessment,
        draft=Draft(body="Synthetic unsent draft.") if state == "success" else None,
    )

    async def run(model_settings, mcp_settings, **options):
        assert model_settings is settings
        assert mcp_settings.psa_base_url == "http://127.0.0.1:9998"
        assert options["apply"] is apply and options["ticket_id"] == 31
        options["progress"](result)
        return [result]

    monkeypatch.setattr(triage_cli, "run_connected", run)
    output = tmp_path / "triage.json"
    argv = [
        "dispatch",
        "--ticket-id",
        "31",
        "--base-url",
        "http://127.0.0.1:9998",
        "--output",
        str(output),
    ]
    monkeypatch.setattr(sys, "argv", [*argv, "--apply"] if apply else argv)
    with pytest.raises(SystemExit) as error:
        triage_cli.triage_main()
    assert error.value.code == (0 if state == "success" else 1)
    report = json.loads(output.read_text())
    assert report["state"] == ("completed" if state == "success" else "incomplete")
    assert report["mode"] == ("apply" if apply else "preview")
    assert report["results"] == [result.model_dump(mode="json")]
    assert "ATTENTION" in capsys.readouterr().out


def test_triage_apply_denied_before_service_or_report(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(
        triage_cli,
        "load_model_settings",
        lambda: ModelSettings(base_url="http://127.0.0.1:9999", model="synthetic"),
    )
    monkeypatch.setattr(triage_cli, "load_settings", MCPSettings)
    output = tmp_path / "denied.json"
    monkeypatch.setattr(sys, "argv", ["dispatch", "--apply", "--output", str(output)])
    with pytest.raises(SystemExit) as error:
        triage_cli.triage_main()
    assert error.value.code == 1 and not output.exists()
    assert "No automatic retries" in capsys.readouterr().err


@pytest.mark.parametrize("apply", [False, True])
def test_triage_all_cli_checkpoints_every_result(tmp_path, monkeypatch, apply):
    monkeypatch.setattr(
        triage_cli,
        "load_model_settings",
        lambda: ModelSettings(base_url="http://127.0.0.1:9999", model="synthetic"),
    )
    monkeypatch.setattr(triage_cli, "load_settings", lambda: MCPSettings(read_only=not apply))

    async def run(model_settings, mcp_settings, **options):
        assert options["all_tickets"] and options["ticket_id"] is None
        assert options["apply"] is apply
        results = [
            TriageResult(ticket_id=i, state="applied" if apply else "preview") for i in range(1, 26)
        ]
        for result in results:
            options["progress"](result)
        return results

    monkeypatch.setattr(triage_cli, "run_connected", run)
    output = tmp_path / "all.json"
    args = ["dispatch", "--all", "--output", str(output)]
    monkeypatch.setattr(sys, "argv", args + (["--apply"] if apply else []))
    with pytest.raises(SystemExit) as error:
        triage_cli.triage_main()
    assert error.value.code == 0
    report = json.loads(output.read_text())
    assert report["scope"] == "all" and report["state"] == "completed"
    assert [r["ticket_id"] for r in report["results"]] == list(range(1, 26))


@pytest.mark.parametrize("failure", [None, "draft"])
def test_eval_command_generation_scores_and_incomplete_exit(tmp_path, monkeypatch, capsys, failure):
    monkeypatch.chdir(tmp_path)
    settings_requests = []

    def settings(*, judge=False):
        settings_requests.append(judge)
        return ModelSettings(base_url="http://127.0.0.1:9999", model="synthetic")

    class Model:
        def __init__(self, settings):
            self.calls = 0

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

        async def generate(self, system, user, schema):
            self.calls += 1
            assert "expected" not in json.loads(user)
            if schema is Assessment:
                return Assessment(
                    category="email",
                    priority="medium",
                    urgency="same_day",
                    security_flag=False,
                    rationale="Synthetic test prediction.",
                )
            if failure:
                raise LLMError("Synthetic draft failure.")
            return Draft(body="Synthetic generated reply.")

    monkeypatch.setattr(eval_cli, "load_model_settings", settings)
    monkeypatch.setattr(eval_cli, "OpenAICompatibleClient", Model)
    output = tmp_path / "eval.json"
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "dispatch",
            "--dataset",
            str(ROOT / "evals/triage.jsonl"),
            "--limit",
            "1",
            "--no-judge",
            "--output",
            str(output),
        ],
    )
    with pytest.raises(SystemExit) as error:
        eval_cli.eval_main()
    assert error.value.code == (1 if failure else 0)
    report = json.loads(output.read_text())
    assert settings_requests == [False]  # No judge fallback/configuration.
    assert report["state"] == ("incomplete" if failure else "completed")
    assert report["subset"] is True and report["evaluated_cases"] == 1
    assert report["summary"]["classification"]["valid_predictions"] == 1
    assert report["summary"]["draft_completion"]["valid"] == (0 if failure else 1)
    assert report["summary"]["draft_quality"]["judge_requested"] is False
    assert "No PSA access or mutations" in capsys.readouterr().out


def check_args(**overrides):
    return Namespace(
        **{
            "base_url": "http://testserver",
            "ticket_id": 1,
            "week_start": "2026-10-12",
            "review": False,
            "company_id": 1,
            "triage": None,
            "wait_seconds": 10,
            **overrides,
        }
    )


@pytest.mark.anyio
async def test_operational_reads_leave_records_unchanged(client, capsys):
    before = client.get("/tickets/1").json()
    await checks.run_checks(check_args(), transport=httpx2.ASGITransport(app=client.app))
    assert client.get("/tickets/1").json() == before
    assert client.get("/tickets/1/drafts").json() == []
    assert client.get("/audit-log").json()["events"] == []
    assert "7/7 checks passed" in capsys.readouterr().out


@pytest.mark.anyio
async def test_operational_review_retains_only_unsent_synthetic_records(write_client, capsys):
    await checks.run_checks(
        check_args(review=True), transport=httpx2.ASGITransport(app=write_client.app)
    )
    tickets = write_client.get("/tickets").json()
    created = tickets[-1]
    assert created["status"] == "new"
    drafts = write_client.get(f"/tickets/{created['id']}/drafts").json()
    assert len(drafts) == 1 and drafts[0]["status"] == "approved"
    assert drafts[0]["revision"] == 3 and drafts[0]["approved_at"] is not None
    assert "14/14 checks passed" in capsys.readouterr().out


@pytest.mark.anyio
@pytest.mark.parametrize(
    "mode,state", [("preview", "preview"), ("apply", "applied"), ("preview", "skipped")]
)
async def test_operational_job_polling_requires_generation_and_never_resubmits(mode, state, capsys):
    posts = []

    def respond(request):
        if request.method == "POST":
            posts.append(json.loads(request.content))
            return httpx2.Response(202, json={"run_id": "synthetic-run"})
        if request.url.path == "/triage/runs/synthetic-run":
            return httpx2.Response(
                200,
                json={
                    "state": "completed",
                    "results": [
                        {
                            "ticket_id": 1,
                            "state": state,
                            "assessment": {"category": "network"},
                            "draft": {"body": "Synthetic unsent reply."},
                            "message": None,
                        }
                    ],
                },
            )
        if request.url.params.get("week_start") == "2026-10-13":
            return httpx2.Response(422, json={})
        return httpx2.Response(200, json={})

    args = check_args(triage=mode)
    if state == "skipped":
        with pytest.raises(RuntimeError):
            await checks.run_checks(args, transport=httpx2.MockTransport(respond))
    else:
        await checks.run_checks(args, transport=httpx2.MockTransport(respond))
        assert "9/9 checks passed" in capsys.readouterr().out
    assert posts == [{"mode": mode, "ticket_id": 1, "limit": 1}]


def test_operational_command_failure_is_nonzero_and_redacted(monkeypatch, capsys):
    async def fail(args):
        raise RuntimeError("private synthetic backend body")

    monkeypatch.setattr(checks, "run_checks", fail)
    monkeypatch.setattr(sys, "argv", ["dispatch"])
    with pytest.raises(SystemExit) as error:
        checks.checks_main()
    assert error.value.code == 1
    stderr = capsys.readouterr().err
    assert "private synthetic" not in stderr and "no automatic retries" in stderr
