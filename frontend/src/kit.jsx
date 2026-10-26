import React, { useEffect, useState } from "react";
import { api, ago } from "./api.js";
import { Busy, Copy, Field, Gate, Sheet, canAct, useHub, useLoad } from "./ui.jsx";

/* A generic editor for the hub's AI-written documents ("kits").
   `inputs`: what the owner fills in before the AI drafts it.  `fields`: how the saved document is edited.
   Field types: text · textarea · number · select · list (one item per line) · objlist (rows of sub-fields) · group (an object of sub-fields) · keyvalue */

function FieldEditor({ f, value, onChange, readOnly }) {
  const v = value ?? (f.type === "list" || f.type === "objlist" ? [] : f.type === "group" || f.type === "keyvalue" ? {} : "");
  if (f.type === "textarea") return <textarea className="textarea" rows={f.rows || 3} readOnly={readOnly} value={v} onChange={(e) => onChange(e.target.value)} />;
  if (f.type === "number") return <input className="input" type="number" min={f.min} max={f.max} readOnly={readOnly} value={v} onChange={(e) => onChange(Number(e.target.value))} />;
  if (f.type === "select") return (
    <select className="select" disabled={readOnly} value={v} onChange={(e) => onChange(e.target.value)}>
      {f.options.map(([val, label]) => <option key={val} value={val}>{label}</option>)}
    </select>
  );
  if (f.type === "list") return (
    <textarea className="textarea" rows={Math.max(3, Math.min(10, v.length + 1))} readOnly={readOnly} value={v.join("\n")}
      onChange={(e) => onChange(e.target.value.split("\n"))} onBlur={(e) => onChange(e.target.value.split("\n").map((x) => x.trim()).filter(Boolean))}
      placeholder="One per line" />
  );
  if (f.type === "keyvalue") return (
    <div className="stack" style={{ gap: 6 }}>
      {Object.entries(v).map(([k, val]) => <div key={k} className="row between small"><span className="muted">{k}</span><b>{val}</b></div>)}
    </div>
  );
  if (f.type === "group") return (
    <div className="grid2">
      {f.fields.map((sf) => <Field key={sf.key} label={sf.label}><FieldEditor f={sf} value={v[sf.key]} readOnly={readOnly} onChange={(x) => onChange({ ...v, [sf.key]: x })} /></Field>)}
    </div>
  );
  if (f.type === "objlist") return (
    <div className="stack" style={{ gap: 8 }}>
      {v.map((row, i) => (
        <div key={i} className="objrow">
          {f.fields.map((sf) => (
            <Field key={sf.key} label={`${sf.label}${sf.key === f.fields[0].key ? ` ${i + 1}` : ""}`}>
              <FieldEditor f={sf} value={row[sf.key]} readOnly={readOnly} onChange={(x) => onChange(v.map((r, j) => (j === i ? { ...r, [sf.key]: x } : r)))} />
            </Field>
          ))}
          {!readOnly && <div className="row"><button className="btn sm ghost" onClick={() => onChange(v.filter((_, j) => j !== i))}>Remove</button></div>}
        </div>
      ))}
      {!readOnly && <div><button className="btn sm ghost" onClick={() => onChange([...v, Object.fromEntries(f.fields.map((sf) => [sf.key, sf.type === "list" ? [] : sf.type === "number" ? (sf.min || 0) : sf.type === "select" ? sf.options[0][0] : ""]))])}>Add {f.item || "row"}</button></div>}
    </div>
  );
  if (String(v).length > 70) return <textarea className="textarea" rows={2} style={{ minHeight: 0 }} readOnly={readOnly} value={v} onChange={(e) => onChange(e.target.value.replace(/\n/g, " "))} />;
  return <input className="input" readOnly={readOnly} value={v} onChange={(e) => onChange(e.target.value)} />;
}

