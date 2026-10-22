let onLimit = () => {};
let onLocked = () => {};
export const setLimitHandler = (fn) => { onLimit = fn; };
export const setLockedHandler = (fn) => { onLocked = fn; };

const FIELD_NAMES = { value: "The number", recorded_on: "Recorded on", duration_min: "Length", due_date: "Due date", amount: "Amount", target: "Target" };

/** A validation error from the API as one plain sentence: "Title: needs at least 2 characters". */
function fieldError(e) {
  if (!e) return "Please check the details and try again.";
  const key = String((e.loc || []).filter((x) => typeof x === "string" && x !== "body").pop() || "");
  const field = FIELD_NAMES[key] || key.replace(/_/g, " ");
  const msg = String(e.msg || "").replace(/^Value error, /, "").replace(/^String should have /, "needs ")
    .replace(/^Input should be greater than or equal to (\S+)/, "can't be below $1").replace(/^Input should be less than or equal to (\S+)/, "can't be more than $1")
    .replace(/^Input should be greater than (\S+)/, "must be more than $1").replace(/^Input should be /, "should be ");
  if (!msg) return "Please check the details and try again.";
  return field ? `${field.charAt(0).toUpperCase()}${field.slice(1)}: ${msg}` : msg;
}

export async function api(path, { method = "GET", body } = {}) {
  const res = await fetch(`/api${path}`, {
    method, credentials: "same-origin",
    headers: body ? { "content-type": "application/json" } : {},
    body: body ? JSON.stringify(body) : undefined,
  });
  const data = await res.json().catch(() => ({}));
  if (res.status === 402 && data.detail?.code === "runs_exhausted") {
    onLimit(data.detail);
    const e = new Error(data.detail.message); e.status = 402; e.handled = true; throw e;
  }
  if (res.status === 403 && data.detail?.code === "locked") {
    onLocked(data.detail);
    const e = new Error(data.detail.message); e.status = 403; e.handled = true; throw e;
  }
  if (!res.ok) {
    const d = data.detail;
    const msg = typeof d === "string" ? d : Array.isArray(d) ? fieldError(d[0]) : d?.message ? d.message : "Something went wrong. Please try again.";
    const e = new Error(msg); e.status = res.status; throw e;
  }
  return data;
}

export const rupees = (minor) =>
  minor ? "₹" + new Intl.NumberFormat("en-IN").format(Math.round(minor / 100)) : "Free";
export const inr = (minor) => "₹" + new Intl.NumberFormat("en-IN").format(Math.round((minor || 0) / 100));
export const day = (iso) => (iso ? new Date(iso).toLocaleDateString("en-IN", { day: "numeric", month: "short", year: "numeric" }) : "");

export const ago = (iso) => {
  if (!iso) return "";
  const s = (Date.now() - new Date(iso)) / 1000;
  if (s < 60) return "just now";
  if (s < 3600) return `${Math.floor(s / 60)} min ago`;
  if (s < 86400) return `${Math.floor(s / 3600)} h ago`;
  return new Date(iso).toLocaleDateString("en-IN", { day: "numeric", month: "short" });
};

// Keeps ?ref / ?src / ?c from the first visit so they survive the login step.
export function rememberAttribution(params) {
  const keep = {};
  for (const k of ["ref", "src", "c"]) if (params.get(k)) keep[k] = params.get(k);
  if (Object.keys(keep).length) sessionStorage.setItem("ah_attr", JSON.stringify(keep));
}
export const attribution = () => { try { return JSON.parse(sessionStorage.getItem("ah_attr") || "{}"); } catch { return {}; } };

/** Indian mobile numbers shown one way everywhere: 98200 12345. Anything else is shown as stored. */
export const localPhone = (p) => {
  let d = String(p || "").replace(/\D/g, "");
  if (d.length === 12 && d.startsWith("91")) d = d.slice(2);
  else if (d.length === 11 && d.startsWith("0")) d = d.slice(1);
  return d.length === 10 && /^[6-9]/.test(d) ? `${d.slice(0, 5)} ${d.slice(5)}` : String(p || "");
};

/** A file upload (multipart). Same error handling as api(). */
export async function upload(path, form) {
  const res = await fetch(`/api${path}`, { method: "POST", credentials: "same-origin", body: form });
  const data = await res.json().catch(() => ({}));
  if (res.status === 403 && data.detail?.code === "locked") {
    onLocked(data.detail);
    const e = new Error(data.detail.message); e.status = 403; e.handled = true; throw e;
  }
  if (res.status === 402 && data.detail?.code === "runs_exhausted") {
    onLimit(data.detail);
    const e = new Error(data.detail.message); e.status = 402; e.handled = true; throw e;
  }
  if (!res.ok) {
    const d = data.detail;
    const e = new Error(typeof d === "string" ? d : d?.message || "That file couldn't be uploaded. Please try again."); e.status = res.status; e.code = d?.code; throw e;
  }
  return data;
}

/** Rupees from a plain number of rupees (not paise): 185000 → ₹1,85,000. */
export const rs = (n) => "₹" + new Intl.NumberFormat("en-IN", { maximumFractionDigits: 2 }).format(Number(n || 0));

/** A WhatsApp link that opens the owner's own WhatsApp with the text typed (to a number when given). */
export const waLink = (text, phone = "") => {
  let d = String(phone || "").replace(/\D/g, "");
  if (d.length === 10) d = "91" + d;
  return `https://wa.me/${d}?text=${encodeURIComponent(text || "")}`;
};
