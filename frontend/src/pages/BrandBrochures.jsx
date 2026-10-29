import React, { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../api.js";
import { Busy, Copy, Field, FormError, LoadError, canAct, useHub, useLoad } from "../ui.jsx";
import { Loading, Pill } from "../v2.jsx";

/* Brochures (v2.3, a Brand kit tab — paid members): a business brochure and a products & services brochure, written as text
   from the brand kit, the website, the Knowledge Hub and the competitor summary. Every line can be edited, then copied or
   downloaded. The contact details are added by the hub from the Knowledge Hub. */

const asText = (label, c, contact) => {
  const out = [label, "", c.headline];
  if (c.subheadline) out.push(c.subheadline);
  for (const s of c.sections || []) {
    out.push("", (s.heading || "").toUpperCase());
    if (s.text) out.push(s.text);
    (s.points || []).forEach((p) => out.push(`• ${p}`));
  }
  if (c.proof?.length) out.push("", "WHAT OUR CUSTOMERS SAY", ...c.proof.map((p) => `“${p}”`));
  if (c.cta) out.push("", c.cta);
  if (contact?.length) out.push("", ...contact);
  return out.join("\n");
};

function Editor({ kind, label, content, contact, canEdit, onData }) {
  const [c, setC] = useState(content);
  const [err, setErr] = useState("");
  useEffect(() => setC(content), [content]);
  const dirty = JSON.stringify(c) !== JSON.stringify(content);
  const setSec = (i, patch) => setC({ ...c, sections: c.sections.map((s, j) => (j === i ? { ...s, ...patch } : s)) });
  const save = async () => {
    const body = { ...c, sections: c.sections.map((s) => ({ ...s, points: (s.points || []).map((p) => p.trim()).filter(Boolean) })),
      proof: (c.proof || []).map((p) => p.trim()).filter(Boolean) };
    onData(await api(`/brochures/${kind}`, { method: "PUT", body }));
  };
  return (
    <div className="stack bb-edit">
      <Field label="Headline"><input className="input" maxLength={140} disabled={!canEdit} value={c.headline} onChange={(e) => setC({ ...c, headline: e.target.value })} /></Field>
      <Field label="Line under it"><input className="input" maxLength={240} disabled={!canEdit} value={c.subheadline || ""} onChange={(e) => setC({ ...c, subheadline: e.target.value })} /></Field>
      {c.sections.map((s, i) => (
        <div key={i} className="bb-sec">
          <Field label={`Section ${i + 1}`}><input className="input" maxLength={80} disabled={!canEdit} value={s.heading} onChange={(e) => setSec(i, { heading: e.target.value })} /></Field>
          <textarea className="textarea" rows={3} maxLength={900} disabled={!canEdit} aria-label={`${s.heading} — text`} value={s.text || ""}
            onChange={(e) => setSec(i, { text: e.target.value })} />
          <textarea className="textarea" rows={Math.max(2, (s.points || []).length)} disabled={!canEdit} aria-label={`${s.heading} — points, one per line`}
            placeholder="Points, one per line" value={(s.points || []).join("\n")} onChange={(e) => setSec(i, { points: e.target.value.split("\n") })} />
        </div>
      ))}
      <Field label="What customers say" hint="Only what you can show. One per line.">
        <textarea className="textarea" rows={2} disabled={!canEdit} value={(c.proof || []).join("\n")} onChange={(e) => setC({ ...c, proof: e.target.value.split("\n") })} />
      </Field>
      <Field label="Closing line"><input className="input" maxLength={240} disabled={!canEdit} value={c.cta || ""} onChange={(e) => setC({ ...c, cta: e.target.value })} /></Field>
      <div className="panel flat">
        <span className="label">Contact details, added from your Knowledge Hub</span>
        {contact.length ? contact.map((x) => <span key={x} className="small">{x}</span>) : <span className="small muted">Add your WhatsApp number and email in the Knowledge Hub.</span>}
      </div>
      <FormError msg={err} />
      <div className="row">
        {canEdit && <Busy className="btn primary" disabled={!dirty} onError={setErr} run={save}>Save changes</Busy>}
        <Copy text={asText(label, c, contact)} label="Copy all" />
        <a className="btn sm" href={`/api/brochures/${kind}.txt`}>Download as text</a>
      </div>
    </div>
  );
}

export default function Brochures() {
  const { me, refresh } = useHub();
  const [loaded, , loadErr] = useLoad("/brochures");
  const [d, setD] = useState(null);
  const [err, setErr] = useState("");
  useEffect(() => { if (loaded) setD(loaded); }, [loaded]);
  const canEdit = canAct(me, "owner");
  if (!d) return <div className="panel">{loadErr ? <LoadError err={loadErr} /> : <Loading />}</div>;
  if (!d.open) {
    return (
      <div className="panel active">
        <span className="label">Brochures</span>
        <h3>Your business brochure and your products & services brochure.</h3>
        <p className="small muted">Written from your brand kit, your website and what makes you different from competitors — ready to send to any buyer or print.</p>
        <p><b>{d.locked_note}</b></p>
        {me?.workspace?.role === "owner" && <div><Link className="btn money" to="/plans">See OwnerDesk Membership</Link></div>}
      </div>
    );
  }
  const write = async (kind) => { setD(await api(`/brochures/${kind}/generate`, { method: "POST" })); refresh(); };
  return (
    <div className="stack">
      {d.kinds.map((k) => (
        <div key={k.key} className="panel">
          <div className="row between">
            <div className="stack" style={{ gap: 4 }}>
              <span className="label">{k.label}</span>
              <span className="small muted">{k.what.charAt(0).toUpperCase() + k.what.slice(1)}.</span>
            </div>
            {k.brochure?.edited && <Pill kind="on">Your edits</Pill>}
          </div>
          {k.brochure ? <Editor kind={k.key} label={k.label} content={k.brochure.content} contact={d.contact} canEdit={canEdit} onData={setD} /> : null}
          {canEdit && (
            <div className="row">
              <Busy className={`btn ai ${k.brochure ? "ghost" : "primary"}`} onError={setErr} run={() => write(k.key)}>
                {k.brochure ? "Write it again" : `Write the ${k.label.toLowerCase()}`}
              </Busy>
              <span className="small muted">Uses {d.tokens_each} AI token{k.brochure ? ". Your edits to this brochure are replaced." : "."}</span>
            </div>
          )}
        </div>
      ))}
      <FormError msg={err} />
    </div>
  );
}
