export class APIError extends Error {
  constructor(message, status = 0, uncertain = false) {
    super(message);
    this.status = status;
    this.uncertain = uncertain;
  }
}
const messages = {
  403: "Writes are disabled by the server. Restart it in explicit write mode to review or Apply.",
  409: "The saved revision changed, was already approved, or another triage run is active. Inspect the current state.",
  422: "Some inputs are invalid. Check IDs, lengths, dates, and allowed values.",
  404: "This record could not be found.",
  503: "The service is unavailable or its safety/configuration limit was reached. Check the server configuration and narrow filters if needed.",
};
export async function request(
  path,
  { method = "GET", body, signal, optional404 = false } = {},
) {
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), 20000);
  const abort = () => controller.abort();
  signal?.addEventListener("abort", abort, { once: true });
  if (signal?.aborted) controller.abort();
  try {
    const response = await fetch(path, {
      method,
      signal: controller.signal,
      credentials: "same-origin",
      cache: "no-store",
      headers: body === undefined ? {} : { "Content-Type": "application/json" },
      body: body === undefined ? undefined : JSON.stringify(body),
      redirect: "error",
    });
    if (optional404 && response.status === 404) return null;
    if (!response.ok)
      throw new APIError(
        messages[response.status] || `Request failed (${response.status}).`,
        response.status,
        method !== "GET" && response.status >= 500,
      );
    const data = await response.json();
    validateIdentifiers(data);
    return data;
  } catch (error) {
    if (signal?.aborted)
      throw new DOMException("Navigation changed", "AbortError");
    if (error instanceof APIError) throw error;
    throw new APIError(
      method === "GET"
        ? "Connection interrupted. Refresh this read or resume monitoring."
        : "Write outcome unknown. Inspect saved records before doing anything else.",
      0,
      method !== "GET",
    );
  } finally {
    clearTimeout(timeout);
    signal?.removeEventListener("abort", abort);
  }
}
export function queryPath(path, params) {
  const query = new URLSearchParams();
  for (const [key, value] of Object.entries(params))
    if (value !== "" && value !== null && value !== undefined)
      query.set(key, String(value));
  return `${path}?${query}`;
}
export async function allCompanies(signal) {
  const values = [];
  for (let offset = 0; offset <= 10000; offset += 100) {
    const page = await request(`/companies?limit=100&offset=${offset}`, {
      signal,
    });
    values.push(...page);
    if (page.length < 100) return values;
  }
  throw new APIError("Company list exceeded its pagination limit.");
}

function validateIdentifiers(data, key = "") {
  if (data === null || data === undefined) return;
  if (
    [
      "id",
      "ticket_id",
      "draft_id",
      "company_id",
      "contact_id",
      "note_id",
      "assessment_draft_id",
      "revision",
    ].includes(key) &&
    typeof data === "number" &&
    !Number.isSafeInteger(data)
  )
    throw new APIError(
      "A record identifier exceeds browser integer precision. Inspect it through an exact-integer application client.",
    );
  if (Array.isArray(data))
    data.forEach((value) =>
      validateIdentifiers(value, key === "history_ids" ? "ticket_id" : ""),
    );
  else if (typeof data === "object")
    Object.entries(data).forEach(([name, value]) =>
      validateIdentifiers(value, name),
    );
}
