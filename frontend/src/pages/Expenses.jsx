import React, { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { api, upload } from "../api.js";
import { Busy, Field, FormError, LoadError, Sheet, Switch, canAct, useHub, useLoad } from "../ui.jsx";
import { Empty, Loading, Num, Pill, Tabs } from "../v2.jsx";
import "./expenses.css";
import DataNav from "./DataNav.jsx";
import ExpenseAnalysis from "./ExpenseAnalysis.jsx";

/* Expenses (v2.2): every company expense in one place — the bill, the GST you can claim back, and where the money went.
   v2.3: one feature with the bank statement page and the analysis & AI advice ("Expenses & bank statement", Data engine). */

const IST = { timeZone: "Asia/Kolkata" };
const isoIn = (days = 0) => new Date(Date.now() + days * 864e5).toLocaleDateString("en-CA", IST);
const thisMonth = () => isoIn(0).slice(0, 7);
const shiftMonth = (m, n) => {
  const d = new Date(Number(m.slice(0, 4)), Number(m.slice(5, 7)) - 1 + n, 1);
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}`;
};
const monthName = (m, o = { month: "short", year: "numeric" }) => new Date(`${m}-01T00:00:00`).toLocaleDateString("en-IN", o);
const dayName = (iso) => new Date(`${iso}T00:00:00`).toLocaleDateString("en-IN", { weekday: "short", day: "numeric", month: "short" });
const money = (n) => "₹" + new Intl.NumberFormat("en-IN", { minimumFractionDigits: Number(n) % 1 ? 2 : 0, maximumFractionDigits: 2 }).format(Number(n || 0));
/** Headline numbers in whole rupees, so they fit on a phone (the list shows the paise). */
const whole = (n) => "₹" + new Intl.NumberFormat("en-IN", { maximumFractionDigits: 0 }).format(Math.round(Number(n || 0)));
const count = (n) => new Intl.NumberFormat("en-IN").format(Number(n || 0));
const RATES = [5, 18, 40];   // GST 2.0 slabs
const PAID_BY = [["upi", "UPI"], ["bank", "Bank"], ["cash", "Cash"], ["card", "Card"], ["cheque", "Cheque"]];
const PAID_LABEL = { upi: "UPI", bank: "Bank transfer", cash: "Cash", card: "Card", cheque: "Cheque" };

// per-viewer conveniences only (the last "paid by", the open tab): never needed for the page to work
const pref = {
  get: (k) => { try { return window.localStorage.getItem(`ah_exp_${k}`); } catch { return null; } },
  set: (k, v) => { try { window.localStorage.setItem(`ah_exp_${k}`, v); } catch { /* private window */ } },
};

/** Like useLoad, but loads nothing while `path` is empty (staff never ask for the manager-only parts). */
function useMaybe(path) {
  const [data, setData] = useState(null);
  const [error, setError] = useState(null);
  const latest = useRef(path);
  latest.current = path;
  const reload = useCallback(() => (path
    ? api(path).then((d) => { if (latest.current === path) { setData(d); setError(null); } return d; })
      .catch((e) => { if (latest.current === path) setError(e); })
    : Promise.resolve(null)), [path]);
  useEffect(() => { reload(); }, [reload]);
  return [path ? data : null, reload, error];
}

/** Phone photos are big: anything over 1.5 MB, or not a PNG/JPEG (HEIC, WebP), is redrawn as a JPEG up to 2000px. */
async function shrink(file) {
  if (!file || !file.type.startsWith("image/")) return file;
  if (file.size < 1.5e6 && ["image/png", "image/jpeg"].includes(file.type)) return file;
  try {
    const bmp = await createImageBitmap(file);
    const scale = Math.min(1, 2000 / Math.max(bmp.width, bmp.height));
    const c = document.createElement("canvas");
    c.width = Math.round(bmp.width * scale);
    c.height = Math.round(bmp.height * scale);
    c.getContext("2d").drawImage(bmp, 0, 0, c.width, c.height);
    const blob = await new Promise((r) => c.toBlob(r, "image/jpeg", 0.85));
    return blob ? new File([blob], `${(file.name || "bill").replace(/\.[^.]+$/, "")}.jpg`, { type: "image/jpeg" }) : file;
  } catch {
    return file;   // the server checks the type and says so if it can't take it
  }
}

const BillIcon = () => (
  <svg width="14" height="14" viewBox="0 0 16 16" aria-hidden="true" fill="none" stroke="currentColor" strokeWidth="1.4">
    <path d="M3.5 1.5h9v13l-2-1.2-2.5 1.2-2.5-1.2-2 1.2z" /><path d="M6 5h4M6 8h4M6 11h2" />
  </svg>
);

// ───────────────────────── the add / edit form ─────────────────────────
const blank = () => ({
  amount: "", date: isoIn(0), category: "", vendor: "", paid_by: pref.get("paid_by") || "upi", gstOn: false, gst: "", gst_claimable: false,
  vendor_gstin: "", project: "", note: "", recurring: false,
});

function ExpenseForm({ initial, settings, items, onDone, onCancel }) {
  const { me, toast } = useHub();
  const manager = canAct(me, "manager");
  const [f, setF] = useState(() => (initial ? {
    amount: initial.amount, date: initial.date, category: initial.category, vendor: initial.vendor || "", paid_by: initial.paid_by,
    gstOn: initial.gst > 0 || !!initial.vendor_gstin, gst: initial.gst || "", gst_claimable: !!initial.gst_claimable,
    vendor_gstin: initial.vendor_gstin || "", project: initial.project || "", note: initial.note || "", recurring: !!initial.recurring,
  } : blank()));
  const [file, setFile] = useState(null);
  const [err, setErr] = useState("");
  const set = (k) => (e) => setF({ ...f, [k]: e.target.value });
  const site = settings?.kind === "construction";
  const own = (settings?.categories || []).filter((c) => c.extra);
  const rest = (settings?.categories || []).filter((c) => !c.extra);
  const known = (settings?.categories || []).some((c) => c.key === f.category);
  const vendors = useMemo(() => [...new Set((items || []).map((i) => i.vendor).filter(Boolean))].slice(0, 40), [items]);
  const projects = useMemo(() => [...new Set((items || []).map((i) => i.project).filter(Boolean))].slice(0, 20), [items]);
  const amount = Number(f.amount) || 0;
  const gst = f.gstOn ? Number(f.gst) || 0 : 0;
  const gstTooBig = f.gstOn && gst > 0 && gst >= amount;
  const hasGstin = f.vendor_gstin.replace(/\s+/g, "").length > 0;
  const ready = amount > 0 && f.date && f.category && !gstTooBig;
  const rate = (r) => setF({ ...f, gst: Math.round((amount * r * 100) / (100 + r)) / 100 });

  const save = async () => {
    const body = {
      date: f.date, amount, category: f.category, vendor: f.vendor.trim(), paid_by: f.paid_by, gst, gst_claimable: f.gstOn && f.gst_claimable && gst > 0 && hasGstin,
      vendor_gstin: f.gstOn ? f.vendor_gstin.replace(/\s+/g, "").toUpperCase() : "", project: f.project.trim(), note: f.note.trim(), recurring: f.recurring,
    };
    const saved = await api(initial ? `/expenses/${initial.id}` : "/expenses", { method: initial ? "PATCH" : "POST", body });
    pref.set("paid_by", f.paid_by);
    if (file) {
      const form = new FormData();
      form.append("file", await shrink(file));
      try { await upload(`/expenses/${saved.id}/bill`, form); }
      catch (e) { if (!e.handled) toast(`Saved, but the bill didn't upload: ${e.message}`, "err"); onDone(saved); return; }
    }
    toast(initial ? "Saved" : saved.status === "to_approve" ? "Added. It goes to your manager for approval." : `Added ${money(saved.amount)}`);
    if (!initial) { setF({ ...blank(), paid_by: f.paid_by }); setFile(null); }
    onDone(saved);
  };

  const siteField = (
    <Field label={site ? "Site / project" : "Project or order (optional)"} hint={site ? "So you can see what each site really cost." : ""}>
      <input className="input" maxLength={60} list="ex-projects" value={f.project} onChange={set("project")} placeholder={site ? "e.g. Powai site, flat 302" : "e.g. Order 118"} />
    </Field>
  );
  return (
    <div className="stack ex-form">
      <div className="grid3">
        <Field label="Amount (₹)" hint={f.gstOn ? "The bill total, including GST" : ""}>
          <input className="input ex-big" type="number" min="0" step="any" inputMode="decimal" autoFocus={!initial} value={f.amount} onChange={set("amount")} placeholder="0" />
        </Field>
        <Field label="Date"><input className="input" type="date" min={isoIn(-3 * 365)} max={isoIn(7)} value={f.date} onChange={set("date")} /></Field>
        <Field label="Category">
          <select className="select" value={f.category} onChange={set("category")}>
            <option value="">Pick one</option>
            {!known && f.category && <option value={f.category}>{initial?.category_label || f.category}</option>}
            {own.length > 0 && <optgroup label={`For your business · ${settings.kind_label}`}>{own.map((c) => <option key={c.key} value={c.key}>{c.label}</option>)}</optgroup>}
            <optgroup label="Every business">{rest.map((c) => <option key={c.key} value={c.key}>{c.label}</option>)}</optgroup>
          </select>
        </Field>
      </div>
      <div className="grid2">
        <Field label="Paid to">
          <input className="input" maxLength={80} list="ex-vendors" value={f.vendor} onChange={set("vendor")} placeholder="e.g. Sharma Steel Traders" />
        </Field>
        {site && siteField}
      </div>
      <datalist id="ex-vendors">{vendors.map((v) => <option key={v} value={v} />)}</datalist>
      <datalist id="ex-projects">{projects.map((v) => <option key={v} value={v} />)}</datalist>

      <div className="stack" style={{ gap: 8 }}>
        <span className="label">Paid by</span>
        <div className="ex-chips" role="group" aria-label="Paid by">
          {PAID_BY.map(([k, l]) => <button key={k} type="button" className="ex-chip" aria-pressed={f.paid_by === k} onClick={() => setF({ ...f, paid_by: k })}>{l}</button>)}
        </div>
      </div>

      <div className="ex-gst">
        <Switch checked={f.gstOn} onChange={(v) => setF({ ...f, gstOn: v })} label="GST bill" />
        {f.gstOn && (
          <div className="stack">
            <div className="grid2">
              <Field label="GST on this bill (₹)" hint="The rate buttons work it out from the total. Check it against the bill.">
                <input className="input" type="number" min="0" step="any" inputMode="decimal" value={f.gst} onChange={set("gst")} />
              </Field>
              <div className="stack" style={{ gap: 8 }}>
                <span className="label">Work it out at</span>
                <div className="ex-chips">
                  {RATES.map((r) => <button key={r} type="button" className="ex-chip" disabled={!amount} onClick={() => rate(r)}>{r}%</button>)}
                </div>
              </div>
            </div>
            {gstTooBig && <p className="form-err">The GST has to be less than the amount. The amount is the bill total, including GST.</p>}
            <Field label="Vendor's GST number (optional)" hint={f.vendor_gstin && f.vendor_gstin.replace(/\s+/g, "").length !== 15 ? "A GST number has 15 characters, like 27ABCDE1234F1Z5." : ""}>
              <input className="input mono" maxLength={20} autoCapitalize="characters" value={f.vendor_gstin} onChange={set("vendor_gstin")} placeholder="27ABCDE1234F1Z5" />
            </Field>
            <Switch checked={f.gst_claimable && hasGstin} disabled={!hasGstin} onChange={(v) => setF({ ...f, gst_claimable: v })} label="I can claim this GST back" />
            <span className="hint">{hasGstin
              ? "Only if the bill is in your business's name, with your GST number on it. Ask your CA if you're not sure."
              : "Add the vendor's GST number from the bill to claim it back. Without it, the GST can't be claimed."}</span>
          </div>
        )}
      </div>

      <details className="ex-more" open={!!(initial && (initial.note || (!site && initial.project)))}>
        <summary>{site ? "Add a note" : "Project and note"}</summary>
        <div className="grid2">
          {!site && siteField}
          <Field label="Note (optional)"><input className="input" maxLength={300} value={f.note} onChange={set("note")} placeholder="e.g. 20 bags cement" /></Field>
        </div>
      </details>

      <div className="grid2">
        <Field label={initial?.bill ? "Replace the bill" : "Bill photo or PDF (optional)"} hint="Take a photo of the bill so it's never lost.">
          <input className="input" type="file" accept="image/*,application/pdf" onChange={(e) => setFile(e.target.files?.[0] || null)} />
        </Field>
        <div className="stack" style={{ alignContent: "end", gap: 6 }}>
          <Switch checked={f.recurring} onChange={(v) => setF({ ...f, recurring: v })} label="This repeats every month" />
        </div>
      </div>
      {initial?.bill && (
        <div className="row small">
          <a href={`/api/expenses/${initial.id}/bill`} target="_blank" rel="noreferrer"><BillIcon /> See the bill</a>
          <Busy className="btn sm ghost" run={async () => { await api(`/expenses/${initial.id}/bill`, { method: "DELETE" }); toast("Bill removed"); onDone(); }}>Remove the bill</Busy>
        </div>
      )}
      {!manager && !initial && <span className="hint">What you add goes to your manager for approval.</span>}
      <FormError msg={err} />
      <div className="row">
        <Busy className="btn primary" disabled={!ready} onError={setErr} run={save}>{initial ? "Save" : "Save expense"}</Busy>
        {onCancel && <button className="btn ghost" onClick={onCancel}>Cancel</button>}
      </div>
    </div>
  );
}

