import { request, queryPath } from "./api.js";
import {
  el,
  button,
  field,
  select,
  notice,
  facts,
  date,
  name,
  loading,
  errorBox,
} from "./ui.js";
function monday() {
  const now = new Date();
  now.setUTCDate(now.getUTCDate() - ((now.getUTCDay() + 6) % 7));
  return now.toISOString().slice(0, 10);
}
function bars(title, values) {
  const max = Math.max(1, ...Object.values(values));
  return el(
    "section",
    { class: "section" },
    el("h2", {}, title),
    el(
      "div",
      { class: "bars" },
      Object.entries(values).map(([key, count]) => {
        const progress = el("progress", {
          max,
          value: count,
          "aria-label": `${name(key)}: ${count}`,
        });
        progress.className = "report-progress";
        return el(
          "div",
          { class: "bar-row" },
          el("span", {}, name(key)),
          progress,
          el("strong", {}, count),
        );
      }),
    ),
  );
}
export async function reportsView(root, params, app) {
  const signal = app.signal;
  const input = el("input", {
    type: "date",
    value: params.get("week") || monday(),
    required: true,
  });
  const company = select(
    [
      ["", "All companies"],
      ...app.companies.map((c) => [String(c.id), c.name]),
    ],
    params.get("company") || "",
  );
  const error = el("div"),
    dataRoot = el("section");
  const form = el("form", {
    class: "filters",
    onSubmit: (event) => {
      event.preventDefault();
      const d = new Date(`${input.value}T00:00:00Z`);
      if (!input.value || d.getUTCDay() !== 1) {
        error.replaceChildren(
          notice("Choose a Monday for the UTC reporting window.", "warning"),
        );
        input.focus();
        return;
      }
      app.navigate(`#reports?week=${input.value}&company=${company.value}`);
    },
  });
  form.append(
    field("Week beginning (Monday, UTC)", input),
    field("Company", company),
    el("button", { type: "submit", class: "primary" }, "Load report"),
  );
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
          el("h1", {}, "Weekly report"),
          el(
            "p",
            {},
            "A fixed UTC window. Current backlog is a separate snapshot.",
          ),
        ),
      ),
      form,
      button(
        "View synthetic seed week · 12 Oct 2026",
        () => app.navigate(`#reports?week=2026-10-12&company=${company.value}`),
        { class: "text-button" },
      ),
      error,
      dataRoot,
    ),
  );
  dataRoot.append(loading("Reading report…"));
  try {
    const report = await request(
      queryPath("/reports/weekly", {
        week_start: input.value,
        company_id: company.value,
      }),
      { signal },
    );
    if (signal.aborted) return;
    dataRoot.replaceChildren(
      el(
        "section",
        { class: "section" },
        el(
          "h2",
          {},
          `${report.week_start} → ${report.week_end_exclusive} (end exclusive)`,
        ),
        facts([
          ["Timezone", report.timezone],
          ["Generated", date(report.generated_at)],
          [
            "Scope",
            report.company_id
              ? app.companies.find((c) => c.id === report.company_id)?.name ||
                `Company #${report.company_id}`
              : "All companies",
          ],
          ["Tickets scanned", report.scanned_tickets],
        ]),
      ),
    );
    if (!report.period_complete)
      dataRoot.append(
        notice(
          "This reporting period is incomplete. Counts are not a finished-week comparison.",
          "warning",
        ),
      );
    dataRoot.append(
      el(
        "div",
        { class: "report-summary" },
        [
          ["Opened in selected week", report.opened],
          ["Resolved in selected week", report.resolved],
        ].map(([label, value]) =>
          el(
            "div",
            { class: "report-stat" },
            el("strong", {}, value),
            el("span", {}, label),
          ),
        ),
      ),
      el(
        "div",
        { class: "report-columns" },
        bars("Opened by category", report.opened_by_category),
        bars("Opened by priority", report.opened_by_priority),
      ),
      el(
        "section",
        { class: "section" },
        el("h2", {}, "Current backlog — not the historical week"),
        el(
          "p",
          { class: "muted" },
          `As of ${date(report.current_backlog.as_of)}`,
        ),
        facts([
          ["New", report.current_backlog.new],
          ["In progress", report.current_backlog.in_progress],
          ["Total open", report.current_backlog.total],
        ]),
      ),
    );
  } catch (error) {
    if (error.name !== "AbortError")
      dataRoot.replaceChildren(errorBox(error, app.refresh));
  }
}
