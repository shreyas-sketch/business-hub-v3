import React, { useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { api, rs, upload } from "../api.js";
import { Busy, Copy, Field, FormError, Gate, Sheet, useHub, useLoad } from "../ui.jsx";
import { Empty, Loading, Pill, Tabs } from "../v2.jsx";
import "./compliance.css";

/* Compliance calendar (v2.1, 3.9). Dates come from the rules table, never from AI; the owner adds their own renewals. */

const CAT = { tax: "Tax", labour: "Staff", licence: "Licence", company: "Company", insurance: "Insurance", agreement: "Agreement", document: "Document", other: "Other" };
const REPEAT = [["none", "Once"], ["monthly", "Every month"], ["quarterly", "Every quarter"], ["yearly", "Every year"]];
const OFFSETS = [30, 7, 1, 0];
const d8 = (iso) => (iso ? new Date(`${iso}T00:00:00`) : null);
const fmt = (iso, o = { day: "numeric", month: "short", year: "numeric" }) => (iso ? d8(iso).toLocaleDateString("en-IN", o) : "");
const monthOf = (iso) => (iso ? fmt(iso, { month: "long", year: "numeric" }) : "No date yet");
const todayIso = () => new Date().toLocaleDateString("en-CA", { timeZone: "Asia/Kolkata" });

function statusPill(i) {
  if (i.status === "done") return <Pill kind="on">Done</Pill>;
  if (i.status === "needs_date") return <Pill kind="warn">Needs a date</Pill>;
  if (i.status === "overdue") return <Pill kind="warn">Overdue · {Math.abs(i.days_left)} {Math.abs(i.days_left) === 1 ? "day" : "days"}</Pill>;
  if (i.days_left === 0) return <Pill kind="warn">Due today</Pill>;
  return <Pill>{i.days_left === 1 ? "Tomorrow" : `In ${i.days_left} days`}</Pill>;
}

function byMonth(items) {
  const out = [];
  for (const i of items) {
    const m = monthOf(i.due_date);
    let g = out.find((x) => x.m === m);
    if (!g) out.push((g = { m, items: [] }));
    g.items.push(i);
  }
  return out;
}

function EditItem({ i, onSaved }) {
  const own = i.source === "owner";
  const [f, setF] = useState({ title: i.title, due_date: i.due_date || "", notes: i.notes || "", amount: i.amount ?? "", reminder_days: i.reminder_days || OFFSETS });
  const [err, setErr] = useState("");
  const set = (k) => (e) => setF({ ...f, [k]: e.target.value });
  const toggle = (n) => setF({ ...f, reminder_days: f.reminder_days.includes(n) ? f.reminder_days.filter((x) => x !== n) : [...f.reminder_days, n] });
  return (
    <div className="stack">
      {own ? (
        <div className="grid2">
          <Field label="Title"><input className="input" maxLength={120} value={f.title} onChange={set("title")} /></Field>
          <Field label="Due or expiry date"><input className="input" type="date" value={f.due_date} onChange={set("due_date")} /></Field>
        </div>
      ) : <p className="small muted">The date of a statutory item comes from the rules table, so it can't be typed here. You can add the amount and notes.</p>}
      <div className="grid2">
        <Field label="Amount (₹, optional)" hint="Shows up as money going out in Cash flow.">
          <input className="input" type="number" min="0" step="any" inputMode="decimal" value={f.amount} onChange={set("amount")} />
        </Field>
        <Field label="Remind me">
          <div className="cp-days">{OFFSETS.map((n) => (
            <label key={n} className="switch"><input type="checkbox" checked={f.reminder_days.includes(n)} onChange={() => toggle(n)} />{n === 0 ? "On the day" : `${n} ${n === 1 ? "day" : "days"} before`}</label>
          ))}</div>
        </Field>
      </div>
      <Field label="Notes"><textarea className="textarea" maxLength={1000} value={f.notes} onChange={set("notes")} placeholder="Who files it, login hints (no passwords), last year's amount" /></Field>
      <FormError msg={err} />
      <div className="row">
        <Busy className="btn sm primary" onError={setErr} run={async () => {
          const body = { notes: f.notes, reminder_days: f.reminder_days, amount: f.amount === "" ? null : Number(f.amount) };
          if (own) Object.assign(body, { title: f.title.trim(), due_date: f.due_date });
          await api(`/compliance/items/${i.id}`, { method: "PATCH", body });
          onSaved("Saved");
        }}>Save</Busy>
      </div>
    </div>
  );
}

function Item({ i, onChange }) {
  const { toast } = useHub();
  const [open, setOpen] = useState(null);   // done | doc | edit
  const [file, setFile] = useState(null);
  const own = i.source === "owner";
  const d = d8(i.due_date);
  const done = async (msg) => { setOpen(null); setFile(null); toast(msg); onChange(); };
  return (
    <div className={`cp-item ${i.status}`}>
      <div className="cp-date">
        {d ? <><b>{d.getDate()}</b><span>{d.toLocaleDateString("en-IN", { month: "short" })}</span></> : <span>No date</span>}
      </div>
      <div className="cp-body">
        <div className="cp-head">
          <h4>{i.title}</h4>
          <div className="cp-pills">{statusPill(i)}<Pill>{CAT[i.category] || i.category}</Pill>{own && <Pill>Yours</Pill>}</div>
        </div>
        {i.period && <span className="small muted">For {i.period}</span>}
        {i.explain ? <p className="small cp-explain">{i.explain}</p> : i.plain ? <p className="small">{i.plain}</p> : null}
        {i.penalty_note && i.status !== "done" && <p className="small muted">If it's late: {i.penalty_note}</p>}
        {i.amount ? <span className="small">Amount: <span className="money">{rs(i.amount)}</span></span> : null}
        {i.notes && <p className="small muted">{i.notes}</p>}
        <span className="small muted">
          {i.authority}{i.source_url && <> · <a href={i.source_url} target="_blank" rel="noreferrer">Official site</a></>}
          {i.recurrence && i.recurrence !== "none" && own ? ` · ${REPEAT.find((r) => r[0] === i.recurrence)?.[1]}` : ""}
        </span>
        {i.doc_file_id && <span className="small">Document: <a href={`/api/compliance-documents/${i.doc_file_id}`} target="_blank" rel="noreferrer">{i.doc_name || "Open"}</a></span>}
        {i.receipt_file_id && <span className="small">Receipt: <a href={`/api/compliance-documents/${i.receipt_file_id}`} target="_blank" rel="noreferrer">Open</a></span>}
      </div>
      <div className="cp-actions">
        {i.status === "done"
          ? <Busy className="btn sm ghost" run={async () => { await api(`/compliance/items/${i.id}/undo`, { method: "POST" }); done("Marked not done"); }}>Undo</Busy>
          : <button className={`btn sm ${open === "done" ? "primary" : ""}`} onClick={() => setOpen(open === "done" ? null : "done")}>Mark done</button>}
        <button className="btn sm ghost" onClick={() => setOpen(open === "doc" ? null : "doc")}>{i.doc_file_id ? "Replace document" : "Attach document"}</button>
        <button className="btn sm ghost" onClick={() => setOpen(open === "edit" ? null : "edit")}>Edit</button>
        {own && <Busy className="btn sm ghost" run={async () => {
          if (!window.confirm(`Remove “${i.title}”?`)) return;
          await api(`/compliance/items/${i.id}`, { method: "DELETE" });
          done("Removed");
        }}>Delete</Busy>}
      </div>
      {open === "done" && (
        <div className="cp-open">
          <Field label="Acknowledgement or receipt (optional)" hint="PDF, JPG or PNG, up to 10 MB. Kept in your documents.">
            <input className="cp-file" type="file" accept="application/pdf,image/png,image/jpeg" onChange={(e) => setFile(e.target.files?.[0] || null)} />
          </Field>
          <div className="row">
            <Busy className="btn sm primary" run={async () => {
              let r;
              if (file) {
                const form = new FormData();
                form.append("receipt", file);
                r = await upload(`/compliance/items/${i.id}/done`, form);
              } else r = await api(`/compliance/items/${i.id}/done`, { method: "POST" });
              done(r.next ? `Done. Next one added for ${fmt(r.next.due_date)}.` : "Marked done");
            }}>Confirm done</Busy>
            {own && i.recurrence !== "none" && <span className="small muted">The next one is added for you.</span>}
          </div>
        </div>
      )}
      {open === "doc" && (
        <div className="cp-open">
          <Field label="The document" hint="The licence, certificate or agreement itself. PDF, JPG or PNG, up to 10 MB.">
            <input className="cp-file" type="file" accept="application/pdf,image/png,image/jpeg" onChange={(e) => setFile(e.target.files?.[0] || null)} />
          </Field>
          <div><Busy className="btn sm primary" disabled={!file} run={async () => {
            const form = new FormData();
            form.append("file", file);
            await upload(`/compliance/items/${i.id}/document`, form);
            done("Document saved");
          }}>Upload</Busy></div>
        </div>
      )}
      {open === "edit" && <div className="cp-open"><EditItem i={i} onSaved={done} /></div>}
    </div>
  );
}

function Months({ items, onChange, empty }) {
  if (!items.length) return <Empty>{empty}</Empty>;
  return byMonth(items).map((g) => (
    <div key={g.m} className="cp-month">
      <h3>{g.m}</h3>
      {g.items.map((i) => <Item key={i.id} i={i} onChange={onChange} />)}
    </div>
  ));
}

const blank = () => ({ title: "", category: "licence", due_date: "", recurrence: "yearly", amount: "", notes: "", rule_key: null });

function AddItem({ renewals, categories, onAdded }) {
  const { toast } = useHub();
  const [f, setF] = useState(blank);
  const [err, setErr] = useState("");
  const set = (k) => (e) => setF({ ...f, [k]: e.target.value });
  const ready = f.title.trim().length >= 2 && f.due_date;
  return (
    <div className="panel active">
      <span className="label">Add a renewal or expiry</span>
      <p className="small muted">Licences, insurance, the rent agreement, AMCs, PUC — anything with a date. You get reminders 30, 7 and 1 day before, and on the day.</p>
      {renewals?.length > 0 && (
        <Field label="Common renewals">
          <select className="select" value={f.rule_key || ""} onChange={(e) => {
            const r = renewals.find((x) => x.key === e.target.value);
            setF(r ? { ...f, rule_key: r.key, title: r.title, category: "licence" } : { ...f, rule_key: null });
          }}>
            <option value="">Something else (type it below)</option>
            {renewals.map((r) => <option key={r.key} value={r.key}>{r.title}</option>)}
          </select>
        </Field>
      )}
      <div className="grid3">
        <Field label="What is it"><input className="input" maxLength={120} value={f.title} onChange={set("title")} placeholder="Shop and establishment licence" /></Field>
        <Field label="Due or expiry date"><input className="input" type="date" min={todayIso()} value={f.due_date} onChange={set("due_date")} /></Field>
        <Field label="Repeats">
          <select className="select" value={f.recurrence} onChange={set("recurrence")}>{REPEAT.map(([k, l]) => <option key={k} value={k}>{l}</option>)}</select>
        </Field>
        <Field label="Kind">
          <select className="select" value={f.category} onChange={set("category")}>{(categories || Object.keys(CAT)).map((c) => <option key={c} value={c}>{CAT[c] || c}</option>)}</select>
        </Field>
        <Field label="Amount (₹, optional)" hint="Shows in Cash flow on that date.">
          <input className="input" type="number" min="0" step="any" inputMode="decimal" value={f.amount} onChange={set("amount")} />
        </Field>
        <Field label="Notes (optional)"><input className="input" maxLength={1000} value={f.notes} onChange={set("notes")} placeholder="Licence no., who renews it" /></Field>
      </div>
      <FormError msg={err} />
      <div>
        <Busy className="btn primary" disabled={!ready} onError={setErr} run={async () => {
          await api("/compliance/items", { method: "POST", body: { ...f, title: f.title.trim(), amount: f.amount === "" ? null : Number(f.amount) } });
          setF(blank());
          toast("Added to your calendar");
          onAdded();
        }}>Add</Busy>
      </div>
    </div>
  );
}

function Documents() {
  const [q, setQ] = useState("");
  const [term, setTerm] = useState("");
  useEffect(() => { const t = setTimeout(() => setTerm(q.trim()), 300); return () => clearTimeout(t); }, [q]);
  const [docs, , err] = useLoad(`/compliance-documents?q=${encodeURIComponent(term)}`);
  const [shared, setShared] = useState({});
  return (
    <div className="stack cp-docs">
      <Field label="Search your documents"><input className="input" value={q} onChange={(e) => setQ(e.target.value)} placeholder="FSSAI, insurance, rent…" /></Field>
      {err ? <FormError msg={err.message} /> : !docs ? <Loading /> : !docs.length ? (
        <Empty>{term ? "Nothing matches that search." : "No documents yet. Attach a licence, certificate or agreement to any item in your calendar."}</Empty>
      ) : docs.map((i) => (
        <div key={i.id} className="cp-doc">
          <div>
            <b>{i.title}</b>
            <div className="small muted">{i.doc_name}{i.due_date ? ` · ${i.status === "done" ? "done" : "due or expires"} ${fmt(i.due_date)}` : ""}</div>
          </div>
          <div className="row">
            <a className="btn sm ghost" href={`/api/compliance-documents/${i.doc_file_id}`} target="_blank" rel="noreferrer">Open</a>
            <Busy className="btn sm" run={async () => {
              const r = await api(`/compliance-documents/${i.doc_file_id}/share`, { method: "POST" });
              setShared({ ...shared, [i.doc_file_id]: r });
            }}>Share link</Busy>
          </div>
          {shared[i.doc_file_id] && (
            <div className="cp-share">
              <input className="input mono" readOnly value={shared[i.doc_file_id].url} />
              <Copy text={shared[i.doc_file_id].url} />
              <span className="small muted">Anyone with this link can open it for {shared[i.doc_file_id].expires_in_days} days.</span>
            </div>
          )}
        </div>
      ))}
    </div>
  );
}

function ComplianceInner() {
  const { toast } = useHub();
  const [d, reload, err] = useLoad("/compliance?days=90");
  const [tab, setTab] = useState("soon");
  const year = useMemo(() => (d?.all || []).filter((i) => i.status !== "done" || (i.due_date || "") >= todayIso()), [d]);
  const head = { kicker: "Systems · Compliance calendar", id: "SY-09", pillar: "Systems" };
  if (!d) return <Sheet {...head} title={<>Every filing, <em>on time</em>.</>}>{err ? <FormError msg={err.message} /> : <Loading />}</Sheet>;
  const week = d.items.filter((i) => i.status === "upcoming" && i.days_left <= 7).length;
  const title = d.overdue.length ? <>{d.overdue.length} {d.overdue.length === 1 ? "item is" : "items are"} <em className="money">overdue</em>.</>
    : week ? <>{week} due <em>this week</em>.</> : <>Every filing, <em>on time</em>.</>;
  const explained = (d.all || []).some((i) => i.explain);
  return (
    <Sheet {...head} title={title}
      sub="Your GST, TDS, staff and company filings from your business details, plus your own licences and renewals — with reminders to you before each one.">
      <div className={`cp-review ${d.review.reviewed ? "" : "warn"}`}>
        <i className="mark-dot" aria-hidden="true" />
        <div className="stack" style={{ gap: 4 }}>
          <b>{d.review.line}</b>
          <span className="small muted">{d.label} Reminders go to you only — never to anyone else.</span>
        </div>
      </div>

      {!d.identity && (
        <div className="panel active">
          <span className="label">One step first</span>
          <p>Add your business identity — whether you're a proprietor or a company, GST, state and staff — so the hub knows which filings apply to you.</p>
          <div><Link className="btn primary" to="/knowledge">Fill it in the Knowledge Hub</Link></div>
        </div>
      )}

      {(d.overdue.length > 0 || d.expiring_docs.length > 0 || d.needs_date.length > 0) && (
        <div className="statgrid">
          {d.overdue.length > 0 && <div className="stat"><span className="label">Overdue</span><span className="num money">{d.overdue.length}</span></div>}
          {d.expiring_docs.length > 0 && <div className="stat"><span className="label">Documents expiring in 30 days</span><span className="num">{d.expiring_docs.length}</span></div>}
          {d.needs_date.length > 0 && <div className="stat"><span className="label">Need a date</span><span className="num">{d.needs_date.length}</span></div>}
        </div>
      )}

      {d.identity && (
        <div className="row between">
          <span className="small muted">{explained ? "Explanations written for your business." : "Want each item explained for your business in plain words?"}</span>
          <Busy className="btn sm" run={async () => { const r = await api("/compliance/explain", { method: "POST" }); toast(`${r.explained} explained`); reload(); }}>
            {explained ? "Explain again" : "Explain for my business"}
          </Busy>
        </div>
      )}
      {d.identity && <p className="hint" style={{ marginTop: -18 }}>Uses 1 AI run. The AI only writes the explanations — it never sets or changes a date.</p>}

      <Tabs tabs={[["soon", "Next 90 days"], ["year", "Next 12 months"], ["docs", "Documents"], ["add", "Add your own"]]} value={tab} onChange={setTab} />
      {tab === "soon" && (
        <div className="stack" style={{ gap: 24 }}>
          {d.needs_date.length > 0 && <Months items={d.needs_date} onChange={reload} empty="" />}
          <Months items={d.items} onChange={reload}
            empty={d.identity ? "Nothing due in the next 90 days." : "Nothing here yet. Fill your business identity, or add your own renewals."} />
        </div>
      )}
      {tab === "year" && <div className="stack" style={{ gap: 24 }}><Months items={year} onChange={reload} empty="Nothing in your calendar yet." /></div>}
      {tab === "docs" && <Documents />}
      {tab === "add" && <AddItem renewals={d.renewals} categories={d.categories} onAdded={() => { reload(); setTab("year"); }} />}
    </Sheet>
  );
}

export default function Compliance() {
  return (
    <Gate feature="compliance_calendar" role="manager" kicker="Systems · Compliance calendar" id="SY-09"
      what="Every filing, licence, renewal and document expiry in one calendar, with reminders before each one.">
      <ComplianceInner />
    </Gate>
  );
}
