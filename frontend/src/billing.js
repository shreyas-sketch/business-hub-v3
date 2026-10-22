// In-app payments. One call runs the whole flow for a tier:
// checkout → Razorpay window → verify → refresh → toast. On a local test setup it takes a test payment instead,
// and where online payment isn't switched on it opens the payment link or the "our team will call you" note.
import { api, day } from "./api.js";

const SCRIPT = "https://checkout.razorpay.com/v1/checkout.js";

/** Exact rupees from paise, keeping paise when there are any: 59970 → ₹599.70, 199900 → ₹1,999. For money owed or paid out. */
export function paise(minor) {
  const v = Math.round(Number(minor) || 0);
  const abs = Math.abs(v);
  const rest = abs % 100;
  return `${v < 0 ? "-" : ""}₹${new Intl.NumberFormat("en-IN").format(Math.floor(abs / 100))}${rest ? `.${String(rest).padStart(2, "0")}` : ""}`;
}
let loading = null;

/** Loads Razorpay Checkout once and resolves window.Razorpay. A failed load can be retried. */
export function loadCheckout() {
  if (window.Razorpay) return Promise.resolve(window.Razorpay);
  if (!loading) {
    loading = new Promise((resolve, reject) => {
      const fail = (s) => {
        loading = null;
        if (s) s.remove();
        reject(new Error("Couldn't open the payment window. Check your internet connection and try again."));
      };
      const s = document.createElement("script");
      s.src = SCRIPT;
      s.async = true;
      s.onload = () => (window.Razorpay ? resolve(window.Razorpay) : fail(s));
      s.onerror = () => fail(s);
      document.head.appendChild(s);
    });
  }
  return loading;
}

function openRazorpay(Razorpay, co) {
  return new Promise((resolve) => {
    const options = {
      key: co.key_id,
      ...(co.kind === "order" ? { order_id: co.order_id, amount: co.amount } : { subscription_id: co.subscription_id }),
      currency: "INR",
      name: co.name,
      description: co.description,
      prefill: co.prefill || {},
      notes: co.notes || {},
      theme: { color: "#C9A84C" },
      handler: (resp) => resolve(resp),
      modal: { ondismiss: () => resolve(null) },
    };
    // A failed attempt stays inside Razorpay's window, where the owner can retry with another method.
    new Razorpay(options).open();
  });
}

/**
 * Buys or starts `tier`. Resolves { status: "paid" | "dismissed" | "contact", result? }.
 * Throws an Error with a plain message when something goes wrong (Busy shows it as a toast).
 */
export async function checkout(tier, { me, refresh, toast, setContact } = {}) {
  const co = await api("/billing/checkout", { method: "POST", body: { tier } });

  if (co.mode === "contact") {
    if (co.url) window.open(co.url, "_blank", "noopener");
    else if (setContact) setContact(tier);
    return { status: "contact" };
  }

  if (co.mode === "dev") {
    const ok = window.confirm(`Test payment of ${paise(co.amount)} — no real money.\n\n${co.description}`);
    if (!ok) return { status: "dismissed" };
    const result = await api("/billing/dev-complete", { method: "POST", body: { payment_id: co.payment_id } });
    if (refresh) await refresh();
    if (toast) toast(`You're on ${result.plan_name}.`);
    return { status: "paid", result };
  }

  const Razorpay = await loadCheckout();
  const resp = await openRazorpay(Razorpay, { ...co, prefill: { contact: me?.user?.phone, ...(co.prefill || {}) } });
  if (!resp) return { status: "dismissed" };
  let result;
  try {
    result = await api("/billing/verify", {
      method: "POST",
      body: {
        razorpay_payment_id: resp.razorpay_payment_id,
        razorpay_order_id: resp.razorpay_order_id,
        razorpay_subscription_id: resp.razorpay_subscription_id,
        razorpay_signature: resp.razorpay_signature,
      },
    });
  } finally {
    if (refresh) await refresh();  // the webhook may already have applied it
  }
  if (toast) {
    const first = co.kind === "subscription" && co.start_at ? ` Your first ${paise(co.amount)} charge is on ${day(co.start_at)}.` : "";
    toast(`You're on ${result.plan_name}.${first}`);
  }
  return { status: "paid", result };
}
