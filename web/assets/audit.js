import { request, queryPath } from "./api.js";
import {
  el,
  button,
  link,
  badge,
  notice,
  empty,
  date,
  name,
  loading,
  errorBox,
  pager,
} from "./ui.js";
export async function auditView(root, params, app) {
  const source = params.get("source") || "backend",
    offset = Number(params.get("offset") || 0),
    signal = app.signal;
  const dataRoot = el("section");
  root.replaceChildren(
    el(
      "div",
      { class: "page" },
      el(
        "header",
        { class: "page-heading" },
        el(
          "div",
          {},
          el("h1", {}, "Audit history"),
          el(
            "p",
            {},
            "Metadata only. Backend and MCP events remain separate sources.",
          ),
        ),
        button("Refresh source", app.refresh),
      ),
      el(
        "nav",
        { class: "tabs", "aria-label": "Audit source" },
        ["backend", "mcp"].map((s) =>
          link(s === "backend" ? "Backend" : "MCP", `#audit?source=${s}`, {
            "aria-current": source === s ? "page" : null,
          }),
        ),
      ),
      notice(
        "No authenticated actor identity, complete request log, or tamper-proof guarantee is provided.",
      ),
      dataRoot,
    ),
  );
  dataRoot.append(loading("Reading audit source…"));
  try {
    const data = await request(
      queryPath("/audit-log", { source, offset, limit: 25 }),
      { signal },
    );
    if (signal.aborted) return;
    dataRoot.replaceChildren(
      el(
        "div",
        { class: "section-head" },
        el("h2", {}, `${source === "mcp" ? "MCP" : "Backend"} events`),
        badge("Oldest first"),
      ),
    );
    if (data.invalid_lines)
      dataRoot.append(
        notice(
          `${data.invalid_lines} malformed lines encountered in this bounded scan.`,
          "warning",
        ),
      );
    if (data.scan_truncated)
      dataRoot.append(
        notice(
          "The source scan was truncated. This is not a complete audit view.",
          "warning",
        ),
      );
    if (!data.events.length)
      dataRoot.append(
        empty(
          "No events on this page",
          "No matching source metadata is available here. This does not prove no activity occurred.",
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
            [
              "Timestamp (UTC)",
              "Event",
              "Related records",
              "Outcome / metadata",
            ].map((t) => el("th", { scope: "col" }, t)),
          ),
        ),
      );
      table.append(
        el(
          "tbody",
          {},
          data.events.map((event) => {
            const relations = el("div", { class: "history-links" }),
              ticketId = event.ticket_id || event.arguments?.ticket_id;
            if (ticketId)
              relations.append(
                link(
                  `Ticket #${ticketId}`,
                  `#tickets?ticket=${ticketId}&status=`,
                ),
              );
            if (event.draft_id)
              relations.append(
                el(
                  "span",
                  {},
                  `Draft #${event.draft_id}${event.revision ? ` · r${event.revision}` : ""}`,
                ),
              );
            if (event.run_id)
              relations.append(
                link(
                  `Run ${event.run_id.slice(0, 8)}`,
                  `#runs?run=${event.run_id}`,
                ),
              );
            return el(
              "tr",
              {},
              el("td", {}, date(event.timestamp)),
              el(
                "td",
                {},
                name(event.action || event.tool),
                event.event ? el("div", { class: "muted" }, event.event) : null,
              ),
              el("td", {}, relations),
              el(
                "td",
                {},
                event.outcome
                  ? name(event.outcome)
                  : event.read_only !== undefined
                    ? event.read_only
                      ? "Read-only MCP"
                      : "Write-enabled MCP"
                    : "Recorded",
                event.duration_ms !== undefined
                  ? el("div", { class: "muted" }, `${event.duration_ms}ms`)
                  : null,
              ),
            );
          }),
        ),
      );
      dataRoot.append(el("div", { class: "table-wrap" }, table));
    }
    dataRoot.append(
      pager(offset, data.next_offset, (n) =>
        app.navigate(`#audit?source=${source}&offset=${n}`),
      ),
    );
  } catch (error) {
    if (error.name !== "AbortError")
      dataRoot.replaceChildren(errorBox(error, app.refresh));
  }
}
