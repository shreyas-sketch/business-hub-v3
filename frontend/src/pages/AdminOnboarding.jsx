import React, { useState } from "react";
import { Link } from "react-router-dom";
import { api, day, localPhone, rupees } from "../api.js";
import { Busy, Field, FormError, Modal, Sheet, useHub, useLoad } from "../ui.jsx";
import { Empty, Loading, Pill } from "../v2.jsx";
import "./admin.css";

/* Admin → Onboarding (v2.3): every member from the event or who paid, with where their access comes from, the setup the hub
   can see for itself, and the team's notes. Members pay outside the hub (Razorpay); "Mark paid" switches Membership on. */

const FILTERS = [["", "Everyone"], ["starter", "L0 starter"], ["welcome", "Full hub"], ["event", "Event access"], ["paid", "Paid"], ["ended", "Event ended"], ["free", "Free"], ["off", "Switched off"]];
const PILL = { starter: "warn", welcome: "warn", event: "warn", paid: "on", ended: "off", free: "off", off: "off" };

// What a new signup gets: only the L0 starter pages (the webinar's), the whole hub, or their plan (Free, or the event's access)
const SIGNUP = [
  ["starter", "L0 only", { welcome_starter: true }, "New signups now get only the L0 pages; the rest stays locked until they join. Owners who signed up before keep what they have."],
  ["full", "Full hub", { welcome_starter: false, welcome_full_hub: true }, "New signups get the full hub again."],
  ["plan", "Their plan", { welcome_starter: false, welcome_full_hub: false }, "New signups now start on Free (or the event's access). Owners who already have the full hub keep it."],
];

function WelcomeSwitch() {
  const { toast } = useHub();
  const [s, reload] = useLoad("/admin/config/ui");
  if (!s) return null;
  const now = s.welcome_starter ? "starter" : s.welcome_full_hub ? "full" : "plan";
  return (
    <div className="panel row" style={{ justifyContent: "space-between", flexWrap: "wrap" }}>
      <span><b>New signups get</b> <span className="small muted">— L0 only: the pages marked L0 in Features &amp; plans. Change it for one owner on their page.</span></span>
      <div className="row">
        {SIGNUP.map(([k, label, body, msg]) => (
          <Busy key={k} className={`btn sm ${now === k ? "primary" : "ghost"}`} disabled={now === k} run={async () => {
            await api("/admin/config/ui", { method: "PATCH", body });
            toast(msg);
            reload();
          }}>{label}</Busy>
        ))}
      </div>
    </div>
  );
}

function Links() {
  const { toast } = useHub();
  const [s, reload] = useLoad("/admin/config/ui");
  const [f, setF] = useState(null);
  const [err, setErr] = useState("");
  if (!s) return null;
  const v = f || s;
  const set = (k) => (e) => setF({ ...v, [k]: e.target.value });
  return (
    <details className="panel">
      <summary><b>Links members see</b> <span className="small muted">— WhatsApp community, support email, how to pay</span></summary>
      <div className="stack" style={{ marginTop: 12 }}>
        <Field label="WhatsApp community link" hint="Shown to paying members only."><input className="input" value={v.community_link || ""} onChange={set("community_link")} placeholder="https://chat.whatsapp.com/…" /></Field>
        <Field label="Support email" hint="Shown to everyone."><input className="input" value={v.support_email || ""} onChange={set("support_email")} placeholder="support@…" /></Field>
        <Field label="Razorpay link: monthly (₹1,999)"><input className="input" value={v.pay_monthly_link || ""} onChange={set("pay_monthly_link")} placeholder="https://rzp.io/…" /></Field>
        <Field label="Razorpay link: yearly (₹19,999)"><input className="input" value={v.pay_yearly_link || ""} onChange={set("pay_yearly_link")} placeholder="https://rzp.io/…" /></Field>
        <FormError msg={err} />
        <div className="row"><Busy className="btn primary" onError={setErr} run={async () => {
          await api("/admin/config/ui", { method: "PATCH", body: { community_link: v.community_link || "", support_email: v.support_email || "",
            pay_monthly_link: v.pay_monthly_link || "", pay_yearly_link: v.pay_yearly_link || "" } });
          setF(null); setErr(""); toast("Saved. Members see it now."); reload();
        }}>Save links</Busy></div>
      </div>
    </details>
  );
}