/** The same document for someone who can read it but not change it: plain text, not form fields. */
function ReadValue({ f, value }) {
  const v = value;
  if (v == null || v === "" || (Array.isArray(v) && !v.length)) return <span className="small muted">—</span>;
  if (f.type === "list") return <ul className="read-list">{v.map((x, i) => <li key={i}>{x}</li>)}</ul>;
  if (f.type === "select") return <span>{(f.options.find((o) => o[0] === v) || [v, v])[1]}</span>;
  if (f.type === "keyvalue") return <div className="stack" style={{ gap: 6 }}>{Object.entries(v).map(([k, x]) => <div key={k} className="row between small"><span className="muted">{k}</span><b>{x}</b></div>)}</div>;
  if (f.type === "group") return <div className="grid2">{f.fields.map((sf) => <div key={sf.key} className="stack" style={{ gap: 4 }}><span className="label">{sf.label}</span><ReadValue f={sf} value={v[sf.key]} /></div>)}</div>;
  if (f.type === "objlist") return (
    <div className="stack" style={{ gap: 8 }}>
      {v.map((row, i) => (
        <div key={i} className="objrow">
          {f.fields.map((sf, j) => (j === 0
            ? <b key={sf.key} style={{ overflowWrap: "anywhere" }}>{i + 1}. {Array.isArray(row[sf.key]) ? row[sf.key].join(", ") : row[sf.key]}</b>
            : (row[sf.key] != null && row[sf.key] !== "" && !(Array.isArray(row[sf.key]) && !row[sf.key].length)) && (
              <div key={sf.key} className="stack" style={{ gap: 2 }}><span className="label">{sf.label}</span><ReadValue f={sf} value={row[sf.key]} /></div>)))}
        </div>
      ))}
    </div>
  );
  return <span style={{ whiteSpace: "pre-wrap", overflowWrap: "anywhere" }}>{String(v)}</span>;
}

/** Plain text of a document, for copying into WhatsApp or a printout. */
export function asText(fields, c) {
  const out = [];
  const line = (f, v, indent = "") => {
    if (v == null || v === "" || (Array.isArray(v) && !v.length)) return;
    if (f.type === "list") { out.push(`${indent}${f.label}:`); v.forEach((x) => out.push(`${indent}• ${x}`)); }
    else if (f.type === "objlist") { out.push(`${indent}${f.label}:`); v.forEach((row, i) => { out.push(`${indent}${i + 1}. ` + f.fields.map((sf) => (Array.isArray(row[sf.key]) ? row[sf.key].join("; ") : row[sf.key])).filter(Boolean).join(" — ")); }); }
    else if (f.type === "group") { out.push(`${indent}${f.label}:`); f.fields.forEach((sf) => line(sf, v[sf.key], indent + "  ")); }
    else if (f.type === "keyvalue") { out.push(`${indent}${f.label}:`); Object.entries(v).forEach(([k, x]) => out.push(`${indent}• ${k}: ${x}`)); }
    else if (f.type === "select") out.push(`${indent}${f.label}: ${(f.options.find((o) => o[0] === v) || [v, v])[1]}`);
    else out.push(`${indent}${f.label}: ${v}`);
  };
  fields.forEach((f) => line(f, c[f.key]));
  return out.join("\n");
}

function Editor({ kind, id, fields, writeRole, onSaved, onDeleted, extraActions }) {
  const { me, toast } = useHub();
  const [doc, setDoc] = useState(null);
  const [c, setC] = useState(null);
  useEffect(() => { setDoc(null); api(`/kits/${kind}/${id}`).then((d) => { setDoc(d); setC(d.content); }); }, [kind, id]);
  if (!doc || !c) return <div className="panel muted">Loading…</div>;
  const readOnly = !canAct(me, writeRole);
  return (
    <div className="panel">
      <div className="row between">
        <span className="label">{doc.by_agent ? `Written by the ${doc.by_agent}` : readOnly ? "Read only" : "Edit and save"} · updated {ago(doc.updated_at)}</span>
        <div className="row">
          {extraActions && extraActions(doc)}
          <Copy text={asText(fields, c)} label="Copy as text" className="btn sm ghost" />
        </div>
      </div>
      {readOnly
        ? fields.map((f) => <div key={f.key} className="stack" style={{ gap: 6 }}><span className="label">{f.label}</span><ReadValue f={f} value={c[f.key]} /></div>)
        : fields.map((f) => <Field key={f.key} label={f.label} hint={f.hint}><FieldEditor f={f} value={c[f.key]} readOnly={readOnly} onChange={(v) => setC({ ...c, [f.key]: v })} /></Field>)}
      {!readOnly && (
        <div className="row">
          <Busy className="btn primary" run={async () => { const d = await api(`/kits/${kind}/${id}`, { method: "PUT", body: { content: c } }); setDoc(d); setC(d.content); toast("Saved"); onSaved(); }}>Save</Busy>
          <Busy className="btn ghost" run={async () => { if (!window.confirm("Delete this document?")) return; await api(`/kits/${kind}/${id}`, { method: "DELETE" }); toast("Deleted"); onDeleted(); }}>Delete</Busy>
        </div>
      )}
    </div>
  );
}

