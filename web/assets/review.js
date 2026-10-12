import { request } from "./api.js";
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
  attention,
  loading,
  errorBox,
  confirmAction,
  announce,
  pager,
} from "./ui.js";

export function assessmentSheet(saved, title = "Saved assessment") {
  if (!saved)
    return el(
      "section",
      { class: "section" },
      el("h2", {}, title),
      empty(
        "Not assessed",
        "No valid saved assessment was found. This is not a negative security finding.",
      ),
    );
  const a = saved.assessment || saved;
  return el(
    "section",
    { class: "section" },
    el(
      "div",
      { class: "section-head" },
      el("h2", {}, title),
      attention(
        saved.requires_attention ??
          (a.security_flag ||
            a.category === "security" ||
            a.priority === "urgent" ||
            a.urgency === "immediate"),
      ),
    ),
    facts([
      ["Category", name(a.category)],
      ["Priority", name(a.priority)],
      ["Urgency", name(a.urgency)],
      [
        "Security flag",
        a.security_flag ? "Yes — review required" : "No model flag",
      ],
    ]),
    el("p", { class: "body-text" }, a.rationale),
    saved.created_at
      ? el(
          "p",
          { class: "muted" },
          `Saved ${date(saved.created_at)} · linked draft #${saved.draft_id}`,
        )
      : null,
    el(
      "div",
      { class: "history-links" },
      (saved.history_ids || []).map((id) =>
        link(`History #${id}`, `#tickets?ticket=${id}&status=`),
      ),
    ),
  );
}
export async function caseView(root, ticketId, params, app, ticketHash) {
  const signal = app.signal;
  root.replaceChildren(loading("Opening source and saved response…"));
  const ticket = await request(`/tickets/${encodeURIComponent(ticketId)}`, {
    signal,
  });
  const [company, contacts, saved, drafts] = await Promise.all([
    request(`/companies/${ticket.company_id}`, { signal }),
    request(`/contacts?company_id=${ticket.company_id}&limit=100`, { signal }),
    request(`/tickets/${ticket.id}/assessment`, { signal, optional404: true }),
    request(`/tickets/${ticket.id}/drafts?limit=25&offset=0`, { signal }),
  ]);
  if (signal.aborted) return;
  const contact = contacts.find((c) => c.id === ticket.contact_id);
  const heading = el(
    "header",
    { class: "case-heading" },
    el(
      "div",
      {},
      el("h1", {}, ticket.subject),
      el("p", { class: "case-ref" }, `Case #${ticket.id} · ${company.name}`),
      el(
        "div",
        { class: "actions" },
        badge(name(ticket.status)),
        badge(
          name(ticket.priority),
          ticket.priority === "urgent" ? "warning" : "",
        ),
        attention(saved?.requires_attention ?? null),
      ),
    ),
    el(
      "div",
      { class: "actions" },
      link("Preview / Apply", `#runs?ticket=${ticket.id}`, {
        class: "button-link",
      }),
      button("Refresh case", app.refresh),
    ),
  );
  const source = el("div", { class: "source-pane" });
  const draftRoot = el("div", { class: "review-pane" });
  root.replaceChildren(
    heading,
    el("div", { class: "case-columns" }, source, draftRoot),
  );
  source.append(
    el(
      "section",
      { class: "section" },
      el("h2", {}, "Original issue"),
      facts([
        ["Company", company.name],
        [
          "Contact",
          contact
            ? `${contact.name} · ${contact.email}`
            : ticket.contact_id
              ? "Contact not in this bounded page"
              : "Automated / no contact",
        ],
        ["SLA tier", name(company.sla_tier)],
        ["Created", date(ticket.created_at)],
        ["Ticket category", name(ticket.category)],
        ["Updated", date(ticket.updated_at)],
      ]),
      el("p", { class: "body-text source-description" }, ticket.description),
    ),
    assessmentSheet(saved),
  );
  const noteSection = el(
    "section",
    { class: "section notes" },
    el("h2", {}, `Internal notes (${ticket.notes.length})`),
  );
  if (!ticket.notes.length)
    noteSection.append(el("p", { class: "muted" }, "No internal notes."));
  for (const note of ticket.notes) {
    const system =
      note.author_name === "Dispatch MCP" &&
      note.body.startsWith("Dispatch triage v1 ");
    noteSection.append(
      el(
        "details",
        {},
        el(
          "summary",
          {},
          `${system ? "Agent completion metadata" : note.author_name} · ${date(note.created_at)}`,
        ),
        el("p", { class: "body-text" }, note.body),
      ),
    );
  }
  source.append(noteSection);
  if (saved?.draft_id && !drafts.some((d) => d.id === saved.draft_id))
    draftRoot.append(
      notice(
        `The assessment links draft #${saved.draft_id}; it is not on this first draft page. Use draft pagination to inspect it.`,
        "warning",
      ),
    );
  return draftReview(draftRoot, ticket.id, drafts, saved, app);
}

