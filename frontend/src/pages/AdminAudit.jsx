import React, { useState } from "react";
import { api } from "../api.js";
import { Busy, Field, FormError, Sheet, Stat, Switch, useHub, useLoad } from "../ui.jsx";
import { Loading, Pill } from "../v2.jsx";
import "./audit.css";

/* Admin → Engine Audit: the statements owners tick, the typical hours a week each gap counts (and what they go on), how much of a gap's cost becomes
   "growth unlocked" (and of its hours, "hours back"), the cap, and switching an item off. Every change is logged. */

const pct = (r) => Math.round((r ?? 0) * 100);

function ItemEditor({ it, onSaved }) {
  const { toast } = useHub();
  const [f, setF] = useState({ statement: it.statement, hint_cost: it.hint_cost, hint_hours: it.hint_hours,
                               est_hours: it.est_hours, rate: pct(it.rate), hours_rate: pct(it.hours_rate) });
  const [err, setErr] = useState("");
  const set = (k) => (e) => setF({ ...f, [k]: e.target.value });
  const save = async () => {
    const r = await api(`/admin/audit/items/${it.key}`, { method: "PUT", body: {
      statement: f.statement.trim(), hint_cost: f.hint_cost.trim(), hint_hours: f.hint_hours.trim(),
      est_hours: Number(f.est_hours), rate: Number(f.rate) / 100, hours_rate: Number(f.hours_rate) / 100 } });
    onSaved(r); toast("Saved");
  };
  const toggle = async (on) => { onSaved(await api(`/admin/audit/items/${it.key}`, { method: "PUT", body: { on } })); };
  const reset = async () => { const r = await api(`/admin/audit/items/${it.key}/reset`, { method: "POST" }); onSaved(r); toast("Back to default"); };
  return (
    <div className="aa-item">
      <div className="row between">
        <div className="row">
          <b>{it.feature_label}</b>
          {!it.feature_on && <Pill kind="off">feature switched off — not in the audit</Pill>}
          {it.changed && <Pill kind="on">changed</Pill>}
        </div>
        <Switch checked={it.on} onChange={toggle} label={it.on ? "In the audit" : "Left out"} />
      </div>
      <Field label="Accomplishment statement"><input className="input" value={f.statement} onChange={set("statement")} maxLength={160} /></Field>
      <div className="aa-grid">
        <Field label="Hint under “costing you a month?”"><input className="input" value={f.hint_cost} onChange={set("hint_cost")} maxLength={200} /></Field>
        <Field label="What the hours go on (shown on Missing)"><input className="input" value={f.hint_hours} onChange={set("hint_hours")} maxLength={200} /></Field>
      </div>
      <div className="aa-rates">
        <Field label="Typical hours a week a gap counts" hint={`Default ${it.default.est_hours}`}>
          <input className="input" inputMode="decimal" value={f.est_hours} onChange={set("est_hours")} />
        </Field>
        <Field label="Growth unlocked (% of the cost)" hint={`Default ${pct(it.default.rate)}%`}>
          <input className="input" inputMode="numeric" value={f.rate} onChange={set("rate")} />
        </Field>
        <Field label="Hours back (% of the hours)" hint={`Default ${pct(it.default.hours_rate)}%`}>
          <input className="input" inputMode="numeric" value={f.hours_rate} onChange={set("hours_rate")} />
        </Field>
      </div>
      <FormError msg={err} />
      <div className="row">
        <Busy className="btn sm primary" run={save} onError={setErr}>Save</Busy>
        {it.changed && <Busy className="btn sm ghost" run={reset} onError={setErr}>Reset to default</Busy>}
      </div>
    </div>
  );
}

export default function AdminAudit() {
  const [data, reload] = useLoad("/admin/audit");
  const [view, setView] = useState(null);
  const [cap, setCap] = useState(null);
  const [err, setErr] = useState("");
  const { toast } = useHub();
  const d = view || data;
  const head = { kicker: "Admin · Engine Audit", id: "AD-EA" };
  if (!d) return <Sheet {...head} title={<>The <em>Engine Audit</em>.</>}><Loading /></Sheet>;
  const capValue = cap ?? d.cap_pct;
  const saveCap = async () => { setView(await api("/admin/audit/settings", { method: "PUT", body: { cap_pct: Number(capValue) } })); toast("Saved"); };
  return (
    <Sheet {...head} title={<>The <em>Engine Audit</em>.</>}
      sub="What owners tick on Day 1: one statement per engine feature. For a gap they give a rough ₹ cost a month and hours a week; growth unlocked and hours back are those × the rates below. Every figure says “Estimated from your own numbers.” It uses 0 tokens.">
      <div className="statgrid">
        <Stat label="Audits taken" value={d.stats.audits} />
        <Stat label="Owners" value={d.stats.owners} />
        <Stat label="Taken today" value={d.stats.today} />
      </div>
      <div className="panel">
        <span className="label">The cap</span>
        <p className="small muted">When an owner's sales in a month are known (typed in the audit, Revenue Multiplier, or the Knowledge Hub target),
          total growth unlocked is never more than this share of them. Default {d.default_cap_pct}%.</p>
        <div className="row">
          <input className="input" style={{ maxWidth: 120 }} inputMode="numeric" value={capValue} onChange={(e) => setCap(e.target.value.replace(/\D/g, ""))} aria-label="Cap, %" />
          <span className="muted">% of a month's sales</span>
          <Busy className="btn sm primary" run={saveCap} onError={setErr}>Save</Busy>
        </div>
        <FormError msg={err} />
      </div>
      {d.engines.map((e) => {
        const items = d.items.filter((i) => i.engine === e.key);
        if (!items.length) return null;
        return (
          <div key={e.key} className="panel">
            <h2>{e.label} engine</h2>
            {items.map((it) => <ItemEditor key={`${it.key}:${it.statement}:${it.rate}:${it.hours_rate}:${it.est_hours}:${it.on}`} it={it} onSaved={(r) => { setView(r); reload(); }} />)}
          </div>
        );
      })}
    </Sheet>
  );
}