// ───────────────────────── the list ─────────────────────────
function Row({ it, settings, items, onChange }) {
  const { me, toast } = useHub();
  const manager = canAct(me, "manager");
  const [editing, setEditing] = useState(false);
  if (editing) {
    return (
      <div className="ex-row editing">
        <ExpenseForm initial={it} settings={settings} items={items} onCancel={() => setEditing(false)} onDone={() => { setEditing(false); onChange(); }} />
      </div>
    );
  }
  return (
    <div className="ex-row">
      <div className="who">
        <b>{it.vendor || it.category_label}</b>
        <div className="small muted">
          {it.vendor ? it.category_label : ""}{it.vendor ? " · " : ""}{PAID_LABEL[it.paid_by] || it.paid_by}{it.project ? ` · ${it.project}` : ""}
          {it.added_by ? ` · added by ${it.added_by}` : ""}
        </div>
        {it.note && <div className="small muted ex-note">{it.note}</div>}
        <div className="row" style={{ gap: 6, marginTop: 6 }}>
          {it.status === "to_approve" && <Pill kind="warn">To approve</Pill>}
          {it.source === "bank" && <Pill>From bank statement</Pill>}
          {it.recurring && <Pill>Repeats monthly</Pill>}
          {it.in_cash_flow && <Pill kind="on">In cash flow</Pill>}
        </div>
      </div>
      <div className="amt">
        <span className="money">{money(it.amount)}</span>
        {it.gst > 0 && <span className="small muted">GST {money(it.gst)}{it.gst_claimable ? " · can claim" : ""}</span>}
      </div>
      <div className="acts">
        {it.bill
          ? <a className="btn sm ghost" href={`/api/expenses/${it.id}/bill`} target="_blank" rel="noreferrer"><BillIcon /> Bill</a>
          : <span className="small muted ex-nobill">No bill</span>}
        {manager && it.status === "to_approve" && (
          <Busy className="btn sm primary" run={async () => { await api(`/expenses/${it.id}/approve`, { method: "POST" }); toast("Approved"); onChange(); }}>Approve</Busy>
        )}
        {it.can_edit && <button className="btn sm ghost" onClick={() => setEditing(true)}>Edit</button>}
        {it.can_edit && (
          <Busy className="btn sm ghost" run={async () => {
            const cf = it.in_cash_flow ? "\n\nIt stays in Cash flow as a monthly payment. Remove it there if it's no longer paid every month." : "";
            if (!window.confirm(`Delete ${money(it.amount)}${it.vendor ? ` paid to ${it.vendor}` : ""}? The bill goes too.${cf}`)) return;
            await api(`/expenses/${it.id}`, { method: "DELETE" });
            toast("Deleted");
            onChange();
          }}>Delete</Busy>
        )}
      </div>
    </div>
  );
}