function draftReview(root, ticketId, firstPage, saved, app) {
  const signal = app.signal;
  let drafts = firstPage,
    offset = 0,
    selected = drafts.find((d) => d.id === saved?.draft_id) || drafts[0],
    busy = false,
    paging = false,
    pageGeneration = 0,
    conflict = false,
    unknown = false;
  let baseline = selected?.body || "",
    local = baseline;
  let revision = selected?.revision;
  const dirty = () => local !== baseline;
  app.setDirty(() => dirty() || busy);
  const sheet = el("section", { class: "draft-sheet" });
  root.append(sheet);
  const errors = el("div"),
    compare = el("div");
  let editor, footer, saveButton, approveButton, selector, statusText;
  function update() {
    const pageButtons = sheet.querySelectorAll(".pager button");
    if (pageButtons.length === 2) {
      pageButtons[0].disabled = busy || paging || offset === 0;
      pageButtons[1].disabled =
        busy || paging || drafts.length < 25 || offset + 25 > 10000;
    }
    if (!selected) return;
    footer.classList.toggle("dirty", dirty());
    statusText.textContent = unknown
      ? "Outcome unknown — reconcile before another write"
      : conflict
        ? `Conflict — local text kept from reviewed revision ${revision}`
        : dirty()
          ? `Unsaved edits · based on revision ${revision}`
          : `Saved revision ${revision}${selected.status === "approved" ? " · approved, not sent" : " · ready for review"}`;
    saveButton.disabled =
      busy ||
      paging ||
      !dirty() ||
      conflict ||
      unknown ||
      app.config.read_only ||
      selected.status === "approved" ||
      !local.trim() ||
      local.trim().length > 10000;
    approveButton.disabled =
      busy ||
      paging ||
      dirty() ||
      conflict ||
      unknown ||
      app.config.read_only ||
      selected.status === "approved";
    const option = selector.querySelector(`option[value="${selected.id}"]`);
    if (option)
      option.textContent = `Draft #${selected.id} · r${selected.revision} · ${selected.status}${selected.id === saved?.draft_id ? " · assessment-linked" : ""}`;
    selector.disabled = busy || paging;
    editor.readOnly =
      busy || paging || app.config.read_only || selected.status === "approved";
  }
  async function loadLatest() {
    const page = await request(
      `/tickets/${ticketId}/drafts?limit=25&offset=${offset}`,
      { signal },
    );
    return page.find((d) => d.id === selected.id) || null;
  }
  async function reconcile() {
    if (signal.aborted) return;
    try {
      const latest = await loadLatest();
      if (!latest) {
        errors.replaceChildren(
          notice(
            "Draft no longer appears on this page. Inspect the draft list before continuing.",
            "warning",
          ),
        );
        return;
      }
      compare.replaceChildren(
        el(
          "div",
          { class: "compare" },
          el(
            "h3",
            {},
            `Latest saved revision ${latest.revision} · ${latest.status === "approved" ? "approved — not sent" : "draft"}`,
          ),
          el("p", { class: "body-text" }, latest.body),
          button("Use latest saved text", async () => {
            if (
              dirty() &&
              !(await confirmAction(
                "Replace local text?",
                "Your unsaved local changes will be discarded. The latest saved revision will become the review baseline.",
                "Use saved text",
              ))
            )
              return;
            selected = latest;
            baseline = latest.body;
            local = baseline;
            revision = latest.revision;
            editor.value = local;
            conflict = false;
            unknown = false;
            errors.replaceChildren();
            compare.replaceChildren();
            update();
          }),
          latest.status === "draft"
            ? button("Keep local text against latest revision", async () => {
                if (
                  !(await confirmAction(
                    "Review your local text again",
                    `This adopts saved revision ${latest.revision} as the new baseline but keeps your local text. Save explicitly after comparing both versions.`,
                    "Keep and review",
                  ))
                )
                  return;
                selected = latest;
                baseline = latest.body;
                revision = latest.revision;
                conflict = false;
                unknown = false;
                errors.replaceChildren();
                compare.replaceChildren();
                update();
                editor.focus();
              })
            : null,
        ),
      );
    } catch (error) {
      if (error.name !== "AbortError")
        errors.replaceChildren(errorBox(error, reconcile));
    }
  }
  async function mutate(approve) {
    if (busy || paging || conflict || unknown || !selected) return;
    if (
      approve &&
      (dirty() ||
        !(await confirmAction(
          "Approve this saved revision?",
          `Approve draft #${selected.id}, revision ${revision}, exactly as displayed. The draft becomes immutable through this API. No reply will be sent.`,
          "Approve — keep unsent",
        )))
    )
      return;
    busy = true;
    update();
    errors.replaceChildren();
    try {
      const result = await request(
        `/tickets/${ticketId}/drafts/${selected.id}${approve ? "/approve" : ""}`,
        {
          method: approve ? "POST" : "PATCH",
          body: approve ? { revision } : { revision, body: local },
        },
      );
      if (signal.aborted) return;
      selected = result;
      drafts = drafts.map((d) => (d.id === result.id ? result : d));
      baseline = result.body;
      local = baseline;
      revision = result.revision;
      editor.value = local;
      errors.replaceChildren(
        notice(
          approve
            ? "Approved — not sent. This saved draft is now read-only."
            : "Edits saved. Review the saved revision before approving.",
        ),
      );
      announce(approve ? "Draft approved, not sent." : "Draft edits saved.");
    } catch (error) {
      if (signal.aborted) return;
      conflict = error.status === 409;
      unknown = error.uncertain;
      errors.replaceChildren(errorBox(error, reconcile));
      if (conflict || unknown) await reconcile();
    } finally {
      busy = false;
      update();
    }
  }
  async function switchDraft(id) {
    if (busy || paging) return;
    if (
      dirty() &&
      !(await confirmAction(
        "Discard unsaved edits?",
        "Switching drafts discards local changes to this draft.",
        "Discard and switch",
      ))
    ) {
      selector.value = String(selected.id);
      return;
    }
    selected = drafts.find((d) => String(d.id) === id);
    baseline = selected.body;
    local = baseline;
    revision = selected.revision;
    conflict = false;
    unknown = false;
    render();
  }
  async function changePage(newOffset) {
    if (busy || paging) return;
    const generation = ++pageGeneration;
    paging = true;
    update();
    try {
      if (
        dirty() &&
        !(await confirmAction(
          "Discard unsaved edits?",
          "Loading another draft page discards this local editor text.",
          "Discard and load",
        ))
      )
        return;
      const snapshot = { body: local, draftId: selected?.id };
      const page = await request(
        `/tickets/${ticketId}/drafts?limit=25&offset=${newOffset}`,
        { signal },
      );
      if (signal.aborted || generation !== pageGeneration) return;
      // Even a queued input event must not be erased by a late page response.
      if (
        local !== snapshot.body ||
        selected?.id !== snapshot.draftId ||
        busy
      ) {
        errors.replaceChildren(
          notice(
            "Local editor changed while drafts loaded. Your text was kept; save or review it before changing pages.",
            "warning",
          ),
        );
        return;
      }
      offset = newOffset;
      drafts = page;
      selected = page[0];
      baseline = selected?.body || "";
      local = baseline;
      revision = selected?.revision;
      conflict = false;
      unknown = false;
      errors.replaceChildren();
      compare.replaceChildren();
      render();
    } catch (error) {
      if (error.name !== "AbortError") errors.replaceChildren(errorBox(error));
    } finally {
      if (generation === pageGeneration) {
        paging = false;
        update();
      }
    }
  }

  function render() {
    sheet.replaceChildren(
      el(
        "div",
        { class: "section-head" },
        el("h2", {}, "Response review"),
        badge("Always unsent"),
      ),
    );
    if (!selected) {
      sheet.append(
        empty(
          "No saved drafts on this page",
          offset
            ? "Return to the previous page to inspect saved drafts."
            : "Run explicit Apply to persist a generated draft. Preview results cannot be approved.",
        ),
        pager(offset, null, changePage),
      );
      return;
    }
    selector = select(
      drafts.map((d) => [
        String(d.id),
        `Draft #${d.id} · r${d.revision} · ${d.status}${d.id === saved?.draft_id ? " · assessment-linked" : ""}`,
      ]),
      String(selected.id),
      { onChange: () => switchDraft(selector.value) },
    );
    editor = el("textarea", {
      id: "draft-body",
      value: local,
      maxLength: 10000,
      spellcheck: true,
      onInput: () => {
        local = editor.value;
        update();
      },
    });
    saveButton = button("Save edits", () => mutate(false));
    approveButton = button("Approve — keep unsent", () => mutate(true), {
      class: "primary",
    });
    statusText = el("p", { class: "revision", role: "status" });
    footer = el(
      "footer",
      { class: "draft-footer" },
      statusText,
      el("div", { class: "actions" }, saveButton, approveButton),
    );
    sheet.append(
      field("Saved draft", selector),
      el(
        "div",
        { class: "draft-caption" },
        el("span", {}, `Created ${date(selected.created_at)}`),
        el("span", {}, `#${selected.id} · r${revision}`),
      ),
      field("Response body", editor),
      footer,
      errors,
      compare,
    );
    if (app.config.read_only)
      sheet.append(
        notice(
          "Review is read-only. The server—not this browser—controls write permission.",
        ),
      );
    if (selected.status === "approved")
      sheet.append(
        notice(
          `Approved ${date(selected.approved_at)}. No message was delivered.`,
        ),
      );
    sheet.append(
      pager(
        offset,
        drafts.length === 25 && offset + 25 <= 10000 ? offset + 25 : null,
        changePage,
        undefined,
        25,
      ),
    );
    update();
  }
  render();
  return () => {
    pageGeneration++;
    app.setDirty(() => false);
  };
}