function PaidModal({ m, prices, onClose, done }) {
  const [period, setPeriod] = useState("month");
  const [ref, setRef] = useState("");
  const [err, setErr] = useState("");
  return (
    <Modal onClose={onClose}>
      <span className="kicker">Mark paid</span>
      <h2>{m.business || m.name || localPhone(m.phone)}</h2>
      <p className="sub">They paid outside the hub. This opens OwnerDesk Membership for them straight away.</p>
      <div className="row">
        {[["month", `A month · ${rupees(prices.month)}`], ["year", `A year · ${rupees(prices.year)}`]].map(([k, l]) => (
          <button key={k} className={`btn sm ${period === k ? "primary" : "ghost"}`} onClick={() => setPeriod(k)}>{l}</button>
        ))}
      </div>
      <Field label="Razorpay payment id or a note" hint="So anyone can find the payment later."><input className="input" value={ref} onChange={(e) => setRef(e.target.value)} placeholder="pay_…" /></Field>
      <FormError msg={err} />
      <div className="row">
        <Busy className="btn money" onError={setErr} run={async () => { await api(`/admin/onboarding/${m.id}/paid`, { method: "POST", body: { period, ref } }); done(); }}>Mark paid</Busy>
        <button className="btn ghost" onClick={onClose}>Cancel</button>
      </div>
    </Modal>
  );
}