function NewForm({ kind, inputs, inputsExtra, cta, onCreated }) {
  const [v, setV] = useState(Object.fromEntries(inputs.map((i) => [i.key, ""])));
  const ready = inputs.filter((i) => i.required).every((i) => (v[i.key] || "").trim().length >= 2);
  return (
    <div className="panel active">
      <span className="label">New</span>
      {inputs.map((i) => (
        <Field key={i.key} label={i.label} hint={i.hint}>
          {i.type === "textarea" ? <textarea className="textarea" rows={i.rows || 4} placeholder={i.placeholder} value={v[i.key]} onChange={(e) => setV({ ...v, [i.key]: e.target.value })} />
            : i.type === "select" ? <select className="select" value={v[i.key]} onChange={(e) => setV({ ...v, [i.key]: e.target.value })}><option value="">Choose…</option>{i.options.map((o) => <option key={o} value={o}>{o}</option>)}</select>
            : <input className="input" placeholder={i.placeholder} aria-label={i.label} value={v[i.key]} onChange={(e) => setV({ ...v, [i.key]: e.target.value })} />}
        </Field>
      ))}
      {inputsExtra && inputsExtra(setV)}
      <Busy className="btn primary ai" disabled={!ready} run={async () => { const d = await api(`/kits/${kind}`, { method: "POST", body: { inputs: v } }); setV(Object.fromEntries(inputs.map((i) => [i.key, ""]))); onCreated(d); }}>{cta}</Busy>
      <span className="small muted">Uses one AI token. You can edit everything after.</span>
    </div>
  );
}

function KitInner({ kind, inputs, inputsExtra, fields, cta = "Write it with AI", writeRole = "owner", empty, extraActions, canCreate = true }) {
  const { me, refresh } = useHub();
  const [list, reload] = useLoad(`/kits/${kind}`);
  const [sel, setSel] = useState(null);
  const [creating, setCreating] = useState(false);
  useEffect(() => { if (list && list.length && !sel && !creating) setSel(list[0].id); }, [list]);
  const writer = canAct(me, writeRole) && canCreate;
  return (
    <div className="split">
      <div className="stack">
        {writer && <button className={`btn ${creating || !list?.length ? "primary" : ""}`} onClick={() => { setCreating(true); setSel(null); }}>+ New</button>}
        <div className="listbox" role="list">
          {list?.map((d) => (
            <button key={d.id} className={sel === d.id ? "on" : ""} onClick={() => { setSel(d.id); setCreating(false); }}>
              <span>{d.title}</span><span className="small muted">{ago(d.updated_at)}{d.by_agent ? ` · ${d.by_agent}` : ""}</span>
            </button>
          ))}
          {list && !list.length && <span className="small muted">{empty || "Nothing here yet."}</span>}
        </div>
      </div>
      <div>
        {(creating || (list && !list.length)) && writer ? (
          <NewForm kind={kind} inputs={inputs} inputsExtra={inputsExtra} cta={cta} onCreated={(d) => { setCreating(false); reload().then(() => setSel(d.id)); refresh(); }} />
        ) : sel ? (
          <Editor key={sel} kind={kind} id={sel} fields={fields} writeRole={writeRole} extraActions={extraActions} onSaved={reload} onDeleted={() => { setSel(null); reload(); }} />
        ) : list && !list.length ? <div className="empty"><p className="muted">{empty || "Nothing here yet."}</p></div> : null}
      </div>
    </div>
  );
}

export default function KitPage({ feature, readRole = "staff", kicker, title, sub, id, pillar, lockedWhat, ...rest }) {
  return (
    <Gate feature={feature} role={readRole} kicker={kicker} id={id} what={lockedWhat || sub}>
      <Sheet kicker={kicker} id={id} pillar={pillar} title={title} sub={sub}>
        <KitInner {...rest} />
      </Sheet>
    </Gate>
  );
}