function List({ list, settings, filters, setFilters, onChange, manager, month, onAdd }) {
  const groups = useMemo(() => {
    const g = [];
    for (const it of list?.items || []) {
      const last = g[g.length - 1];
      if (last && last.date === it.date) { last.items.push(it); last.total += it.amount; }
      else g.push({ date: it.date, items: [it], total: it.amount });
    }
    return g;
  }, [list]);
  const filtered = filters.category || filters.q || filters.status;
  return (
    <div className="stack">
      <div className="ex-filters">
        <select className="select" value={filters.category} onChange={(e) => setFilters({ ...filters, category: e.target.value })} aria-label="Category">
          <option value="">All categories</option>
          {(settings?.categories || []).map((c) => <option key={c.key} value={c.key}>{c.label}</option>)}
        </select>
        <input className="input" type="search" maxLength={60} value={filters.q} onChange={(e) => setFilters({ ...filters, q: e.target.value })}
          placeholder="Search vendor, note or site" aria-label="Search" />
        {manager && (
          <button type="button" className="ex-chip" aria-pressed={filters.status === "to_approve"}
            onClick={() => setFilters({ ...filters, status: filters.status === "to_approve" ? "" : "to_approve" })}>
            To approve{list?.totals?.to_approve && !filters.status ? ` (${list.totals.to_approve})` : ""}
          </button>
        )}
      </div>
      {!list ? <Loading /> : !list.items.length ? (
        filtered ? <Empty>Nothing matches in {monthName(month, { month: "long", year: "numeric" })}.</Empty> : (
          <div className="panel flat empty">
            <h3>Nothing added for {monthName(month, { month: "long" })} yet.</h3>
            <p className="muted">Start with the bills you paid this week: rent, a supplier, fuel, salaries. Take a photo of each bill as you add it, so it's never lost.</p>
            <button className="btn primary" onClick={onAdd}>Add an expense</button>
          </div>
        )
      ) : (
        <div className="ex-list">
          {groups.map((g) => (
            <section key={g.date}>
              <div className="ex-day"><span>{dayName(g.date)}</span><span className="money">{money(g.total)}</span></div>
              {g.items.map((it) => <Row key={it.id} it={it} settings={settings} items={list.items} onChange={onChange} />)}
            </section>
          ))}
          {list.more && <p className="small muted">This list shows the latest {count(list.shown_cap)} entries of the month{list.totals_capped ? "" : ", and the totals count all of them"}. Search or pick a category to see the rest.</p>}
        </div>
      )}
    </div>
  );
}

