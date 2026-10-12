import { request, allCompanies } from "./api.js";
import { badge, errorBox, button, confirmAction, announce } from "./ui.js";
import { ticketsView } from "./tickets.js";
import { runsView } from "./runs.js";
import { reportsView } from "./reports.js";
import { auditView } from "./audit.js";

const root = document.querySelector("#view");
const app = {
  config: { read_only: true },
  companies: [],
  dirty: () => false,
  navigate,
  refresh: async () => {
    if (
      !app.dirty() ||
      (await confirmAction(
        "Reload this view?",
        "Unsaved local text will be discarded. Use conflict reconciliation to preserve it.",
        "Reload",
      ))
    )
      await render();
  },
  signal: null,
};
let activeHash = location.hash || "#tickets";
let controller,
  dispose = () => {},
  navigating = false,
  approvedHash = null,
  generation = 0;
async function navigate(hash) {
  if (navigating || hash === location.hash) return;
  navigating = true;
  try {
    if (
      app.dirty() &&
      !(await confirmAction(
        "Discard unsaved edits?",
        "Your local draft text has not been saved. Leaving this view will discard it.",
        "Discard and leave",
      ))
    )
      return;
    approvedHash = hash;
    location.hash = hash;
  } finally {
    navigating = false;
  }
}
document.addEventListener("click", (event) => {
  const anchor = event.target.closest('a[href^="#"]');
  if (!anchor || event.ctrlKey || event.metaKey || event.shiftKey) return;
  if (anchor.getAttribute("href") === "#main") {
    event.preventDefault();
    document.querySelector("#main").focus();
    return;
  }
  event.preventDefault();
  navigate(anchor.getAttribute("href"));
});
window.addEventListener("beforeunload", (event) => {
  if (app.dirty()) {
    event.preventDefault();
    event.returnValue = "";
  }
});
window.addEventListener("hashchange", async () => {
  const target = location.hash || "#tickets";
  if (
    target !== approvedHash &&
    app.dirty() &&
    !(await confirmAction(
      "Discard unsaved edits?",
      "Leaving this case will discard your unsaved draft text.",
      "Discard and leave",
    ))
  ) {
    history.replaceState(null, "", activeHash);
    return;
  }
  approvedHash = null;
  activeHash = target;
  await render();
});
async function render() {
  const current = ++generation;
  controller?.abort();
  dispose();
  app.dirty = () => false;
  controller = new AbortController();
  app.signal = controller.signal;
  const [route, raw = ""] = (location.hash.slice(1) || "tickets").split("?");
  const views = {
    tickets: ticketsView,
    runs: runsView,
    reports: reportsView,
    audit: auditView,
  };
  document.querySelectorAll("[data-view]").forEach((a) => {
    if (a.dataset.view === route) a.setAttribute("aria-current", "page");
    else a.removeAttribute("aria-current");
  });
  const context = {
    ...app,
    signal: controller.signal,
    setDirty: (fn) => {
      if (current === generation) app.dirty = fn;
    },
  };
  try {
    const cleanup =
      (await (views[route] || ticketsView)(
        root,
        new URLSearchParams(raw),
        context,
      )) || (() => {});
    if (current === generation) dispose = cleanup;
    else cleanup();
  } catch (error) {
    if (current === generation && error.name !== "AbortError")
      root.replaceChildren(errorBox(error, () => render()));
  }
}
async function init() {
  try {
    const [config, companies] = await Promise.all([
      request("/console/config"),
      allCompanies(),
    ]);
    app.config = config;
    app.companies = companies;
    const policy = badge(
      config.read_only ? "Read-only server" : "Draft writes enabled",
      config.read_only ? "" : "info",
    );
    policy.id = "policy";
    document.querySelector("#policy").replaceWith(policy);
    if (!location.hash) history.replaceState(null, "", "#tickets");
    await render();
  } catch (error) {
    root.replaceChildren(errorBox(error), button("Reconnect", init));
    announce("Could not connect to Dispatch.");
  }
}
init();
