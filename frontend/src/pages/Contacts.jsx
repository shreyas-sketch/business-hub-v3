import React, { useEffect, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { ago, api, localPhone, rs, upload, waLink } from "../api.js";
import { Busy, Field, FormError, Modal, Sheet, Stat, canAct, useHub, useLoad } from "../ui.jsx";
import { Loading, Pill, Tabs } from "../v2.jsx";
import "./crm.css";

/* v2.3 CRM — every person and company the business deals with: their deals, calls, notes and follow-up dates.
   Contacts fill in by themselves from every inquiry; add, import (CSV or Excel) and export here. Nothing here messages a
   customer: WhatsApp, call and email buttons open the owner's own apps. */

const shortDay = (d) => (d ? new Date(`${d}T00:00:00`).toLocaleDateString("en-IN", { day: "numeric", month: "short" }) : "");
const dueClass = (day, today) => (!day ? "" : day < today ? "crm-due over" : day === today ? "crm-due today" : "crm-due");
const head = { kicker: "Systems · CRM", id: "CV-02", pillar: "Conversion" };

function AddContact({ onClose, onAdded }) {
  const [f, setF] = useState({ name: "", phone: "", email: "", company: "", tags: "", notes: "", source: "manual" });
  const [err, setErr] = useState("");
  const set = (k) => (e) => setF({ ...f, [k]: e.target.value });
  return (
    <Modal onClose={onClose}>
      <span className="kicker">CRM</span>
      <h2>Add a <em>contact</em>.</h2>
      <div className="stack">
        <Field label="Name"><input className="input" value={f.name} maxLength={80} onChange={set("name")} autoFocus /></Field>
        <div className="crm-two">
          <Field label="Mobile"><input className="input" inputMode="tel" value={f.phone} maxLength={24} onChange={set("phone")} placeholder="98200 00000" /></Field>
          <Field label="Email"><input className="input" type="email" value={f.email} maxLength={254} onChange={set("email")} /></Field>
        </div>
        <Field label="Company (optional)"><input className="input" value={f.company} maxLength={120} onChange={set("company")} /></Field>
        <Field label="Tags (optional)" hint="Separate with commas: Architect, Repeat customer"><input className="input" value={f.tags} onChange={set("tags")} /></Field>
        <Field label="Notes (optional)"><textarea className="textarea" rows={2} value={f.notes} maxLength={2000} onChange={set("notes")} /></Field>
        <FormError msg={err} />
        <div className="row">
          <Busy className="btn primary" onError={setErr} disabled={f.name.trim().length < 2}
            run={async () => { const c = await api("/crm/contacts", { method: "POST", body: { ...f, tags: f.tags.split(",").map((x) => x.trim()).filter(Boolean) } }); onAdded(c); }}>Add contact</Busy>
          <button className="btn ghost" onClick={onClose}>Cancel</button>
        </div>
      </div>
    </Modal>
  );
}

function ImportBox({ onDone }) {
  const { toast } = useHub();
  const [err, setErr] = useState("");
  const [busy, setBusy] = useState(false);
  const pick = async (e) => {
    const file = e.target.files?.[0];
    e.target.value = "";
    if (!file) return;
    setBusy(true); setErr("");
    try {
      const fd = new FormData();
      fd.append("file", file);
      const r = await upload("/crm/import", fd);
      toast(`${r.added} added · ${r.matched} already here (filled in) · ${r.skipped} skipped`);
      onDone();
    } catch (x) { if (!x.handled) setErr(x.message); }
    finally { setBusy(false); }
  };
  return (
    <div className="panel stack">
      <span className="label">Import a list</span>
      <span className="small muted">An Excel (.xlsx) or CSV file with a name column and a mobile or email column in the first row. Company, tags, source and notes are read when there.
        People already in your CRM (same number or email) are matched, not doubled.</span>
      <label className={`btn sm ${busy ? "is-busy" : ""}`} style={{ width: "fit-content" }}>
        {busy ? "Reading…" : "Choose a file"}<input type="file" accept=".xlsx,.csv" hidden onChange={pick} disabled={busy} />
      </label>
      <FormError msg={err} />
    </div>
  );
}

function ListPage() {
  const { me } = useHub();
  const nav = useNavigate();
  const [tab, setTab] = useState("contacts");
  const [q, setQ] = useState("");
  const [source, setSource] = useState("");
  const [tag, setTag] = useState("");
  const [company, setCompany] = useState(null);
  const [due, setDue] = useState(false);
  const [adding, setAdding] = useState(false);
  const [importing, setImporting] = useState(false);
  const qs = new URLSearchParams({ ...(q.trim() ? { q: q.trim() } : {}), ...(source ? { source } : {}), ...(tag ? { tag } : {}),
    ...(company ? { company: company.id } : {}), ...(due ? { due: 1 } : {}) }).toString();
  const [data, reload] = useLoad(`/crm/contacts${qs ? `?${qs}` : ""}`);
  const [companies, reloadCompanies] = useLoad("/crm/companies");
  const [fu, reloadFu] = useLoad("/crm/followups");
  if (!data) return <Sheet {...head} title={<>Everyone you deal with, in one <em>place</em>.</>}><Loading /></Sheet>;
  const today = data.today;
  const manager = canAct(me, "manager");
  const dueCount = fu ? fu.overdue.length + fu.today.length : 0;
  const title = data.total ? <>{data.total} {data.total === 1 ? "contact" : "contacts"}, every deal and call <em>remembered</em>.</>
    : <>Everyone you deal with, in one <em>place</em>.</>;
  const FuRow = ({ i }) => (
    <button className="crm-row" onClick={() => nav(i.kind === "deal" ? `/pipeline?deal=${i.id}` : `/contacts/${i.contact_id}`)}>
      <span className="who"><b>{i.name || "—"}</b><span className="small muted">{i.note || (i.kind === "deal" ? "Deal" : "Contact")}</span></span>
      <span className="small hide-sm">{i.kind === "deal" ? "Deal" : "Contact"}</span>
      <span className={`small hide-sm ${dueClass(i.on, today)}`}>{i.on === today ? "Today" : shortDay(i.on)}</span>
      <span className={i.value ? "money" : "small muted"}>{i.value ? rs(i.value) : i.phone ? localPhone(i.phone) : ""}</span>
    </button>
  );
  return (
    <Sheet {...head} title={title}
      sub="Every inquiry adds its person here by itself. Open a contact for their deals, calls, notes and follow-up date — and the reminder comes to your hub on the day.">
      <div className="statgrid">
        <Stat label="Contacts" value={data.total} />
        <Stat label="Companies" value={companies?.items?.length ?? "—"} />
        <Stat label="Follow-ups due" value={dueCount} note={dueCount ? "Today or earlier" : "None due"} />
      </div>
      <div className="crm-bar">
        <Tabs value={tab} onChange={setTab} tabs={[["contacts", "Contacts"], ["companies", "Companies"], ["followups", `Follow-ups${dueCount ? ` · ${dueCount}` : ""}`]]} />
        <button className="btn sm primary" onClick={() => setAdding(true)}>+ Add contact</button>
        {manager && <button className="btn sm ghost" onClick={() => setImporting((x) => !x)}>Import</button>}
        {manager && <a className="btn sm ghost" href="/api/crm/export?what=contacts&fmt=xlsx">Export contacts</a>}
        {manager && <a className="btn sm ghost" href="/api/crm/export?what=deals&fmt=xlsx">Export deals</a>}
      </div>
      {importing && <ImportBox onDone={() => { setImporting(false); reload(); reloadCompanies(); }} />}

      {tab === "contacts" && <>
        <div className="crm-bar">
          <input className="input grow" type="search" value={q} placeholder="Search name, number, email, company, tag…" aria-label="Search contacts" onChange={(e) => setQ(e.target.value)} />
          <select className="select" value={source} aria-label="Source" onChange={(e) => setSource(e.target.value)}>
            <option value="">Every source</option>{(data.sources || []).map(([k, l]) => <option key={k} value={k}>{l}</option>)}
            <option value="guide">Free guide</option><option value="lead_magnet">Lead magnet</option><option value="import">Imported</option><option value="manual">Added by hand</option>
          </select>
          <button type="button" className={`crm-chip ${due ? "on" : ""}`} aria-pressed={due} onClick={() => setDue(!due)}>Follow-up due</button>
        </div>
        {(data.tags.length > 0 || company) && (
          <div className="crm-chips">
            {company && <span className="crm-chip on x">{company.name}<button type="button" aria-label="Clear company" onClick={() => setCompany(null)}>×</button></span>}
            {data.tags.map((t) => <button type="button" key={t} className={`crm-chip ${tag === t ? "on" : ""}`} onClick={() => setTag(tag === t ? "" : t)}>{t}</button>)}
          </div>
        )}
        {data.items.length ? (
          <div className="crm-rows">
            {data.items.map((c) => (
              <button key={c.id} className="crm-row" onClick={() => nav(`/contacts/${c.id}`)}>
                <span className="who"><b>{c.name}</b>
                  <span className="small muted">{[c.company, c.phone && localPhone(c.phone), !c.phone && c.email].filter(Boolean).join(" · ")}</span>
                  {c.tags.length > 0 && <span className="crm-tags">{c.tags.map((t) => <span key={t} className="crm-tag">{t}</span>)}</span>}
                </span>
                <span className="small hide-sm">{c.open_deals ? `${c.open_deals} open ${c.open_deals === 1 ? "deal" : "deals"}` : c.deals ? `${c.deals} ${c.deals === 1 ? "deal" : "deals"}` : c.source_label}</span>
                <span className={`small hide-sm ${dueClass(c.follow_up_on, today)}`}>{c.follow_up_on ? `Follow up ${c.follow_up_on === today ? "today" : shortDay(c.follow_up_on)}` : ago(c.last_activity_at)}</span>
                <span className={c.open_value ? "money" : "small muted"}>{c.open_value ? rs(c.open_value) : c.won_value ? <span className="money">{rs(c.won_value)} won</span> : ""}</span>
              </button>
            ))}
          </div>
        ) : (
          <div className="empty"><h2>{data.total ? "No one matches." : "No contacts yet."}</h2>
            <p className="muted">{data.total ? "Clear the search or the filters." : "The next inquiry from your website, lead magnet, email or a pasted WhatsApp adds its person here. Or add someone, or import your old list."}</p></div>
        )}
      </>}

      {tab === "companies" && (companies?.items?.length ? (
        <div className="crm-rows">
          {companies.items.map((co) => (
            <button key={co.id} className="crm-row" onClick={() => { setCompany({ id: co.id, name: co.name }); setTab("contacts"); }}>
              <span className="who"><b>{co.name}</b><span className="small muted">{co.contacts} {co.contacts === 1 ? "contact" : "contacts"}</span></span>
              <span className="small hide-sm">{co.open_deals} open {co.open_deals === 1 ? "deal" : "deals"}</span>
              <span className="small hide-sm">{co.won_value ? <span className="money">{rs(co.won_value)} won</span> : ""}</span>
              <span className={co.open_value ? "money" : "small muted"}>{co.open_value ? rs(co.open_value) : ""}</span>
            </button>
          ))}
        </div>
      ) : <div className="empty"><p className="muted">Add a company name to a contact and their company shows here, with everyone you know there.</p></div>)}

      {tab === "followups" && (fu ? (
        <div className="stack">
          {[["overdue", "Overdue"], ["today", "Today"], ["upcoming", "Next 7 days"]].map(([k, l]) => (
            <div key={k} className="stack">
              <span className="label">{l} · {fu[k].length}</span>
              {fu[k].length ? <div className="crm-rows">{fu[k].map((i) => <FuRow key={`${i.kind}-${i.id}`} i={i} />)}</div> : <span className="small muted">Nothing.</span>}
            </div>
          ))}
          <button className="btn sm ghost" style={{ width: "fit-content" }} onClick={reloadFu}>Refresh</button>
        </div>
      ) : <Loading />)}

      {adding && <AddContact onClose={() => setAdding(false)} onAdded={(c) => { setAdding(false); nav(`/contacts/${c.id}`); }} />}
    </Sheet>
  );
}

function ContactPage({ id }) {
  const { me, toast } = useHub();
  const nav = useNavigate();
  const [c, setC] = useState(null);
  const [f, setF] = useState(null);
  const [err, setErr] = useState("");
  const [kind, setKind] = useState("call");
  const [text, setText] = useState("");
  const [newDeal, setNewDeal] = useState(false);
  const [dealValue, setDealValue] = useState("");
  const fill = (x) => {
    setC(x);
    setF({ name: x.name, phone: x.phone ? localPhone(x.phone) : "", email: x.email || "", company: x.company || "", tags: (x.tags || []).join(", "),
      notes: x.notes || "", follow_up_on: x.follow_up_on || "", follow_up_note: x.follow_up_note || "", assignee_id: x.assignee_id || "" });
  };
  useEffect(() => { api(`/crm/contacts/${id}`).then(fill).catch((e) => { if (!e.handled) toast(e.message, "err"); nav("/contacts"); }); }, [id]);  // eslint-disable-line react-hooks/exhaustive-deps
  if (!c || !f) return <Sheet {...head} title={<>Contact</>}><Loading /></Sheet>;
  const today = new Date(Date.now() + 5.5 * 3600e3).toISOString().slice(0, 10);
  const dirty = f.name !== c.name || f.phone !== (c.phone ? localPhone(c.phone) : "") || f.email !== (c.email || "") || f.company !== (c.company || "")
    || f.tags !== (c.tags || []).join(", ") || f.notes !== (c.notes || "") || f.follow_up_on !== (c.follow_up_on || "")
    || f.follow_up_note !== (c.follow_up_note || "") || (f.assignee_id || "") !== (c.assignee_id || "");
  const save = async () => {
    const x = await api(`/crm/contacts/${id}`, { method: "PATCH", body: { ...f, tags: f.tags.split(",").map((t) => t.trim()).filter(Boolean), assignee_id: f.assignee_id || null } });
    fill(x);
    toast("Saved");
  };
  const set = (k) => (e) => setF({ ...f, [k]: e.target.value });
  const team = c.team || [];
  return (
    <Sheet {...head} title={<>{c.name}</>} sub={[c.company, c.source_label && `Came from ${c.source_label}`, c.created_at && `added ${ago(c.created_at)}`].filter(Boolean).join(" · ")}
      actions={<Link className="btn sm ghost" to="/contacts">← All contacts</Link>}>
      <div className="crm-quick">
        {c.phone && <a className="btn sm" href={waLink("", c.phone)} target="_blank" rel="noopener">WhatsApp {localPhone(c.phone)}</a>}
        {c.phone && <a className="btn sm ghost" href={`tel:${c.phone}`}>Call</a>}
        {c.email && <a className="btn sm ghost" href={`mailto:${c.email}`}>Email</a>}
        {me.features?.pipeline && <button className="btn sm ghost" onClick={() => setNewDeal((x) => !x)}>+ New opportunity</button>}
      </div>
      {newDeal && (
        <div className="panel row">
          <span className="small">A new deal for {c.name} in the first stage of your pipeline.</span>
          <input className="input money" style={{ width: 160 }} type="number" min="0" inputMode="decimal" placeholder="Value ₹ (optional)" value={dealValue}
            onChange={(e) => setDealValue(e.target.value)} />
          <Busy className="btn sm primary" run={async () => {
            const d = await api("/crm/deals", { method: "POST", body: { name: c.name, phone: c.phone || "", email: c.email || "", company: c.company || "",
              value: dealValue === "" ? null : Number(dealValue), source: "manual" } });
            toast("Opportunity added");
            nav(`/pipeline?deal=${d.id}`);
          }}>Add it</Busy>
        </div>
      )}
      <div className="statgrid">
        <Stat label="Open deals" value={c.open_deals} note={c.open_value ? rs(c.open_value) : undefined} />
        <Stat label="Won" value={rs(c.won_value)} money />
        {c.customer && <Stat label="Customer since" value={c.customer.last_purchase ? shortDay(c.customer.last_purchase) : "—"} note={`${c.customer.purchases} purchases · tier ${c.customer.tier || "—"}`} />}
      </div>

      <div className="crm-side">
        <div className="stack">
          <div className="panel stack">
            <span className="label">Details</span>
            <div className="crm-two">
              <Field label="Name"><input className="input" value={f.name} maxLength={80} onChange={set("name")} /></Field>
              <Field label="Company"><input className="input" value={f.company} maxLength={120} onChange={set("company")} /></Field>
            </div>
            <div className="crm-two">
              <Field label="Mobile"><input className="input" inputMode="tel" value={f.phone} maxLength={24} onChange={set("phone")} /></Field>
              <Field label="Email"><input className="input" type="email" value={f.email} maxLength={254} onChange={set("email")} /></Field>
            </div>
            <Field label="Tags" hint="Separate with commas"><input className="input" value={f.tags} onChange={set("tags")} /></Field>
            <Field label="Notes"><textarea className="textarea" rows={3} value={f.notes} maxLength={2000} onChange={set("notes")} /></Field>
            <div className="crm-two">
              <Field label="Follow up on"><input className="input" type="date" value={f.follow_up_on} onChange={set("follow_up_on")} /></Field>
              {team.length > 1 && <Field label="Handled by"><select className="select" value={f.assignee_id} onChange={set("assignee_id")}>
                <option value="">Not set</option>{team.map((t) => <option key={t.id} value={t.id}>{t.name}</option>)}</select></Field>}
            </div>
            {f.follow_up_on && <Field label="What to follow up about" hint="A reminder comes to your hub at 10am that day.">
              <input className="input" value={f.follow_up_note} maxLength={300} onChange={set("follow_up_note")} /></Field>}
            <FormError msg={err} />
            {dirty && <Busy className="btn primary" onError={setErr} run={save}>Save changes</Busy>}
          </div>

          <div className="panel stack">
            <span className="label">Deals</span>
            {(c.deals_list || []).length ? (
              <div className="crm-rows">
                {c.deals_list.map((d) => (
                  <Link key={d.id} className="crm-row" to={`/pipeline?deal=${d.id}`} style={{ textDecoration: "none" }}>
                    <span className="who"><b>{d.name}</b><span className="small muted">{c.stage_names?.[d.pstage] || "—"} · {ago(d.created_at)}</span></span>
                    <span className="small hide-sm">{d.status === "won" ? <Pill kind="money">Won</Pill> : d.status === "lost" ? <Pill kind="off">Lost{d.lost_reason ? `: ${d.lost_reason}` : ""}</Pill> : <Pill kind="on">Open</Pill>}</span>
                    <span className={`small hide-sm ${dueClass(d.follow_up_on, today)}`}>{d.follow_up_on ? `Follow up ${shortDay(d.follow_up_on)}` : d.source_label}</span>
                    <span className={d.value ? "money" : "small muted"}>{d.value ? rs(d.value) : "No value"}</span>
                  </Link>
                ))}
              </div>
            ) : <span className="small muted">No deals yet.</span>}
          </div>

          {(c.documents || []).length > 0 && (
            <div className="panel stack">
              <span className="label">Quotations and invoices</span>
              {c.documents.map((q) => (
                <Link key={q.id} to="/quotes" className="row between small" style={{ textDecoration: "none" }}>
                  <span>{q.kind === "invoice" ? "Invoice" : q.kind === "quote" ? "Quotation" : "Credit note"} {q.number} · {q.status}</span>
                  {q.total != null && <span className="money">{rs(q.total)}</span>}
                </Link>
              ))}
            </div>
          )}
        </div>

        <div className="panel stack">
          <span className="label">History</span>
          <div className="crm-seg" role="group" aria-label="What happened">
            {[["call", "Call"], ["meeting", "Meeting"], ["note", "Note"]].map(([k, l]) => <button type="button" key={k} className={kind === k ? "on" : ""} onClick={() => setKind(k)}>{l}</button>)}
          </div>
          <textarea className="textarea" rows={2} value={text} maxLength={2000} onChange={(e) => setText(e.target.value)}
            placeholder={kind === "call" ? "What did you talk about?" : kind === "meeting" ? "What was decided?" : "A note for later"} />
          <div><Busy className="btn sm" disabled={text.trim().length < 2} run={async () => {
            await api(`/crm/contacts/${id}/activities`, { method: "POST", body: { kind, text: text.trim() } });
            setText("");
            fill(await api(`/crm/contacts/${id}`));
            toast("Saved to the history");
          }}>Save</Busy></div>
          <div className="crm-timeline">
            {(c.activities || []).map((a) => (
              <div key={a.id} className={`crm-ev ${a.kind}`}><i />
                <div><p className="small">{a.text}</p><span className="small muted">{ago(a.at)}{a.by_name ? ` · ${a.by_name}` : ""}</span></div>
              </div>
            ))}
            {!(c.activities || []).length && <span className="small muted">Nothing yet.</span>}
          </div>
          {canAct(me, "manager") && (
            <Busy className="btn sm ghost crm-fit" run={async () => {
              if (!window.confirm(`Remove ${c.name} from the CRM? Their deals stay in the pipeline.`)) return;
              await api(`/crm/contacts/${id}`, { method: "DELETE" });
              toast("Contact removed");
              nav("/contacts");
            }}>Remove contact</Busy>
          )}
        </div>
      </div>
    </Sheet>
  );
}

export default function Contacts() {
  const { id } = useParams();
  return id ? <ContactPage key={id} id={id} /> : <ListPage />;
}