// ───────────────────────── where it went ─────────────────────────
function WhereItWent({ sum, month }) {
  if (!sum) return <Loading />;
  const rows = sum.by_category.filter((r) => r.amount > 0 || r.avg_3m > 0 || r.budget);
  const top = Math.max(1, ...rows.map((r) => Math.max(r.amount, r.avg_3m || 0, r.budget || 0)));
  const pct = (v) => `${Math.min(100, (v / top) * 100)}%`;
  const paidTotal = Object.values(sum.by_paid_by).reduce((a, b) => a + b, 0);
  const usual = sum.avg_months === 3 ? "your 3-month average" : sum.avg_months ? `your average over the ${sum.avg_months === 1 ? "month" : `${sum.avg_months} months`} before with entries` : "";
  return (
    <div className="stack">
      {sum.jumps.length > 0 && (
        <div className="ex-jumps">
          {sum.jumps.map((j) => <p key={j.key}><b>{j.label}</b> {j.line.replace(`${j.label} `, "")}</p>)}
        </div>
      )}
      <div className="panel">
        <span className="label">By category · {monthName(month)}</span>
        {!rows.length ? <span className="small muted">Nothing this month yet.</span> : (
          <div className="ex-cats">
            {rows.map((r) => (
              <div key={r.key} className="ex-cat">
                <div className="top"><span>{r.label}</span><span className="money">{money(r.amount)}</span></div>
                <div className="ex-track" role="img" aria-label={`${r.label}: ${money(r.amount)} this month${r.avg_3m != null ? `, usually ${money(r.avg_3m)}` : ""}${r.budget ? `, budget ${money(r.budget)}` : ""}`}>
                  <i className={r.over_budget ? "over" : ""} style={{ width: pct(r.amount) }} />
                  {r.avg_3m > 0 && <b className="avg" style={{ left: pct(r.avg_3m) }} />}
                  {r.budget > 0 && <b className="bud" style={{ left: pct(r.budget) }} />}
                </div>
                <div className="small muted">
                  {r.avg_3m == null ? "No earlier months to compare" : r.avg_3m === 0 ? "Nothing on this in the months before" : `Usual ${money(r.avg_3m)}`}
                  {r.budget ? ` · Budget ${money(r.budget)}` : ""}
                  {r.over_budget ? <span className="money"> · {money(r.amount - r.budget)} over budget</span> : ""}
                </div>
              </div>
            ))}
          </div>
        )}
        <div className="ex-legend">
          <span><i className="bar" />This month</span>
          {usual && <span><i className="avg" />Usual ({usual})</span>}
          <span><i className="bud" />Budget</span>
        </div>
      </div>

      <div className="grid2">
        <div className="panel">
          <span className="label">Paid the most</span>
          {!sum.top_vendors.length ? <span className="small muted">Add who you paid to see this.</span> : (
            <div className="ex-kv">{sum.top_vendors.map((v) => (
              <div key={v.vendor}><span>{v.vendor}<span className="small muted"> · {v.count} {v.count === 1 ? "payment" : "payments"}</span></span><span className="money">{money(v.amount)}</span></div>
            ))}</div>
          )}
        </div>
        <div className="panel">
          <span className="label">How it was paid</span>
          <div className="ex-kv">{PAID_BY.filter(([k]) => sum.by_paid_by[k] > 0).map(([k, l]) => (
            <div key={k}><span>{l}<span className="small muted"> · {Math.round((sum.by_paid_by[k] / paidTotal) * 100)}%</span></span><span className="money">{money(sum.by_paid_by[k])}</span></div>
          ))}</div>
          {!paidTotal && <span className="small muted">Nothing this month yet.</span>}
        </div>
      </div>

      {sum.projects.length > 0 && (
        <div className="panel">
          <span className="label">By site / project</span>
          <div className="ex-kv">{sum.projects.map((p) => <div key={p.project}><span>{p.project}</span><span className="money">{money(p.amount)}</span></div>)}</div>
        </div>
      )}
    </div>
  );
}

