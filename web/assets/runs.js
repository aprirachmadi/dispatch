import { request, queryPath } from "./api.js";
import {
  el,
  button,
  link,
  badge,
  notice,
  empty,
  field,
  select,
  facts,
  date,
  name,
  loading,
  errorBox,
  confirmAction,
  announce,
  pager,
} from "./ui.js";
import { assessmentSheet } from "./review.js";

export async function runsView(root, params, app) {
  const signal = app.signal;
  let timer,
    stopped = false,
    polling = false,
    active = null,
    posting = false,
    unknownStart = false;
  const offset = Number(params.get("offset") || 0);
  const heading = el(
    "header",
    { class: "page-heading" },
    el(
      "div",
      {},
      el("h1", {}, "Triage runs"),
      el(
        "p",
        {},
        "Inspect generation and persistence separately. One active run per database.",
      ),
    ),
  );
  const form = el("form", { class: "run-form" }),
    workspace = el("section", { "aria-label": "Run history and results" }),
    results = el("div"),
    history = el("div"),
    errors = el("div");
  root.replaceChildren(
    el(
      "div",
      { class: "page" },
      heading,
      el("div", { class: "run-layout" }, form, workspace),
    ),
  );
  workspace.append(errors, results, history);
  const scope = select(
    [
      ["ticket", "One ticket"],
      ["queue", "New-ticket queue"],
    ],
    params.has("ticket") ? "ticket" : "queue",
  );
  const ticket = el("input", {
    type: "number",
    min: 1,
    step: 1,
    value: params.get("ticket") || "",
    placeholder: "Ticket ID",
    required: scope.value === "ticket",
  });
  const limit = el("input", {
    type: "number",
    min: 1,
    max: 20,
    value: 5,
    step: 1,
  });
  const mode = select(
    [
      ["preview", "Preview — no ticket writes"],
      ["apply", "Apply — persist an unsent draft"],
    ],
    "preview",
  );
  const applyOption = mode.querySelector("[value=apply]");
  applyOption.disabled = app.config.read_only;
  const start = el(
    "button",
    { type: "submit", class: "primary" },
    "Start preview",
  );
  function controls() {
    ticket.disabled = scope.value !== "ticket" || posting;
    ticket.required = scope.value === "ticket";
    limit.disabled = scope.value === "ticket" || posting;
    scope.disabled = posting;
    mode.disabled = posting;
    start.disabled = posting || unknownStart || active?.state === "running";
    start.textContent = posting
      ? "Starting…"
      : mode.value === "apply"
        ? "Start Apply"
        : "Start preview";
  }
  scope.addEventListener("change", controls);
  mode.addEventListener("change", controls);
  form.append(
    el("h2", {}, "Generate a response"),
    field("Scope", scope),
    field("Ticket ID", ticket),
    field("Queue limit", limit),
    field("Mode", mode),
    notice(
      "Preview calls the real model and MCP retrieval. It saves job metadata, not ticket fields, drafts, or notes.",
    ),
    notice(
      "Apply regenerates; it does not save an earlier preview. Read all resulting drafts before approval.",
      "warning",
    ),
    start,
  );
  if (app.config.read_only)
    form.append(
      el("p", { class: "muted" }, "Apply is disabled by this server."),
    );
  controls();
  async function loadHistory() {
    const data = await request(
      queryPath("/triage/runs", { offset, limit: 25 }),
      { signal },
    );
    if (signal.aborted) return;
    history.replaceChildren(
      el(
        "div",
        { class: "section-head" },
        el("h2", {}, "Persisted runs"),
        button("Refresh history", () => loadHistory().catch(showError)),
      ),
    );
    if (!data.runs.length)
      history.append(
        empty(
          "No runs yet",
          "Choose a ticket or a bounded new-ticket queue and start Preview.",
        ),
      );
    else {
      const table = el(
        "table",
        {},
        el(
          "thead",
          {},
          el(
            "tr",
            {},
            ["Run / scope", "Mode", "State", "Started"].map((t) =>
              el("th", { scope: "col" }, t),
            ),
          ),
        ),
      );
      table.append(
        el(
          "tbody",
          {},
          data.runs.map((r) =>
            el(
              "tr",
              {},
              el(
                "td",
                {},
                link(r.run_id.slice(0, 8), `#runs?run=${r.run_id}`),
                el(
                  "div",
                  { class: "muted" },
                  r.ticket_id
                    ? `Ticket #${r.ticket_id}`
                    : `New queue · limit ${r.limit}`,
                ),
              ),
              el("td", {}, name(r.mode)),
              el(
                "td",
                {},
                badge(
                  name(r.state),
                  r.state === "running"
                    ? "info"
                    : r.state === "completed"
                      ? ""
                      : "warning",
                ),
              ),
              el("td", {}, date(r.started_at)),
            ),
          ),
        ),
      );
      history.append(
        el("div", { class: "table-wrap" }, table),
        pager(offset, data.next_offset, (n) =>
          app.navigate(`#runs?offset=${n}`),
        ),
      );
    }
    if (data.scan_truncated)
      history.append(
        notice(
          "Run history pagination reached its safety limit. Older runs may not appear.",
          "warning",
        ),
      );
    const running = data.runs.find((r) => r.state === "running");
    if (running && !active && !params.get("run")) await monitor(running.run_id);
    if (unknownStart) {
      errors.replaceChildren(
        notice(
          "Start outcome remains unknown. Inspect the newest persisted runs and open the matching scope/time. Do not resubmit blindly.",
          "warning",
        ),
        button("I inspected history — enable deliberate start", async () => {
          if (
            await confirmAction(
              "Start only after inspection",
              "A new start may duplicate work if the earlier request created a job. Continue only after inspecting persisted runs and any writes.",
              "Enable start",
            )
          ) {
            unknownStart = false;
            controls();
          }
        }),
      );
    }
  }
  function showError(error) {
    if (error.name !== "AbortError" && !signal.aborted)
      errors.replaceChildren(
        errorBox(error, () => loadHistory().catch(showError)),
      );
  }
  function renderRun(run) {
    const elapsed = Math.max(
      0,
      Math.round(
        ((run.finished_at ? new Date(run.finished_at) : new Date()) -
          new Date(run.started_at)) /
          1000,
      ),
    );
    results.replaceChildren(
      el(
        "div",
        { class: "section-head" },
        el("h2", {}, `${name(run.mode)} run`),
        badge(
          name(run.state),
          run.state === "running"
            ? "info"
            : run.state === "completed"
              ? ""
              : "warning",
        ),
      ),
      facts([
        ["Run ID", run.run_id],
        ["Model", run.model],
        ["Prompt", run.prompt_version],
        [
          "Scope",
          run.ticket_id
            ? `Ticket #${run.ticket_id}`
            : `New-ticket queue, limit ${run.limit}`,
        ],
        ["Started", date(run.started_at)],
        ["Elapsed", `${elapsed}s`],
        ["Last read", date(new Date().toISOString())],
      ]),
    );
    if (run.state === "running")
      results.append(
        notice(
          "Monitoring the backend job. Leaving this page does not cancel it.",
        ),
        button("Pause monitoring", () => {
          stopped = true;
          clearTimeout(timer);
          results.append(
            notice("Monitoring paused. The server job continues."),
            button("Resume monitoring", () => {
              stopped = false;
              monitor(run.run_id).catch(showError);
            }),
          );
        }),
      );
    if (run.message) results.append(notice(run.message, "warning"));
    if (!run.results.length)
      results.append(
        empty(
          "No ticket results yet",
          run.state === "running"
            ? "Waiting for the bounded workflow. No percentage or step progress is reported."
            : "Inspect the run state and audit metadata.",
        ),
      );
    for (const result of run.results) {
      const item = el(
        "article",
        { class: "run-result" },
        el(
          "h3",
          {},
          link(
            `Ticket #${result.ticket_id}`,
            `#tickets?ticket=${result.ticket_id}&status=`,
          ),
          badge(
            name(result.state),
            ["failed", "partial"].includes(result.state) ? "warning" : "",
          ),
        ),
      );
      if (result.state === "preview")
        item.append(
          notice(
            "Preview — not saved to ticket. This response cannot be approved here.",
          ),
        );
      if (result.state === "applied")
        item.append(
          notice(
            "Apply completed. Open the ticket to re-read persisted assessment and drafts before review.",
          ),
        );
      if (result.state === "skipped")
        item.append(
          notice(
            "Skipped — this result does not prove a new draft was generated.",
          ),
        );
      if (result.message) item.append(notice(result.message, "warning"));
      if (result.assessment)
        item.append(
          assessmentSheet(
            { ...result, requires_attention: undefined },
            result.state === "preview"
              ? "Preview assessment"
              : "Run assessment",
          ),
        );
      if (result.draft)
        item.append(
          el(
            "h3",
            {},
            result.state === "preview"
              ? "Preview response — unsent"
              : "Generated response — inspect saved draft",
          ),
          el("p", { class: "body-text" }, result.draft.body),
        );
      if (result.operations.length)
        item.append(
          el("h3", {}, "Write journal"),
          el(
            "ul",
            {},
            result.operations.map((op) =>
              el(
                "li",
                {},
                `${op.tool}: ${op.outcome}${op.record_id ? ` · record #${op.record_id}` : ""}`,
              ),
            ),
          ),
          notice(
            "Started or uncertain operations may already have committed. Do not automatically repeat Apply.",
            "warning",
          ),
        );
      results.append(item);
    }
  }
  async function monitor(id) {
    if (polling || signal.aborted || stopped) return;
    polling = true;
    clearTimeout(timer);
    try {
      const run = await request(`/triage/runs/${encodeURIComponent(id)}`, {
        signal,
      });
      if (signal.aborted) return;
      active = run;
      renderRun(run);
      controls();
      if (run.state === "running" && !stopped)
        timer = setTimeout(() => monitor(id), 2000);
      else {
        announce(`Triage run ${run.state}. Inspect individual ticket results.`);
        await loadHistory();
      }
    } catch (error) {
      if (error.name !== "AbortError" && !signal.aborted) {
        stopped = true;
        showError(error);
        results.append(
          notice("Disconnected. The last known job may still be running."),
          button("Resume monitoring", () => {
            stopped = false;
            errors.replaceChildren();
            monitor(id);
          }),
        );
      }
    } finally {
      polling = false;
    }
  }
  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    if (posting || unknownStart || active?.state === "running") return;
    const ticketId = scope.value === "ticket" ? Number(ticket.value) : null;
    if (
      scope.value === "ticket" &&
      (!Number.isSafeInteger(ticketId) || ticketId < 1)
    ) {
      showError(new Error("Enter a positive, safe integer ticket ID."));
      return;
    }
    const count = Number(limit.value);
    if (
      scope.value === "queue" &&
      (!Number.isInteger(count) || count < 1 || count > 20)
    ) {
      showError(new Error("Queue limit must be 1–20."));
      return;
    }
    if (
      mode.value === "apply" &&
      !(await confirmAction(
        "Apply triage to this scope?",
        `Regenerate for ${ticketId ? `ticket #${ticketId}` : `up to ${count} new tickets`}; update category/priority; create an unsent draft and completion note. This is not saving an earlier preview. No sending, approval, closing, or remediation.`,
        "Start Apply",
      ))
    )
      return;
    posting = true;
    controls();
    errors.replaceChildren();
    try {
      const run = await request("/triage/runs", {
        method: "POST",
        body: {
          mode: mode.value,
          ticket_id: ticketId,
          limit: ticketId ? 1 : count,
        },
      });
      if (signal.aborted) return;
      active = run;
      history.replaceChildren();
      app.navigate(`#runs?run=${run.run_id}`);
    } catch (error) {
      if (signal.aborted) return;
      unknownStart = error.uncertain;
      showError(error);
      if (error.status === 409 || unknownStart)
        await loadHistory().catch(showError);
    } finally {
      posting = false;
      controls();
    }
  });
  history.append(loading("Loading persisted runs…"));
  await loadHistory();
  if (params.get("run")) await monitor(params.get("run"));
  return () => {
    stopped = true;
    clearTimeout(timer);
  };
}
