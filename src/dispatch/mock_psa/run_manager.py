"""One local asynchronous job, with a durable journal and no automatic replay/retries."""

import asyncio
import json
import logging
from dataclasses import replace
from uuid import uuid4

from fastapi import HTTPException
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError, SQLAlchemyError

from dispatch.agent.config import load_model_settings
from dispatch.agent.prompts import PROMPT_VERSION
from dispatch.agent.service import run_connected
from dispatch.mock_psa.console_schemas import RunResponse, StartTriage
from dispatch.mock_psa.database import utc_now
from dispatch.mock_psa.models import BackendAudit, TriageRun


def run_response(record: TriageRun) -> RunResponse:
    return RunResponse(
        run_id=record.id,
        state=record.state,
        mode=record.mode,
        ticket_id=record.ticket_id,
        limit=record.limit,
        model=record.model,
        prompt_version=record.prompt_version,
        started_at=record.started_at,
        finished_at=record.finished_at,
        message=record.message,
        results=json.loads(record.results_json),
    )


class RunManager:
    def __init__(self, session_factory, mcp_settings, *, read_only: bool):
        self.sessions = session_factory
        self.settings = replace(mcp_settings, read_only=read_only)
        self.task: asyncio.Task | None = None
        self.runner = run_connected
        self.model_loader = load_model_settings

    def recover(self) -> None:
        """Single-worker startup marks abandoned jobs interrupted; never resume writes."""
        with self.sessions.begin() as session:
            abandoned = session.scalars(select(TriageRun).where(TriageRun.active_slot == 1)).all()
            for record in abandoned:
                record.state = "interrupted"
                record.active_slot = None
                record.finished_at = utc_now()
                record.message = (
                    "Server stopped; inspect partial/uncertain writes before rerunning."
                )
                session.add(BackendAudit(action="triage_interrupted", run_id=record.id))

    async def start(self, request: StartTriage) -> RunResponse:
        if request.mode == "apply" and self.settings.read_only:
            raise HTTPException(403, "Apply is disabled in read-only mode")
        with self.sessions() as session:
            if session.scalar(select(TriageRun.id).where(TriageRun.active_slot == 1)):
                raise HTTPException(409, "A triage job is already active")
        try:
            model = self.model_loader()
        except ValueError:
            raise HTTPException(
                503, "Configure the server's OPENAI_BASE_URL and OPENAI_MODEL first"
            ) from None
        record = TriageRun(
            id=str(uuid4()),
            active_slot=1,
            state="running",
            mode=request.mode,
            ticket_id=request.ticket_id,
            limit=request.limit,
            model=model.model,
            prompt_version=PROMPT_VERSION,
        )
        try:
            with self.sessions.begin() as session:
                session.add(record)
                session.add(BackendAudit(action="triage_started", run_id=record.id))
                session.flush()
                response = run_response(record)
        except IntegrityError:
            raise HTTPException(409, "A triage job is already active") from None
        self.task = asyncio.create_task(self._execute(response.run_id, request, model))
        return response

    def _progress(self, run_id, result):
        # Commit each write intention before the workflow attempts its mutation.
        with self.sessions.begin() as session:
            record = session.get(TriageRun, run_id)
            records = {item["ticket_id"]: item for item in json.loads(record.results_json)}
            records[result.ticket_id] = result.model_dump(mode="json")
            record.results_json = json.dumps(list(records.values()), allow_nan=False)

    def _finish(self, run_id, state, message=None):
        with self.sessions.begin() as session:
            changed = session.execute(
                update(TriageRun)
                .where(TriageRun.id == run_id, TriageRun.state == "running")
                .values(state=state, active_slot=None, finished_at=utc_now(), message=message)
            ).rowcount
            if changed:
                session.add(BackendAudit(action=f"triage_{state}", run_id=run_id))

    def _safe_finish(self, run_id, state, message=None):
        try:
            self._finish(run_id, state, message)
        except SQLAlchemyError:
            # Retain the durable active claim if storage fails: do not enable another run.
            # A later single-worker restart can recover it. Never log SQL parameters/results.
            logging.getLogger(__name__).error(
                "Triage journal unavailable; stop server and inspect storage before restarting."
            )

    async def _execute(self, run_id, request, model):
        try:
            results = await self.runner(
                model,
                self.settings,
                apply=request.mode == "apply",
                limit=request.limit,
                ticket_id=request.ticket_id,
                progress=lambda result: self._progress(run_id, result),
            )
            for result in results:
                self._progress(run_id, result)
            state = (
                "incomplete"
                if any(r.state in {"failed", "partial"} for r in results)
                else "completed"
            )
            self._safe_finish(run_id, state)
        except asyncio.CancelledError:
            self._safe_finish(
                run_id,
                "interrupted",
                "Server stopped; inspect any partial writes before rerunning.",
            )
            raise
        except Exception:
            # No raw exception/provider body in API, DB, logs, or audit.
            self._safe_finish(
                run_id,
                "failed",
                "Triage failed; inspect run results and MCP audit. No automatic retries.",
            )

    async def close(self):
        if self.task is not None and not self.task.done():
            self.task.cancel()
            try:
                await self.task
            except asyncio.CancelledError:
                pass