// ───────────────────────── repeating payments ─────────────────────────
function Repeating({ sum, onChange }) {
  const { me, toast } = useHub();
  if (!sum) return <Loading />;
  const label = (r) => r.vendor || r.category_label;
  return (
    <div className="stack">
      <p className="small muted">Payments you marked “repeats every month”, and ones that came up in at least 2 of the last 3 months at about the same amount (within 15%).</p>
      <p className="small muted">A payment the bank statement page already put into Cash flow (same name, about the same amount) shows as in Cash flow here and is never added twice.</p>
      {!sum.recurring.length ? <Empty>No repeating payments yet. Tick “This repeats every month” when you add rent, salaries or a subscription.</Empty> : (
        <div className="ex-list">
          {sum.recurring.map((r) => (
            <div key={`${r.expense_id}`} className="ex-row">
              <div className="who">
                <b>{label(r)}</b>
                <div className="small muted">{r.vendor ? `${r.category_label} · ` : ""}around day {r.day_of_month} · in {r.months} of the last 3 months</div>
                <div className="row" style={{ gap: 6, marginTop: 6 }}>
                  <Pill>{r.marked ? "You marked it" : "Spotted from your entries"}</Pill>
                  {r.in_cash_flow && <Pill kind="on">In cash flow ✓</Pill>}
                </div>
                {r.in_knowledge_hub && !r.in_cash_flow && (
                  <p className="small ex-warn">Your Knowledge Hub already has “{r.in_knowledge_hub.name}” ({money(r.in_knowledge_hub.amount)} a month), and Cash flow counts that already. Adding this too would count it twice.</p>
                )}
              </div>
              <div className="amt"><span className="money">{money(r.amount)}</span><span className="small muted">a month, usually</span></div>
              <div className="acts">
                {!sum.can_cash_flow ? null : r.in_cash_flow ? (
                  <>
                    {me.features?.cash_flow && <Link className="btn sm ghost" to="/cash-flow">See it in Cash flow</Link>}
                    <Busy className="btn sm ghost" run={async () => {
                      if (!window.confirm(`Stop counting ${label(r)} in Cash flow every month?`)) return;
                      await api(`/expenses/${r.expense_id}/to-cash-flow`, { method: "DELETE" });
                      toast("Taken out of Cash flow");
                      onChange();
                    }}>Take out</Busy>
                  </>
                ) : (
                  <Busy className="btn sm" run={async () => {
                    if (r.in_knowledge_hub && !window.confirm("This looks like a fixed cost your Knowledge Hub already has. Add it to Cash flow anyway?")) return;
                    const out = await api(`/expenses/${r.expense_id}/to-cash-flow`, { method: "POST" });
                    toast(out.created ? `Cash flow now counts ${money(out.cash_item.amount)} on day ${out.cash_item.day_of_month} every month`
                      : `Already in Cash flow as “${out.cash_item.title}”, so it’s counted once`);
                    onChange();
                  }}>Add to cash flow</Busy>
                )}
              </div>
            </div>
          ))}
        </div>
      )}
      {sum.can_cash_flow && sum.recurring.length > 0 && (
        <p className="small muted">Cash flow counts the amount of the latest bill on the same day every month. That's a forecast, not a promise: change the amount in Cash flow if it moves.</p>
      )}
    </div>
  );
}

