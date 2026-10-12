export function el(tag, attrs = {}, ...children) {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(attrs)) {
    if (value === null || value === undefined || value === false) continue;
    if (key.startsWith("on"))
      node.addEventListener(key.slice(2).toLowerCase(), value);
    else if (key === "class") node.className = value;
    else if (key === "text") node.textContent = value;
    else if (key in node && !key.startsWith("aria")) node[key] = value;
    else node.setAttribute(key, value === true ? "" : String(value));
  }
  for (const child of children.flat(Infinity)) {
    if (child !== null && child !== undefined)
      node.append(
        child instanceof Node ? child : document.createTextNode(String(child)),
      );
  }
  return node;
}
export const button = (label, action, attrs = {}) =>
  el("button", { type: "button", onClick: action, ...attrs }, label);
export const link = (label, hash, attrs = {}) =>
  el("a", { href: hash, ...attrs }, label);
export const badge = (text, tone = "") =>
  el("span", { class: `badge ${tone}` }, text);
export const notice = (text, tone = "") =>
  el("p", { class: `notice ${tone}` }, text);
export function empty(title, text) {
  return el("div", { class: "empty" }, el("h3", {}, title), el("p", {}, text));
}
export function field(label, input) {
  const id = input.id || `field-${++fieldCount}`;
  input.id = id;
  return el(
    "div",
    { class: "field" },
    el("label", { htmlFor: id }, label),
    input,
  );
}
let fieldCount = 0;
export function select(options, value, attrs = {}) {
  const input = el(
    "select",
    attrs,
    options.map(([id, label]) => el("option", { value: id }, label)),
  );
  input.value = value ?? "";
  return input;
}
export function facts(items) {
  return el(
    "dl",
    { class: "facts" },
    items.map(([label, value]) => [
      el("dt", {}, label),
      el("dd", {}, value ?? "—"),
    ]),
  );
}
export function date(value) {
  if (!value) return "—";
  const d = new Date(value);
  return Number.isNaN(d.getTime())
    ? "Unknown timestamp"
    : `${d.toLocaleString(undefined, { timeZone: "UTC", year: "numeric", month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" })} UTC`;
}
export function name(value) {
  if (!value) return "Not classified";
  const text = value.replaceAll("_", " ");
  return text[0].toUpperCase() + text.slice(1);
}
export function attention(value) {
  return value === null
    ? badge("Not assessed")
    : value
      ? badge("Attention required", "warning")
      : badge("No saved attention flag");
}
export function announce(text) {
  document.querySelector("#announcements").textContent = text;
}
export function errorBox(error, recover) {
  const box = el(
    "div",
    { class: "error-box", role: "alert" },
    el("strong", {}, error.message || "Request failed."),
    el(
      "p",
      {},
      error.uncertain
        ? "Do not repeat this write. Inspect the latest saved records first."
        : "Your local edits have not been discarded.",
    ),
  );
  if (recover) box.append(button("Refresh saved state", recover));
  return box;
}
export function pager(offset, next, change, total, limit = 25) {
  return el(
    "div",
    { class: "pager" },
    button("Previous", () => change(Math.max(0, offset - limit)), {
      disabled: offset === 0,
    }),
    el(
      "span",
      {},
      total === undefined
        ? `From record ${offset + 1}`
        : total === 0
          ? "0 matches"
          : `${offset + 1}–${Math.min(offset + limit, total)} of ${total}`,
    ),
    button("Next", () => change(next), {
      disabled: next === null || next === undefined,
    }),
  );
}
export function confirmAction(title, text, label = "Continue") {
  return new Promise((resolve) => {
    const prior = document.activeElement;
    const dialog = el("dialog", {
      class: "confirm-dialog",
      "aria-labelledby": "confirm-title",
    });
    const finish = (value) => {
      dialog.close();
      dialog.remove();
      prior?.focus();
      resolve(value);
    };
    dialog.append(
      el("h2", { id: "confirm-title" }, title),
      el("p", {}, text),
      el(
        "div",
        { class: "actions" },
        button("Cancel", () => finish(false)),
        button(label, () => finish(true), { class: "primary" }),
      ),
    );
    dialog.addEventListener("cancel", (event) => {
      event.preventDefault();
      finish(false);
    });
    document.body.append(dialog);
    dialog.showModal();
    dialog.querySelector("button").focus();
  });
}
export const categories = [
  "email",
  "network",
  "hardware",
  "security",
  "onboarding",
  "alerts",
];
export const priorities = ["low", "medium", "high", "urgent"];
export const statuses = ["new", "in_progress", "resolved", "closed"];
export const enumOptions = (values, all = "All") => [
  ["", all],
  ...values.map((v) => [v, name(v)]),
];
export function loading(title = "Loading saved records…") {
  return el(
    "div",
    { class: "loading", role: "status" },
    title,
    el("div", { class: "skeleton", "aria-hidden": "true" }),
  );
}
