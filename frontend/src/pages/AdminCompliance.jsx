import React, { useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../api.js";
import { Busy, Field, FormError, LoadError, Sheet, Switch, useHub, useLoad } from "../ui.jsx";
import { Empty, Loading, Pill } from "../v2.jsx";
import "./compliance.css";

/* Admin → Compliance rules: the table every owner's compliance dates come from. Edit or add a rule, move one due date when
   the government extends it, and record the CA who reviewed the table (owners see the stamp). */

const CATS = ["tax", "labour", "licence", "company", "insurance", "agreement", "document", "other"];
const pretty = (o) => JSON.stringify(o || {}, null, 2);

function dueLine(due) {
  if (!due) return "";
  const t = due.type;
  if (t === "monthly") return `Day ${due.day} of ${due.offset ? `the month after` : "the same month"}${due.exceptions ? " (with exceptions)" : ""}`;
  if (t === "month_end") return "Last day of the month";
  if (t === "quarterly") return `Day ${due.day}, the month after each quarter`;
  if (t === "qrmp_payment") return `Day ${due.day}, after months 1 and 2 of each quarter`;
  if (t === "dates") return `Every year on ${(due.dates || []).join(", ")}${due.for === "previous_fy" ? " (for last financial year)" : ""}`;
  if (t === "owner_date") return "A renewal: the owner enters the date";
  return t;
}

function appliesLine(a) {
  const parts = [];
  if (!a || !Object.keys(a).length) return "Every business";
  if (a.entity) parts.push(`Entity: ${a.entity.join(", ")}`);
  if (a.gst !== undefined) parts.push(a.gst ? "GST registered" : "Not GST registered");
  if (a.scheme) parts.push(`GST scheme: ${a.scheme}`);
  if (a.staff_min) parts.push(`${a.staff_min}+ staff`);
  if (a.states) parts.push(`States: ${a.states.length > 3 ? `${a.states.slice(0, 3).join(", ")} +${a.states.length - 3}` : a.states.join(", ")}`);
  if (a.states_not) parts.push(`Not in ${a.states_not.length} states`);
  if (a.registration) parts.push(`Registration: ${a.registration}`);
  if (a.or_registration) parts.push(`or registered for ${a.or_registration}`);
  return parts.join(" · ");
}

function RuleEditor({ rule, dueTypes, onSaved, onCancel }) {
  const isNew = !rule;
  const [f, setF] = useState({
    key: rule?.key || "", title: rule?.title || "", category: rule?.category || "tax", authority: rule?.authority || "",
    plain: rule?.plain || "", penalty_note: rule?.penalty_note || "", source_url: rule?.source_url || "", on: rule?.on ?? true,
    applies: pretty(rule?.applies), due: pretty(rule?.due || { type: "monthly", day: 20, offset: 1 }),
  });
  const [err, setErr] = useState("");
  const set = (k) => (e) => setF({ ...f, [k]: e.target.value });
  return (
    <div className="stack">
      <div className="grid3">
        {isNew && <Field label="Key" hint="a–z, 0–9 and _, e.g. pt_rj"><input className="input mono" maxLength={40} value={f.key} onChange={set("key")} /></Field>}
        <Field label="Title"><input className="input" maxLength={120} value={f.title} onChange={set("title")} /></Field>
        <Field label="Category"><select className="select" value={f.category} onChange={set("category")}>{CATS.map((c) => <option key={c}>{c}</option>)}</select></Field>
        <Field label="Authority"><input className="input" maxLength={120} value={f.authority} onChange={set("authority")} placeholder="GST" /></Field>
        <Field label="Official site"><input className="input" maxLength={300} value={f.source_url} onChange={set("source_url")} placeholder="https://" /></Field>
      </div>
      <Field label="What it means, in plain words"><textarea className="textarea" maxLength={500} value={f.plain} onChange={set("plain")} /></Field>
      <Field label="If it's late"><input className="input" maxLength={300} value={f.penalty_note} onChange={set("penalty_note")} /></Field>
      <div className="grid2">
        <Field label="Applies when (JSON)" hint="Keys: entity, gst, scheme, staff_min, states, states_not, registration, or_registration. {} = every business.">
          <textarea className="textarea mono" rows={6} value={f.applies} onChange={set("applies")} />
        </Field>
        <Field label="Due date rule (JSON)" hint={`type is one of: ${dueTypes.join(", ")}. E.g. {"type":"monthly","day":20,"offset":1}`}>
          <textarea className="textarea mono" rows={6} value={f.due} onChange={set("due")} />
        </Field>
      </div>
      <Switch checked={f.on} onChange={(v) => setF({ ...f, on: v })} label={f.on ? "On for every owner it applies to" : "Switched off"} />
      <FormError msg={err} />
      <div className="row">
        <Busy className="btn sm primary" onError={setErr} run={async () => {
          let applies, due;
          try { applies = JSON.parse(f.applies || "{}"); } catch { throw new Error("“Applies when” isn't valid JSON."); }
          try { due = JSON.parse(f.due || "{}"); } catch { throw new Error("“Due date rule” isn't valid JSON."); }
          const key = (isNew ? f.key : rule.key).trim();
          if (!/^[a-z0-9_]{2,40}$/.test(key)) throw new Error("Keys use a–z, 0–9 and _ (2 to 40 characters).");
          await api(`/admin/compliance/rules/${key}`, { method: "PUT", body: { title: f.title.trim(), category: f.category, authority: f.authority.trim(),
            applies, due, plain: f.plain.trim(), penalty_note: f.penalty_note.trim(), source_url: f.source_url.trim(), on: f.on } });
          onSaved(isNew ? "Rule added" : "Rule saved");
        }}>{isNew ? "Add rule" : "Save rule"}</Busy>
        <button className="btn sm ghost" onClick={onCancel}>Cancel</button>
      </div>
    </div>
  );
}

function Overrides({ rule, onSaved }) {
  const [f, setF] = useState({ date: "", new_date: "" });
  const [err, setErr] = useState("");
  const list = Object.entries(rule.overrides || {});
  const send = async (date, new_date) => {
    await api(`/admin/compliance/rules/${rule.key}/override`, { method: "PUT", body: { date, new_date } });
    onSaved(new_date ? "Date moved for every owner" : "Override removed");
  };
  return (
    <div className="stack">
      <span className="label">Government extensions</span>
      {list.length ? list.map(([from, to]) => (
        <div key={from} className="row between">
          <span className="small">{from} → <b>{to}</b></span>
          <Busy className="btn sm ghost" run={() => send(from, null)}>Remove</Busy>
        </div>
      )) : <span className="small muted">No dates moved.</span>}
      <div className="grid2">
        <Field label="Due date as the table gives it"><input className="input" type="date" value={f.date} onChange={(e) => setF({ ...f, date: e.target.value })} /></Field>
        <Field label="Extended to"><input className="input" type="date" value={f.new_date} onChange={(e) => setF({ ...f, new_date: e.target.value })} /></Field>
      </div>
      <FormError msg={err} />
      <div><Busy className="btn sm" disabled={!f.date || !f.new_date} onError={setErr} run={async () => { await send(f.date, f.new_date); setF({ date: "", new_date: "" }); }}>Move this date</Busy></div>
    </div>
  );
}

function Review({ review, onSaved }) {
  const [by, setBy] = useState("");
  const [err, setErr] = useState("");
  return (
    <div className={`cp-review ${review.reviewed ? "" : "warn"}`}>
      <i className="mark-dot" aria-hidden="true" />
      <div className="stack" style={{ gap: 10, width: "100%" }}>
        <b>{review.line}</b>
        {review.reviewed && <span className="small muted">Recorded: {review.reviewed_by}</span>}
        <span className="small muted">Owners see this line on their compliance calendar. Record a review only after a CA has checked the whole table.</span>
        <div className="grid2">
          <Field label="Reviewed by" hint="Name and membership number, e.g. CA Priya Mehta, M. No. 123456">
            <input className="input" maxLength={120} value={by} onChange={(e) => setBy(e.target.value)} />
          </Field>
        </div>
        <FormError msg={err} />
        <div><Busy className="btn sm primary" disabled={by.trim().length < 3} onError={setErr} run={async () => {
          await api("/admin/compliance/review", { method: "POST", body: { reviewed_by: by.trim() } });
          setBy("");
          onSaved("Review recorded");
        }}>Record the CA review</Busy></div>
      </div>
    </div>
  );
}

export default function AdminCompliance() {
  const { toast } = useHub();
  const [d, reload, err] = useLoad("/admin/compliance/rules");
  const [open, setOpen] = useState(null);   // rule key | "new"
  const [q, setQ] = useState("");
  const rules = useMemo(() => (d?.rules || []).filter((r) => !q.trim() || `${r.key} ${r.title} ${r.authority} ${r.category}`.toLowerCase().includes(q.trim().toLowerCase())), [d, q]);
  const saved = (msg) => { toast(msg); setOpen(null); reload(); };
  return (
    <Sheet kicker="Admin · Compliance rules" id="AD-08" title={<>The rules every <em>due date</em> comes from.</>}
      sub="Owners' compliance calendars are built from this table and their business identity. The AI never sets a date."
      actions={<Link className="btn sm ghost" to="/admin">Back to admin</Link>}>
      {err ? <LoadError err={err} /> : !d ? <Loading /> : (
        <>
          <Review review={d.review} onSaved={(m) => { toast(m); reload(); }} />
          <div className="row between">
            <input className="input" style={{ maxWidth: 360 }} value={q} onChange={(e) => setQ(e.target.value)} placeholder="Search rules" aria-label="Search rules" />
            <button className="btn sm" onClick={() => setOpen(open === "new" ? null : "new")}>Add a rule</button>
          </div>
          {open === "new" && <div className="panel active"><RuleEditor dueTypes={d.due_types} onSaved={saved} onCancel={() => setOpen(null)} /></div>}
          {!rules.length ? <Empty>No rules match.</Empty> : (
            <table className="t cards">
              <thead><tr><th>Rule</th><th>Applies to</th><th>Due</th><th /></tr></thead>
              <tbody>{rules.map((r) => (
                <React.Fragment key={r.key}>
                  <tr>
                    <td>
                      <b>{r.title}</b>
                      <div className="small muted mono">{r.key} · {r.category}{r.authority ? ` · ${r.authority}` : ""}</div>
                      <div className="row" style={{ gap: 6, marginTop: 6 }}>
                        {r.on === false ? <Pill kind="off">Off</Pill> : <Pill kind="on">On</Pill>}
                        {Object.keys(r.overrides || {}).length > 0 && <Pill kind="warn">{Object.keys(r.overrides).length} moved</Pill>}
                      </div>
                    </td>
                    <td data-l="Applies to" className="small">{appliesLine(r.applies)}</td>
                    <td data-l="Due" className="small">{dueLine(r.due)}</td>
                    <td><button className="btn sm ghost" onClick={() => setOpen(open === r.key ? null : r.key)}>{open === r.key ? "Close" : "Edit"}</button></td>
                  </tr>
                  {open === r.key && (
                    <tr><td colSpan={4}>
                      <div className="stack" style={{ gap: 24 }}>
                        <RuleEditor rule={r} dueTypes={d.due_types} onSaved={saved} onCancel={() => setOpen(null)} />
                        {r.due?.type !== "owner_date" && <Overrides rule={r} onSaved={(m) => { toast(m); reload(); }} />}
                      </div>
                    </td></tr>
                  )}
                </React.Fragment>
              ))}</tbody>
            </table>
          )}
        </>
      )}
    </Sheet>
  );
}