// ───────────────────────── for the CA ─────────────────────────
function ForCA() {
  const prev = shiftMonth(thisMonth(), -1);
  const lastDay = new Date(Number(prev.slice(0, 4)), Number(prev.slice(5, 7)), 0).getDate();
  const [r, setR] = useState({ from: `${prev}-01`, to: `${prev}-${String(lastDay).padStart(2, "0")}` });
  const days = r.from && r.to ? (new Date(r.to) - new Date(r.from)) / 864e5 : -1;
  const problem = !r.from || !r.to ? "Pick both dates." : days < 0 ? "The ‘to’ date is before the ‘from’ date." : days > 366 ? "Download at most one year at a time." : "";
  return (
    <div className="panel">
      <span className="label">Download for your CA</span>
      <p className="small muted">One line per expense: date, vendor, their GST number, category, amount, GST, whether you can claim it, how it was paid, site, note, whether the bill is attached, and if it's approved. Opens in Excel or Google Sheets.</p>
      <div className="grid2">
        <Field label="From"><input className="input" type="date" value={r.from} onChange={(e) => setR({ ...r, from: e.target.value })} /></Field>
        <Field label="To"><input className="input" type="date" value={r.to} onChange={(e) => setR({ ...r, to: e.target.value })} /></Field>
      </div>
      {problem && r.from && r.to ? <p className="form-err">{problem}</p> : null}
      <div>
        {problem ? <button className="btn primary" disabled>Download for your CA (CSV)</button>
          : <a className="btn primary" href={`/api/expenses/export.csv?from=${r.from}&to=${r.to}`} download>Download for your CA (CSV)</a>}
      </div>
      <p className="small muted">The bills themselves stay here. Open any of them from the list.</p>
      <p className="small muted">One file holds up to 50,000 lines. If you have more in a range, download it in shorter parts.</p>
    </div>
  );
}

// ───────────────────────── settings ─────────────────────────
function Settings({ settings, sum, onSaved }) {
  const { toast } = useHub();
  const [kind, setKind] = useState(settings.kind);
  const [b, setB] = useState(() => ({ ...settings.budgets }));
  const [pick, setPick] = useState("");
  const [label, setLabel] = useState("");
  const [err, setErr] = useState("");
  const labels = Object.fromEntries(settings.categories.map((c) => [c.key, c.label]));
  const shown = useMemo(() => {
    const keys = Object.keys(b).filter((k) => labels[k]);
    for (const r of sum?.by_category || []) if ((r.amount > 0 || r.avg_3m > 0) && labels[r.key] && !keys.includes(r.key) && keys.length < 12) keys.push(r.key);
    return keys;
  }, [b, sum]); // eslint-disable-line react-hooks/exhaustive-deps
  const put = async (body, msg) => { await api("/expenses/settings", { method: "PUT", body }); toast(msg); onSaved(); };
  return (
    <div className="stack">
      <div className="panel">
        <span className="label">Your kind of business</span>
        <p className="small muted">{settings.chosen ? "This decides which categories come first." : "We picked this from what your Knowledge Hub says your business does. Change it if it's wrong."}</p>
        <div className="ex-filters">
          <select className="select" value={kind} onChange={(e) => setKind(e.target.value)} aria-label="Kind of business">
            {settings.kinds.map((k) => <option key={k.key} value={k.key}>{k.label}</option>)}
          </select>
          <Busy className="btn sm" disabled={kind === settings.kind && settings.chosen} run={() => put({ kind }, "Saved")}>Save</Busy>
        </div>
      </div>

      <div className="panel">
        <span className="label">Monthly budgets</span>
        <p className="small muted">A limit for a category, a month. “Where it went” shows a line at the budget and tells you when you go over. Leave it empty for no budget.</p>
        <div className="ex-budgets">
          {shown.map((k) => (
            <label key={k} className="ex-budget">
              <span>{labels[k]}</span>
              <Num min="0" step="any" value={b[k] ?? ""} onChange={(v) => setB({ ...b, [k]: v })} placeholder="No budget" aria-label={`${labels[k]} budget`} />
            </label>
          ))}
        </div>
        <div className="ex-filters">
          <select className="select" value={pick} onChange={(e) => setPick(e.target.value)} aria-label="Add a budget for">
            <option value="">Add a budget for…</option>
            {settings.categories.filter((c) => !shown.includes(c.key)).map((c) => <option key={c.key} value={c.key}>{c.label}</option>)}
          </select>
          <button className="btn sm ghost" disabled={!pick} onClick={() => { setB({ ...b, [pick]: "" }); setPick(""); }}>Add</button>
        </div>
        <FormError msg={err} />
        <div><Busy className="btn sm primary" onError={setErr} run={() => put({ budgets: Object.fromEntries(Object.entries(b).map(([k, v]) => [k, Number(v) || 0])) }, "Budgets saved")}>Save budgets</Busy></div>
      </div>

      <div className="panel">
        <span className="label">Your own categories ({settings.custom.length} of 10)</span>
        <p className="small muted">For heads that matter to you and aren't in the list, like “Trade fairs” or “Puja & festivals”.</p>
        {settings.custom.length > 0 && (
          <div className="ex-kv">{settings.custom.map((c) => (
            <div key={c.key}>
              <span>{c.label}</span>
              <Busy className="btn sm ghost" run={async () => {
                if (!window.confirm(`Remove “${c.label}”? Expenses already under it keep it; new ones can't use it.`)) return;
                await put({ custom: settings.custom.filter((x) => x.key !== c.key) }, "Removed");
              }}>Remove</Busy>
            </div>
          ))}</div>
        )}
        {settings.custom.length < 10 && (
          <div className="inline-add">
            <input className="input" maxLength={40} value={label} onChange={(e) => setLabel(e.target.value)} placeholder="Name of the category" />
            <Busy className="btn sm" disabled={label.trim().length < 2} onError={setErr} run={async () => {
              await put({ custom: [...settings.custom, { label: label.trim() }] }, "Added");
              setLabel("");
            }}>Add</Busy>
          </div>
        )}
      </div>
    </div>
  );
}