function Member({ m, checks, stages, prices, reload }) {
  const { toast } = useHub();
  const [open, setOpen] = useState(false);
  const [paying, setPaying] = useState(false);
  const [f, setF] = useState({ stage: m.stage, call_at: (m.call_at || "").slice(0, 16), handler: m.handler, notes: m.notes });
  const a = m.access;
  return (
    <div className="panel">
      <div className="row between" style={{ flexWrap: "wrap", gap: 8 }}>
        <div className="stack" style={{ gap: 2 }}>
          <b>{m.business || m.name || "No business name yet"}</b>
          <span className="small muted">{m.name}{m.name ? " · " : ""}{localPhone(m.phone)}{m.city ? ` · ${m.city}` : ""}</span>
        </div>
        <div className="row" style={{ gap: 6 }}>
          <Pill kind={PILL[a.status]}>{a.label}</Pill>
          <span className="small muted">{a.status === "paid" && a.paid_until ? `until ${day(a.paid_until)}` : a.status === "event" ? `${a.event_until ? `until ${day(a.event_until)}` : "until End access"}${a.event_day ? ` · Day ${a.event_day}` : ""}` : ""}</span>
        </div>
      </div>
      <div className="onb-checks">
        {checks.map((c) => <span key={c.key} className={m.checks[c.key] ? "ok" : ""}>{m.checks[c.key] ? "✓ " : ""}{c.label}</span>)}
      </div>
      <div className="row" style={{ flexWrap: "wrap", gap: 6 }}>
        <span className="small muted">{m.stage_label}{m.call_at ? ` · call ${new Date(m.call_at).toLocaleString("en-IN", { day: "numeric", month: "short", hour: "numeric", minute: "2-digit" })}` : ""}{m.handler ? ` · ${m.handler}` : ""}</span>
        <span style={{ flex: 1 }} />
        <button className="btn sm ghost" onClick={() => setOpen(!open)}>{open ? "Close" : "Notes & actions"}</button>
        {a.status !== "paid" && <button className="btn sm money" onClick={() => setPaying(true)}>Mark paid</button>}
      </div>
      {open && (
        <div className="stack">
          <div className="grid2">
            <Field label="Stage">
              <select className="input" value={f.stage} onChange={(e) => setF({ ...f, stage: e.target.value })}>
                {stages.map((s) => <option key={s.key} value={s.key}>{s.label}</option>)}
              </select>
            </Field>
            <Field label="Onboarding call (India time)"><input className="input" type="datetime-local" value={f.call_at} onChange={(e) => setF({ ...f, call_at: e.target.value })} /></Field>
            <Field label="Handled by"><input className="input" value={f.handler} onChange={(e) => setF({ ...f, handler: e.target.value })} placeholder="Name on the team" /></Field>
          </div>
          <Field label="Notes"><textarea className="input" rows={3} value={f.notes} onChange={(e) => setF({ ...f, notes: e.target.value })} /></Field>
          <div className="row" style={{ flexWrap: "wrap" }}>
            <Busy className="btn primary sm" run={async () => { await api(`/admin/onboarding/${m.id}`, { method: "PATCH", body: { ...f, call_at: f.call_at || "" } }); toast("Saved"); reload(); }}>Save notes</Busy>
            <Busy className="btn sm ghost" run={async () => {
              const n = Number(window.prompt("How many extra AI tokens?", "50"));
              if (!n) return;
              const r = await api(`/admin/onboarding/${m.id}/tokens`, { method: "POST", body: { tokens: n } }); toast(`Extra tokens now ${r.bonus_tokens}`); reload();
            }}>Add tokens{m.bonus_tokens ? ` (${m.bonus_tokens})` : ""}</Busy>
            <Busy className="btn sm ghost" run={async () => {
              if (a.status !== "off" && !window.confirm("Switch this account off? They can't log in until it's switched on again. Nothing is deleted.")) return;
              await api(`/admin/onboarding/${m.id}/off`, { method: "POST", body: { off: a.status !== "off" } }); toast(a.status === "off" ? "Switched on" : "Switched off"); reload();
            }}>{a.status === "off" ? "Switch on" : "Switch off"}</Busy>
            <Link className="btn sm ghost" to={`/admin/owners/${m.id}`}>Full account</Link>
          </div>
          {m.payments?.length > 0 && (
            <div className="stack" style={{ gap: 4 }}>
              <span className="label">Payments recorded</span>
              {m.payments.map((p) => <span key={p.id} className="small">{day(p.at)} · a {p.period} · <span className="money">{rupees(p.amount_minor)}</span>{p.ref ? ` · ${p.ref}` : ""} · until {day(p.until)}</span>)}
            </div>
          )}
        </div>
      )}
      {paying && <PaidModal m={m} prices={prices} onClose={() => setPaying(false)} done={() => { setPaying(false); toast("Membership switched on"); reload(); }} />}
    </div>
  );
}

export default function AdminOnboarding() {
  const [status, setStatus] = useState("");
  const [q, setQ] = useState("");
  const [d, reload] = useLoad(`/admin/onboarding?status=${status}&q=${encodeURIComponent(q)}`, [status, q]);
  return (
    <Sheet kicker="Admin · Onboarding" id="A-10" pillar="Admin" title={<>Every member, <em>set up</em>.</>}
      sub="Who came from the event, who paid, and what's set up in their hub. Mark payments made on Razorpay here.">
      <WelcomeSwitch />
      <Links />
      <div className="row" style={{ flexWrap: "wrap" }}>
        {FILTERS.map(([k, l]) => (
          <button key={k} className={`btn sm ${status === k ? "primary" : "ghost"}`} onClick={() => setStatus(k)}>
            {l}{k && d?.counts && status === "" ? ` · ${d.counts[k] ?? 0}` : ""}
          </button>
        ))}
        <input className="input" style={{ flex: 1, minWidth: 180 }} placeholder="Search name, business, phone, city" value={q} onChange={(e) => setQ(e.target.value)} />
      </div>
      {!d ? <Loading /> : !d.members.length ? <Empty>No members here yet.</Empty> : (
        <div className="stack">
          {d.members.map((m) => <Member key={m.id + (m.payments?.length || 0) + m.access.status} m={m} checks={d.checks} stages={d.stages} prices={d.prices} reload={reload} />)}
        </div>
      )}
    </Sheet>
  );
}
