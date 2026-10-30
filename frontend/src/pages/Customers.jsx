import React, { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api, day, localPhone, rs, upload, waLink } from "../api.js";
import { Busy, Field, FormError, Locked, Modal, Sheet, Switch, useHub, useLoad } from "../ui.jsx";
import { Empty, Loading, Pill, Tabs } from "../v2.jsx";
import "./running.css";

const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
const pad = (n) => String(n).padStart(2, "0");
const occDate = (mmdd) => { const [m, d] = String(mmdd).split("-").map(Number); return m && d ? `${d} ${MONTHS[m - 1]}` : ""; };
const since = (n) => (n == null ? "" : n <= 0 ? "today" : n === 1 ? "1 day ago" : `${n} days ago`);
const todayIso = () => new Date().toLocaleDateString("en-CA");

// The column names the import understands (any column whose heading contains one of these words).
const COLUMNS = [
  ["Name", "name, customer, client, party"], ["Phone", "phone, mobile, number, WhatsApp"], ["Total value", "total, amount, value, sales"],
  ["Last purchase", "last purchase, last order, date"], ["What they bought", "item, product, service"], ["Reorder every (days)", "reorder, cycle, every"],
  ["Occasion", "birthday, DOB, anniversary"], ["Number of orders", "orders, purchases, visits, count"],
];

const blank = () => ({ name: "", phone: "", total_value: "", purchases: "", last_purchase: "", last_item: "", reorder_days: "",
  occ_label: "birthday", occ_m: "", occ_d: "", notes: "", opted_out: false });
const toForm = (c) => ({ name: c.name || "", phone: c.phone ? localPhone(c.phone) : "", total_value: c.total_value ?? "", purchases: c.purchases ?? "",
  last_purchase: c.last_purchase || "", last_item: c.last_item || "", reorder_days: c.reorder_days ?? "", occ_label: c.occasion?.label || "birthday",
  occ_m: c.occasion?.date?.slice(0, 2) || "", occ_d: c.occasion?.date?.slice(3, 5) || "", notes: c.notes || "", opted_out: !!c.opted_out });

function toBody(f, edit) {
  const occ = f.occ_m && f.occ_d ? { label: f.occ_label.trim().slice(0, 40) || "birthday", date: `${f.occ_m}-${f.occ_d}` } : edit ? { label: "", date: "" } : null;
  const b = { name: f.name.trim(), phone: f.phone.trim(), total_value: Number(f.total_value || 0), purchases: Math.round(Number(f.purchases || 0)),
    last_purchase: f.last_purchase || "", last_item: f.last_item.trim(), notes: f.notes.trim(), occasion: occ,
    reorder_days: f.reorder_days === "" || Number(f.reorder_days) <= 0 ? (edit ? 0 : null) : Math.round(Number(f.reorder_days)) };
  if (edit) b.opted_out = f.opted_out;
  return b;
}

function CustomerForm({ customer, onClose, onSaved }) {
  const { toast } = useHub();
  const edit = !!customer?.id;
  const [f, setF] = useState(() => (edit ? toForm(customer) : blank()));
  const [err, setErr] = useState("");
  const set = (k) => (e) => setF({ ...f, [k]: e.target.value });
  return (
    <Modal onClose={onClose}>
      <span className="kicker">{edit ? "Edit customer" : "Add a customer"}</span>
      <h2>{edit ? customer.name : <>A customer worth <em>keeping</em>.</>}</h2>
      <div className="grid2">
        <Field label="Name"><input className="input" maxLength={80} value={f.name} onChange={set("name")} placeholder="Kavita Desai" /></Field>
        <Field label="WhatsApp number"><input className="input" inputMode="tel" maxLength={24} value={f.phone} onChange={set("phone")} placeholder="98200 12345" /></Field>
        <Field label="Total bought so far (₹)"><input className="input" type="number" min="0" step="any" inputMode="decimal" value={f.total_value} onChange={set("total_value")} placeholder="150000" /></Field>
        <Field label="Number of purchases"><input className="input" type="number" min="0" step="1" inputMode="numeric" value={f.purchases} onChange={set("purchases")} placeholder="3" /></Field>
        <Field label="Last purchase"><input className="input" type="date" max={todayIso()} value={f.last_purchase} onChange={set("last_purchase")} /></Field>
        <Field label="What they bought last"><input className="input" maxLength={120} value={f.last_item} onChange={set("last_item")} placeholder="Water purifier service" /></Field>
        <Field label="Reorders every (days)" hint="Leave empty if they don't buy again on a cycle."><input className="input" type="number" min="1" max="730" step="1" inputMode="numeric" value={f.reorder_days} onChange={set("reorder_days")} placeholder="90" /></Field>
        <Field label="Occasion" hint="For a greeting on the day.">
          <div className="row" style={{ gap: 8, flexWrap: "nowrap" }}>
            <input className="input" list="rb-occasions" maxLength={40} value={f.occ_label} onChange={set("occ_label")} aria-label="Occasion name" style={{ minWidth: 0 }} />
            <select className="select" aria-label="Day" value={f.occ_d} onChange={set("occ_d")} style={{ width: 76, flex: "none" }}>
              <option value="">Day</option>{Array.from({ length: 31 }, (_, i) => <option key={i} value={pad(i + 1)}>{i + 1}</option>)}
            </select>
            <select className="select" aria-label="Month" value={f.occ_m} onChange={set("occ_m")} style={{ width: 88, flex: "none" }}>
              <option value="">Month</option>{MONTHS.map((m, i) => <option key={m} value={pad(i + 1)}>{m}</option>)}
            </select>
          </div>
          <datalist id="rb-occasions"><option value="birthday" /><option value="anniversary" /></datalist>
        </Field>
      </div>
      <Field label="Notes (optional)"><textarea className="textarea" maxLength={600} value={f.notes} onChange={set("notes")} placeholder="Prefers a call after 6pm" /></Field>
      {edit && <Switch checked={f.opted_out} onChange={(v) => setF({ ...f, opted_out: v })} label="No messages to this customer" />}
      <FormError msg={err} />
      <div className="row">
        <Busy className="btn primary" disabled={f.name.trim().length < 2} onError={setErr} run={async () => {
          if (f.occ_m && f.occ_d && Number(f.occ_d) > new Date(2024, Number(f.occ_m), 0).getDate()) throw new Error("That day doesn't exist in that month.");
          if ((f.occ_m && !f.occ_d) || (!f.occ_m && f.occ_d)) throw new Error("Pick both the day and the month of the occasion, or neither.");
          await api(edit ? `/customers/${customer.id}` : "/customers", { method: edit ? "PATCH" : "POST", body: toBody(f, edit) });
          toast(edit ? "Saved" : `Added ${f.name.trim()}`);
          onSaved();
        }}>{edit ? "Save" : "Add customer"}</Busy>
        <button className="btn ghost" onClick={onClose}>Cancel</button>
      </div>
    </Modal>
  );
}