// ───────────────────────── the page ─────────────────────────
/** Said plainly when a number doesn't count everything. */
function Caps({ sum, list, manager }) {
  const lines = [];
  if (manager && sum?.capped) lines.push(`You have more than ${count(sum.cap)} entries in these 4 months, so these numbers leave out the oldest ones. Download the CSV for your CA to see everything.`);
  if (list?.totals_capped) lines.push(`More than ${count(list.totals_cap)} entries match, so the totals count the latest ${count(list.totals_cap)} only.`);
  return lines.length ? <div className="ex-cap">{lines.map((l) => <p key={l} className="small">{l}</p>)}</div> : null;
}

function Stats({ sum, list, month, manager }) {
  if (!manager) {
    const t = list?.totals;
    if (!t) return null;
    return (
      <div className="statgrid">
        <div className="stat"><span className="label">You added · {monthName(month, { month: "short" })}</span><span className="num money">{whole(t.spent)}</span>
          <span className="small muted">{t.count} {t.count === 1 ? "expense" : "expenses"}</span></div>
        <div className="stat"><span className="label">Waiting for approval</span><span className="num">{t.to_approve}</span></div>
      </div>
    );
  }
  if (!sum) return null;
  const prev = monthName(shiftMonth(month, -1), { month: "short" });
  const change = sum.change_pct == null ? `Nothing recorded in ${prev}`
    : sum.change_pct === 0 ? `Same as ${prev}` : `${Math.abs(sum.change_pct)}% ${sum.change_pct > 0 ? "more" : "less"} than ${prev} (${whole(sum.last_month_spent)})`;
  const big = sum.by_category[0]?.amount > 0 ? sum.by_category[0] : null;
  return (
    <div className="statgrid">
      <div className="stat"><span className="label">Spent · {monthName(month, { month: "short" })}</span><span className="num money">{whole(sum.spent)}</span>
        <span className="small muted">{change}{sum.to_approve ? `. Includes ${sum.to_approve} waiting for approval.` : ""}</span></div>
      <div className="stat"><span className="label">GST you can claim back</span><span className="num money">{whole(sum.gst_claimable)}</span>
        <span className="small muted">If the bills are in your business's name.{sum.gst_claimable_waiting ? ` ${money(sum.gst_claimable_waiting)} more once approved.` : ""}</span></div>
      <div className="stat"><span className="label">Biggest head</span><span className="num ex-head">{big ? big.label : "—"}</span>
        <span className="small muted">{big ? `${whole(big.amount)} · ${Math.round((big.amount / Math.max(sum.spent, 0.01)) * 100)}% of the month` : "Nothing yet"}</span></div>
      {sum.budget_total ? (
        <div className="stat"><span className="label">Budget used</span>
          <span className={`num ${sum.budget_spent > sum.budget_total ? "money" : ""}`}>{Math.round((sum.budget_spent / sum.budget_total) * 100)}%</span>
          <span className="small muted">{whole(sum.budget_spent)} of {whole(sum.budget_total)}, on heads with a budget</span></div>
      ) : null}
    </div>
  );
}

