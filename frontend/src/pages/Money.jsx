import React, { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { ago, api, day, localPhone } from "../api.js";
import { paise as inr } from "../billing.js";
import { Busy, Field, FormError, Gate, LoadError, Sheet, Stat, canAct, useHub, useLoad } from "../ui.jsx";
import { Pill } from "../v2.jsx";
import { AutoFollowups } from "./Agents.jsx";

const today = () => new Date().toLocaleDateString("en-CA"); // YYYY-MM-DD, local time
const blank = () => ({ customer: "", phone: "", email: "", amount: "", due_date: today(), invoice_no: "", note: "" });
const STAGE = { pending: "Before the due date", due: "On the due date", overdue: "Overdue reminder" };
const STOPPED = { replied: "they replied", paid: "it was paid" };

function AddForm({ onAdded }) {
  const { toast } = useHub();
  const [f, setF] = useState(blank());
  const set = (k) => (e) => setF({ ...f, [k]: e.target.value });
  const ready = f.customer.trim().length >= 2 && Number(f.amount) > 0 && f.due_date;
  return (
    <div className="panel active">
      <span className="label">Add money owed</span>
      <div className="grid3">
        <Field label="Customer"><input className="input" maxLength={80} value={f.customer} onChange={set("customer")} placeholder="Ramesh Patel" /></Field>
        <Field label="Amount (₹)"><input className="input" type="number" min="1" step="any" inputMode="decimal" value={f.amount} onChange={set("amount")} placeholder="12500" /></Field>
        <Field label="Due date"><input className="input" type="date" value={f.due_date} onChange={set("due_date")} /></Field>
        <Field label="WhatsApp number (for reminders)"><input className="input" inputMode="tel" maxLength={24} value={f.phone} onChange={set("phone")} placeholder="98200 12345" /></Field>
        <Field label="Email (for automatic follow-ups)"><input className="input" type="email" inputMode="email" maxLength={254} value={f.email} onChange={set("email")} placeholder="accounts@customer.in" /></Field>
        <Field label="Invoice no. (optional)"><input className="input" maxLength={40} value={f.invoice_no} onChange={set("invoice_no")} /></Field>
        <Field label="Note (optional)"><input className="input" maxLength={300} value={f.note} onChange={set("note")} placeholder="Kitchen balance payment" /></Field>
      </div>
      <div>
        <Busy className="btn primary" disabled={!ready} run={async () => {
          await api("/money", { method: "POST", body: { ...f, amount: Number(f.amount) } });
          setF(blank());
          toast("Added");
          onAdded();
        }}>Add</Busy>
      </div>
    </div>
  );
}

function History({ id }) {
  const [h, , err] = useLoad(`/followups/history/${id}`);
  if (err) return <LoadError err={err} />;
  if (!h) return <span className="small muted">Loading…</span>;
  return (
    <div className="stack" style={{ gap: 8 }}>
      <span className="label">Reminder history</span>
      {!h.history.length ? <span className="small muted">No reminders yet.</span> : (
        <div className="timeline">
          {h.history.map((x, i) => (
            <div key={i} className={x.ok ? "done" : "wait"}>
              <span className="small muted">{ago(x.at)} · {x.channel === "email" ? "Email" : "WhatsApp"}</span>
              <span className="small">{STAGE[x.stage] || x.stage}{x.stage === "overdue" && x.n ? ` ${x.n}` : ""} — {x.status}{x.replied ? " · they replied" : ""}</span>
            </div>
          ))}
        </div>
      )}
      {h.stopped && <span className="small muted">Automatic follow-ups stopped: {STOPPED[h.stopped] || h.stopped}.</span>}
    </div>
  );
}

function EditDue({ d, onSaved, onCancel }) {
  const [f, setF] = useState({ customer: d.customer, phone: d.phone || "", email: d.email || "", amount: d.amount / 100, due_date: d.due_date,
    invoice_no: d.invoice_no || "", note: d.note || "" });
  const [err, setErr] = useState("");
  const set = (k) => (e) => setF({ ...f, [k]: e.target.value });
  return (
    <div className="stack">
      <div className="grid3">
        <Field label="Customer"><input className="input" maxLength={80} value={f.customer} onChange={set("customer")} /></Field>
        <Field label="Amount (₹)"><input className="input" type="number" min="1" step="any" inputMode="decimal" value={f.amount} onChange={set("amount")} /></Field>
        <Field label="Due date"><input className="input" type="date" value={f.due_date} onChange={set("due_date")} /></Field>
        <Field label="WhatsApp number"><input className="input" inputMode="tel" maxLength={24} value={f.phone} onChange={set("phone")} /></Field>
        <Field label="Email (for automatic follow-ups)"><input className="input" type="email" inputMode="email" maxLength={254} value={f.email} onChange={set("email")} /></Field>
        <Field label="Invoice no."><input className="input" maxLength={40} value={f.invoice_no} onChange={set("invoice_no")} /></Field>
      </div>
      <Field label="Note"><input className="input" maxLength={300} value={f.note} onChange={set("note")} /></Field>
      <FormError msg={err} />
      <div className="row">
        <Busy className="btn sm primary" onError={setErr} run={async () => {
          await api(`/money/${d.id}`, { method: "PATCH", body: { ...f, customer: f.customer.trim(), amount: Number(f.amount) } });
          onSaved();
        }}>Save</Busy>
        <button className="btn sm ghost" onClick={onCancel}>Cancel</button>
      </div>
      {f.due_date !== d.due_date && <span className="hint">A new due date starts a fresh round of reminders.</span>}
    </div>
  );
}

function AutoLine({ auto, owner, on }) {
  const { me } = useHub();
  if (!me.features.auto_reminders) return null;
  if (owner && !me.features.automations) {   // no Automations page on this plan: approve the wording right here
    return <AutoFollowups owner a={{ key: "payment_followups_auto", available: true, name: "Automatic payment follow-ups", feature: "auto_reminders",
      what: "Once you approve the wording: emails customers 3 days before a payment is due, on the day, and every 3 days after (at most 4). Stops when it's paid or they reply." }} />;
  }
  if (!owner) return <p className="small muted">Automatic email follow-ups are {on ? <b style={{ color: "var(--aqua)" }}>on</b> : "off"}. The owner switches them in <Link to="/agents">Automations</Link>; amounts with an email get them.</p>;
  if (!auto) return null;
  return auto.on
    ? <p className="small muted">Automatic follow-ups are <b style={{ color: "var(--aqua)" }}>on</b>: an email {auto.timing.pending_days_before} days before the due date, on the day, then every {auto.timing.overdue_every_days} days (at most {auto.timing.overdue_max}). Only amounts with an email get them. <Link to="/agents">Change in Automations</Link></p>
    : <p className="small muted">Automatic email follow-ups are off. <Link to="/agents">Read the wording and switch them on in Automations</Link> — nothing goes out until you do.</p>;
}

function MoneyInner() {
  const { me, toast } = useHub();
  const [data, reload] = useLoad("/money");
  const owner = canAct(me, "owner");
  const [auto, setAuto] = useState(null);
  const [status, setStatus] = useState(null);   // managers: is it on (the switch stays with the owner)
  useEffect(() => { if (!owner && me.features.auto_reminders) api("/followups/status").then(setStatus).catch(() => setStatus(null)); }, [owner, me.features.auto_reminders]);
  const [open, setOpen] = useState({});   // due id → "history" | "edit"
  const loadAuto = () => (owner && me.features.auto_reminders ? api("/followups/auto").then(setAuto).catch(() => setAuto(null)) : Promise.resolve());
  useEffect(() => { loadAuto(); }, [owner, me.features.auto_reminders]);  // eslint-disable-line react-hooks/exhaustive-deps
  const flip = (id, what) => setOpen({ ...open, [id]: open[id] === what ? null : what });
  const t = data?.totals;
  const patch = async (id, body, msg) => { await api(`/money/${id}`, { method: "PATCH", body }); toast(msg); reload(); };
  const title = !t ? <>Know who owes you <em>what</em>.</>
    : t.overdue ? <>{inr(t.overdue)} is <em>overdue</em>.</>
    : t.due ? <>Nothing overdue. <em>Good</em>.</>
    : <>Know who owes you <em>what</em>.</>;
  return (
    <Sheet kicker="Systems · Money owed" id="SY-04" pillar="Systems" money title={title}
      sub="Write down every payment a customer still owes you, with the date it's due. Mark it paid when the money comes in.">
      {t && (
        <div className="statgrid">
          <Stat label="Owed to you" value={inr(t.due)} money note={`${t.count_due} unpaid`} />
          <Stat label="Overdue" value={inr(t.overdue)} money note={t.count_overdue ? `${t.count_overdue} past the due date` : "None past the due date"} />
          <Stat label="Collected this month" value={inr(t.collected_month)} money />
        </div>
      )}
      {me.features.reminder_agent
        ? <p className="small muted">The payment reminder automation drafts a polite reminder at 10am for anything past its due date — every few days, up to 4 times — and puts it in your <Link to="/approvals">Send list</Link>. Add how customers pay you on the <Link to="/agents">Automations</Link> page.</p>
        : null}
      {me.features.quotations && <p className="small muted">Invoices you make from a quotation in <Link to="/quotes">Quotations &amp; invoices</Link> appear here on their own.</p>}
      <AutoLine auto={auto} owner={owner} on={status?.on} />
      <AddForm onAdded={reload} />
      {data && !data.items.length && <div className="empty"><p className="muted">Nothing owed yet. Add the first payment a customer still has to make.</p></div>}
      {data?.items.length > 0 && (
        <table className="t cards">
          <thead><tr><th>Customer</th><th>Amount</th><th>Due</th><th>Reminders</th><th /></tr></thead>
          <tbody>{data.items.map((d) => (
            <React.Fragment key={d.id}>
            <tr>
              <td>
                <b>{d.customer}</b>{d.invoice_no && <span className="small muted"> · {d.invoice_no}</span>}
                {d.phone && <div className="small muted mono">{localPhone(d.phone)}</div>}
                {d.email && <div className="small muted">{d.email}</div>}
                {me.features.auto_reminders && d.status === "due" && (
                  <div className="row" style={{ gap: 6, marginTop: 4 }}>
                    {d.auto_stopped ? <Pill>Auto stopped: {STOPPED[d.auto_stopped] || d.auto_stopped}</Pill>
                      : d.auto_excluded ? <Pill kind="off">Not chased automatically</Pill>
                      : !d.email ? <Pill kind="off">No email</Pill> : null}
                  </div>
                )}
                {d.invoice_id && me.features.quotations && (
                  <div className="small"><a href={`/api/quotes/${d.invoice_id}/print`} target="_blank" rel="noreferrer">View invoice{d.invoice_no ? ` ${d.invoice_no}` : ""}</a></div>
                )}
                {d.note && <div className="small muted">{d.note}</div>}
              </td>
              <td data-l="Amount"><span className="money" style={{ fontFamily: "var(--head)", fontSize: 18 }}>{inr(d.amount)}</span></td>
              <td data-l="Due">
                <div className="stack" style={{ gap: 4 }}>
                  <span className="small">{day(d.due_date)}</span>
                  {d.status === "paid" ? <span className="token grey">Paid {day(d.paid_at)}</span>
                    : d.overdue ? <span className="token">Overdue · {d.days_overdue} {d.days_overdue === 1 ? "day" : "days"}</span> : null}
                </div>
              </td>
              <td data-l="Reminders" className="small muted">
                {(() => {
                  const emails = (d.followups || []).filter((x) => x.ok && x.channel === "email").length;
                  const parts = [];
                  if (d.reminders_sent) parts.push(`WhatsApp ${d.reminders_sent} of 4`);
                  if (emails) parts.push(`${emails} ${emails === 1 ? "email" : "emails"}`);
                  return parts.length ? parts.join(" · ") : d.status === "due" && !d.phone && !d.email ? "No number or email" : "None yet";
                })()}
              </td>
              <td>
                <div className="row">
                  {d.status === "due"
                    ? <Busy className="btn sm primary" run={() => patch(d.id, { status: "paid" }, `Marked ${inr(d.amount)} paid`)}>Mark paid</Busy>
                    : <Busy className="btn sm ghost" run={() => patch(d.id, { status: "due" }, "Marked unpaid")}>Mark unpaid</Busy>}
                  <button className="btn sm ghost" onClick={() => flip(d.id, "history")}>{open[d.id] === "history" ? "Hide history" : "History"}</button>
                  <button className="btn sm ghost" onClick={() => flip(d.id, "edit")}>Edit</button>
                  {me.features.auto_reminders && d.status === "due" && !d.auto_stopped && (d.followups || []).length > 0 && (
                    <Busy className="btn sm ghost" run={async () => {
                      await api(`/money/${d.id}/replied`, { method: "POST" });
                      toast(`Automatic follow-ups to ${d.customer} stopped for this amount`);
                      reload();
                    }}>They replied</Busy>
                  )}
                  {me.features.auto_reminders && d.status === "due" && (d.auto_excluded
                    ? <Busy className="btn sm ghost" run={async () => {
                      await api("/followups/auto/exclude", { method: "POST", body: { due_id: d.id, exclude: false } });
                      toast(`${d.customer} will be chased automatically again`);
                      await loadAuto();
                      reload();
                    }}>Chase automatically</Busy>
                    : <Busy className="btn sm ghost" run={async () => {
                      if (!window.confirm(`Stop automatic follow-ups to ${d.customer}? This covers every amount they owe. You can still remind them yourself.`)) return;
                      await api("/followups/auto/exclude", { method: "POST", body: { due_id: d.id, exclude: true } });
                      toast(`${d.customer} won't be chased automatically`);
                      await loadAuto();
                      reload();
                    }}>Don't chase automatically</Busy>)}
                  <Busy className="btn sm ghost" run={async () => {
                    if (!window.confirm(`Remove ${d.customer}'s ${inr(d.amount)}?`)) return;
                    await api(`/money/${d.id}`, { method: "DELETE" });
                    toast("Removed");
                    reload();
                  }}>Delete</Busy>
                </div>
              </td>
            </tr>
            {open[d.id] && (
              <tr><td colSpan={5}>
                {open[d.id] === "history" ? <History id={d.id} />
                  : <EditDue d={d} onCancel={() => flip(d.id, "edit")} onSaved={() => { toast("Saved"); flip(d.id, "edit"); reload(); }} />}
              </td></tr>
            )}
            </React.Fragment>
          ))}</tbody>
        </table>
      )}
    </Sheet>
  );
}

export default function Money() {
  return (
    <Gate feature="money_owed" role="manager" kicker="Systems · Money owed" id="SY-04"
      what="Track every payment a customer owes you, see what's overdue, and let the payment reminder automation chase it politely.">
      <MoneyInner />
    </Gate>
  );
}