function Import({ onDone }) {
  const { toast } = useHub();
  const [busy, setBusy] = useState(false);
  const [res, setRes] = useState(null);
  const [err, setErr] = useState("");
  const pick = async (e) => {
    const file = e.target.files?.[0];
    e.target.value = "";
    if (!file) return;
    if (file.size > 5 * 1024 * 1024) { setErr("Keep the file under 5 MB."); return; }
    setBusy(true); setErr(""); setRes(null);
    try {
      const form = new FormData();
      form.append("file", file);
      const r = await upload("/customers/import", form);
      setRes(r);
      toast(`${r.added} added, ${r.merged} merged`);
      onDone();
    } catch (x) {
      if (!x.handled) setErr(x.message);
    } finally { setBusy(false); }
  };
  return (
    <div className="panel active">
      <div className="row between">
        <span className="label">Import your old customer list</span>
        <label className={`btn sm primary rb-file ${busy ? "off" : ""}`}>
          {busy ? "Importing…" : "Upload Excel or CSV"}
          <input type="file" accept=".xlsx,.xlsm,.csv" onChange={pick} disabled={busy} aria-label="Upload an Excel or CSV customer list" />
        </label>
      </div>
      <p className="small muted">Put the column names in the first row. We need a name column and a phone or mobile column; these are read too when you have them:</p>
      <div className="rb-cols">
        {COLUMNS.map(([l, w]) => <div key={l} className="stack" style={{ gap: 2 }}><span className="small">{l}</span><span className="small muted">{w}</span></div>)}
      </div>
      <p className="small muted">Only real mobile numbers are kept, and the same number twice becomes one customer with the values added up. Up to 5 MB.</p>
      <FormError msg={err} />
      {res && (
        <div className="prompt small">
          <b>{res.added}</b> added · <b>{res.merged}</b> merged into customers already on your list · <b>{res.skipped}</b> skipped (no valid mobile number or name)
          {"\n"}Your list now: A {res.tiers.A} · B {res.tiers.B} · C {res.tiers.C}
        </div>
      )}
    </div>
  );
}