export default function Expenses() {
  const { me } = useHub();
  const manager = canAct(me, "manager");
  const owner = canAct(me, "owner");
  const [month, setMonth] = useState(thisMonth);
  const [params0, setParams] = useSearchParams();
  const [tab, setTabState] = useState(() => (manager && (params0.get("tab") || pref.get("tab"))) || "list");
  const urlTab = params0.get("tab");
  useEffect(() => {   // the Data links: "Analysis & AI advice" is /expenses?tab=analysis, "Expenses" is /expenses
    if (!manager) return;
    if (urlTab && urlTab !== tab) setTabState(urlTab);
    else if (!urlTab && tab === "analysis") setTabState("list");
  }, [urlTab]);
  const setTab = (t) => { setTabState(t); pref.set("tab", t); setParams(t === "analysis" ? { tab: "analysis" } : {}, { replace: true }); };
  const [filters, setFilters] = useState({ category: "", q: "", status: "" });
  const [q, setQ] = useState("");
  useEffect(() => { const t = setTimeout(() => setQ(filters.q.trim()), 300); return () => clearTimeout(t); }, [filters.q]);
  const params = new URLSearchParams({ month });
  if (filters.category) params.set("category", filters.category);
  if (q) params.set("q", q);
  if (filters.status) params.set("status", filters.status);
  const [list, reloadList, listErr] = useLoad(`/expenses?${params}`);
  const [settings, reloadSettings] = useLoad("/expenses/settings");
  const [sum, reloadSum, sumErr] = useMaybe(manager ? `/expenses/summary?month=${month}` : "");
  const [adding, setAdding] = useState(false);
  const maxMonth = isoIn(7).slice(0, 7);
  const reload = () => { reloadList(); reloadSum(); };
  const added = (saved) => {
    if (saved?.date && saved.date.slice(0, 7) !== month) setMonth(saved.date.slice(0, 7));   // show it where it landed
    reload();
  };

  const tabs = [["list", "List"], ["where", "Where it went"], ["analysis", "Analysis"], ["repeat", "Repeating"], ["ca", "For your CA"], ["settings", "Settings"]];
  if (tab === "analysis" && manager) {
    return (
      <Sheet kicker="Data · Expenses & bank statement" id="DA-02" pillar="Data" money title={<>Where the money <em>goes</em>, month by month.</>}
        sub="Spend by month and by category, who you paid the most, money in against money out — and, when you ask, the AI's advice from the totals.">
        <DataNav />
        <Tabs tabs={tabs} value={tab} onChange={setTab} />
        <ExpenseAnalysis />
      </Sheet>
    );
  }
  return (
    <Sheet kicker="Data · Expenses & bank statement" id="ST-13" pillar="Data" money title={<>Where did the money <em>go</em>?</>}
      sub="Every bill and payment in one place: what you spent, the GST you can claim back, and where it went.">
      <DataNav />
      <div className="ex-top">
        <div className="ex-month" role="group" aria-label="Month">
          <button className="btn sm ghost" aria-label="Previous month" onClick={() => setMonth(shiftMonth(month, -1))}>‹</button>
          <b>{monthName(month)}</b>
          <button className="btn sm ghost" aria-label="Next month" disabled={month >= maxMonth} onClick={() => setMonth(shiftMonth(month, 1))}>›</button>
        </div>
        {!adding && <button className="btn primary" onClick={() => setAdding(true)}>Add an expense</button>}
      </div>

      {listErr && <LoadError err={listErr} />}
      {sumErr && <FormError msg={sumErr.message} />}
      <Stats sum={sum} list={list} month={month} manager={manager} />
      <Caps sum={sum} list={list} manager={manager} />

      {adding && (
        <div className="panel active">
          <div className="row between"><span className="label">Add an expense</span><button className="btn sm ghost" onClick={() => setAdding(false)}>Close</button></div>
          {settings ? <ExpenseForm settings={settings} items={list?.items} onDone={added} onCancel={() => setAdding(false)} /> : <Loading />}
        </div>
      )}

      {owner && me.features?.bank_reader && (
        <Link to="/bank-statement" className="ex-bank">
          <span><b>Have a bank statement? Read it here</b><span className="small muted">Upload it and the payments come into this list in one tap. No typing.</span>
            <span className="small muted">Left out on purpose: cash withdrawals, transfers between your own accounts, money coming in, and salaries unless you pick them (managers can see this list). Payments more than 3 years old or over ₹10 crore are left out too. Check those in the statement.</span></span>
          <span aria-hidden="true">→</span>
        </Link>
      )}

      {manager && <Tabs tabs={tabs} value={tab} onChange={setTab} />}
      {(!manager || tab === "list") && (
        <List list={list} settings={settings} filters={filters} setFilters={setFilters} onChange={reload} manager={manager} month={month}
          onAdd={() => { setAdding(true); window.scrollTo({ top: 0, behavior: "smooth" }); }} />
      )}
      {manager && tab === "where" && <WhereItWent sum={sum} month={month} />}
      {manager && tab === "repeat" && <Repeating sum={sum} onChange={reload} />}
      {manager && tab === "ca" && <ForCA />}
      {manager && tab === "settings" && (settings
        ? <Settings key={JSON.stringify([settings.kind, settings.budgets, settings.custom])} settings={settings} sum={sum} onSaved={() => { reloadSettings(); reload(); }} />
        : <Loading />)}
    </Sheet>
  );
}
