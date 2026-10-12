import { request, queryPath } from "./api.js";
import {
  el,
  field,
  select,
  enumOptions,
  categories,
  priorities,
  statuses,
  link,
  attention,
  badge,
  name,
  pager,
  button,
  loading,
  empty,
  errorBox,
} from "./ui.js";
import { caseView } from "./review.js";

export async function ticketsView(root, params, app) {
  const signal = app.signal;
  root.replaceChildren(loading("Loading ticket queue…"));
  const filters = {
    status: params.has("status") ? params.get("status") : "new",
    query: params.get("query") || "",
    company_id: params.get("company_id") || "",
    category: params.get("category") || "",
    priority: params.get("priority") || "",
    attention: params.get("attention") || "",
    offset: Number(params.get("offset") || 0),
    limit: 25,
  };
  const current = params.get("ticket");
  const data = await request(queryPath("/console/tickets", filters), {
    signal,
  });
  if (signal.aborted) return;
  const queue = el("aside", { class: "queue", "aria-label": "Ticket queue" });
  const caseRoot = el("section", {
    class: "case",
    "aria-label": "Selected case",
  });
  const layout = el(
    "div",
    { class: `workbench ${current ? "has-case" : ""}` },
    queue,
    caseRoot,
  );
  root.replaceChildren(layout);
  function hash(changes = {}) {
    const p = new URLSearchParams(params);
    for (const [k, v] of Object.entries(changes)) {
      if (v === null) p.delete(k);
      else p.set(k, String(v));
    }
    return `#tickets?${p}`;
  }
  queue.append(
    el(
      "div",
      { class: "queue-heading" },
      el("h1", {}, "Tickets"),
      badge(`${data.total} matches`),
    ),
  );
  const search = el("input", {
    type: "search",
    value: filters.query,
    placeholder: "Subject or description",
    maxLength: 200,
  });
  const status = select(enumOptions(statuses, "All statuses"), filters.status);
  const company = select(
    [
      ["", "All companies"],
      ...app.companies.map((c) => [String(c.id), c.name]),
    ],
    filters.company_id,
  );
  const category = select(
    enumOptions(categories, "All categories"),
    filters.category,
  );
  const priority = select(
    enumOptions(priorities, "All priorities"),
    filters.priority,
  );
  const flag = select(
    [
      ["", "All assessments"],
      ["required", "Attention required"],
      ["not_assessed", "Not assessed"],
      ["not_flagged", "No saved flag"],
    ],
    filters.attention,
  );
  const form = el("form", {
    class: "filters",
    onSubmit: (event) => {
      event.preventDefault();
      app.navigate(
        hash({
          query: search.value.trim(),
          status: status.value,
          company_id: company.value,
          category: category.value,
          priority: priority.value,
          attention: flag.value,
          offset: 0,
          ticket: null,
        }),
      );
    },
  });
  const searchField = field("Search tickets", search);
  searchField.classList.add("wide");
  form.append(
    searchField,
    field("Status", status),
    field("Company", company),
    field("Category", category),
    field("Priority", priority),
    field("Saved attention", flag),
    el("button", { type: "submit", class: "primary" }, "Filter queue"),
  );
  queue.append(form);
  const list = el("div", { class: "queue-list" });
  for (const ticket of data.tickets) {
    list.append(
      link("", hash({ ticket: ticket.id }), {
        class: `ticket-row ${String(ticket.id) === current ? "selected" : ""}`,
        "aria-current": String(ticket.id) === current ? "true" : null,
        "aria-label": `Ticket ${ticket.id}: ${ticket.subject}`,
      }),
    );
    list.lastChild.append(
      el(
        "div",
        { class: "row-top" },
        el("span", { class: "ticket-id" }, `#${ticket.id}`),
        badge(
          name(ticket.priority),
          ticket.priority === "urgent" ? "warning" : "",
        ),
      ),
      el("strong", {}, ticket.subject),
      el("p", {}, `${ticket.company_name} · ${name(ticket.category)}`),
      el(
        "div",
        { class: "actions" },
        attention(ticket.requires_attention),
        ticket.draft_count
          ? badge(
              `${ticket.draft_count - ticket.approved_count} drafts · ${ticket.approved_count} approved`,
            )
          : null,
      ),
    );
  }
  if (!data.tickets.length)
    list.append(
      empty(
        "No matching tickets",
        "Try clearing filters or viewing all statuses. Your existing database has not been reset.",
      ),
    );
  queue.append(
    list,
    pager(
      filters.offset,
      data.next_offset,
      (offset) => app.navigate(hash({ offset, ticket: null })),
      data.total,
    ),
    el(
      "p",
      { class: "muted" },
      "Attention reflects saved model assessment only. Unassessed does not mean safe.",
    ),
  );
  if (!current) {
    caseRoot.append(
      empty(
        "Choose a case to review",
        "Inspect the original issue, saved assessment, and response together. Approval records a review; it never sends.",
      ),
    );
    return;
  }
  caseRoot.append(
    link("Back to ticket queue", hash({ ticket: null }), {
      class: "mobile-back",
    }),
  );
  const detail = el("div");
  caseRoot.append(detail);
  try {
    return await caseView(detail, current, params, app, hash);
  } catch (error) {
    if (error.name !== "AbortError")
      detail.replaceChildren(errorBox(error, app.refresh));
  }
}