export default function Customers() {
  const { me, toast } = useHub();
  const [tier, setTier] = useState("all");
  const [q, setQ] = useState("");
  const [qq, setQQ] = useState("");
  const [form, setForm] = useState(null);   // null | {} (new) | a customer (edit)
  const [importing, setImporting] = useState(false);
  useEffect(() => { const t = setTimeout(() => setQQ(q.trim()), 300); return () => clearTimeout(t); }, [q]);
  const [d, reload] = useLoad(`/customers?tier=${tier === "all" ? "" : tier}&q=${encodeURIComponent(qq)}`);
  const counts = d?.counts || { A: 0, B: 0, C: 0 };
  const a = counts.A;
  const title = !d?.total ? <>Know your best <em>customers</em>.</>
    : a && a < d.total ? <>{a} {a === 1 ? "customer brings" : "customers bring"} <em>70%</em> of your sales.</>   // with one or two customers it's all of them
    : <>{d.total} {d.total === 1 ? "customer" : "customers"}, sorted by <em>value</em>.</>;
  const showImport = d?.can_import && (importing || d.total === 0);
  return (
    <Sheet kicker="Systems · Customers" id="RB-01" pillar="Systems" title={title}
      sub="Everyone who has bought from you: what they spent, when they last bought, when they'll need you again and the day to greet them."
      actions={<>
        {d?.can_import && d.total > 0 && <button className="btn" onClick={() => setImporting(!importing)}>{importing ? "Close import" : "Import from Excel"}</button>}
        <button className="btn primary" onClick={() => setForm({})}>Add customer</button>
      </>}>
      <p className="small muted"><b style={{ color: "var(--paper)" }}>A</b> = the customers who bring 70% of your sales, <b style={{ color: "var(--paper)" }}>B</b> = the next 20%, <b style={{ color: "var(--paper)" }}>C</b> = the rest. The list re-sorts itself whenever a value changes, and won leads join it on their own.</p>
      {me.features?.customer_desk && (
        <p className="small muted">The Customer desk automation prepares reorder reminders, occasion greetings, referral asks and check-ins with quiet customers. They wait in your <Link to="/approvals">Send list</Link> for one tap.</p>
      )}
      {d && !d.can_import && <Locked feature="customer_import" what="Import your old customer list from Excel" />}
      {showImport && <Import onDone={reload} />}

      {d && d.total > 0 && (
        <div className="stack">
          <Tabs tabs={[["all", `All · ${d.total}`], ["A", `A · ${counts.A}`], ["B", `B · ${counts.B}`], ["C", `C · ${counts.C}`]]} value={tier} onChange={setTier} />
          <input className="input" type="search" value={q} onChange={(e) => setQ(e.target.value)} placeholder="Search by name or number" aria-label="Search customers" maxLength={40} />
        </div>
      )}

      {!d ? <Loading /> : !d.total ? (
        <div className="empty">
          <h2>No customers yet.</h2>
          <p className="muted">Add your best customer first — what they've spent and when they last bought.{d.can_import ? " Or upload your old list from Excel above." : ""} Leads you mark Won join this list on their own.</p>
          <button className="btn primary" onClick={() => setForm({})}>Add my first customer</button>
        </div>
      ) : !d.items.length ? (
        <Empty>No customers match. Try another name or number, or another tier.</Empty>
      ) : (
        <table className="t cards">
          <thead><tr><th>Customer</th><th>Tier</th><th>Bought</th><th>Last purchase</th><th>Next reorder</th><th>Occasion</th><th /></tr></thead>
          <tbody>{d.items.map((c) => {
            const due = c.reorder_on && c.reorder_on <= todayIso();
            return (
              <tr key={c.id}>
                <td className="rb-wrap">
                  <b>{c.name}</b>{c.opted_out && <> <Pill kind="off">No messages</Pill></>}
                  {c.phone && <div><a className="mono small" href={waLink("", c.phone)} target="_blank" rel="noreferrer" aria-label={`WhatsApp ${c.name}`}>{localPhone(c.phone)}</a></div>}
                  {c.notes && <div className="small muted">{c.notes}</div>}
                </td>
                <td data-l="Tier">{c.tier ? <Pill kind={c.tier === "A" ? "on" : ""}>{c.tier}</Pill> : <span className="muted small">Not sorted yet</span>}</td>
                <td data-l="Bought">
                  <div className="stack" style={{ gap: 2 }}>
                    <span className="money">{rs(c.total_value)}</span>
                    <span className="small muted">{c.purchases || 0} {c.purchases === 1 ? "purchase" : "purchases"}</span>
                  </div>
                </td>
                <td data-l="Last purchase" className="small">
                  {c.last_purchase ? <div className="stack" style={{ gap: 2 }}><span>{day(c.last_purchase)}<span className="muted"> · {since(c.days_since)}</span></span>{c.last_item && <span className="muted">{c.last_item}</span>}</div> : <span className="muted">—</span>}
                </td>
                <td data-l="Next reorder" className="small">
                  {c.reorder_on ? <span>{day(c.reorder_on)}{due && <b style={{ color: "var(--aqua)" }}> · due</b>}</span> : <span className="muted">—</span>}
                </td>
                <td data-l="Occasion" className="small">
                  {c.occasion?.date ? <span>{occDate(c.occasion.date)} <span className="muted">· {c.occasion.label || "occasion"}</span></span> : <span className="muted">—</span>}
                </td>
                <td>
                  <div className="row" style={{ gap: 8 }}>
                    <button className="btn sm" onClick={() => setForm(c)}>Edit</button>
                    <Busy className="btn sm ghost" run={async () => {
                      if (!window.confirm(`Remove ${c.name} from your customers?`)) return;
                      await api(`/customers/${c.id}`, { method: "DELETE" });
                      toast("Removed");
                      reload();
                    }}>Delete</Busy>
                  </div>
                </td>
              </tr>
            );
          })}</tbody>
        </table>
      )}
      {form && <CustomerForm customer={form} onClose={() => setForm(null)} onSaved={() => { setForm(null); reload(); }} />}
    </Sheet>
  );
}
