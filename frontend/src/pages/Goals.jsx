import React, { useState } from "react";
import { api, ago, day } from "../api.js";
import { Busy, Field, Modal, Sheet, useHub, useLoad } from "../ui.jsx";
import { Pill, Tabs } from "../v2.jsx";
import "./membership.css";

const METRICS = [
  ["custom", "Something I'll check in on (revenue, orders…)", "₹"],
  ["leads", "New inquiries — counts itself", "inquiries"],
  ["won", "Customers won — counts itself", "customers"],
  ["site_views", "Website views — counts itself", "views"],
];
const UNIT_FOR = Object.fromEntries(METRICS.map(([k, , u]) => [k, u]));
const COUNTS_FROM = { leads: "your leads inbox", won: "leads you mark Won", site_views: "visits to your website" };
const STATUS = { on_track: ["On track", "token"], behind: ["Behind", "token grey"], done: ["Done", "token"] };
const KINDS = [
  ["financial", "Financial", "Money in the business: revenue, profit, collections."],
  ["functional", "Functional", "How one part of the business runs: sales, marketing, operations, accounts or HR."],
  ["learning", "Learning", "What you or your team will learn."],
];
const AREAS = [["sales", "Sales"], ["marketing", "Marketing"], ["operations", "Operations"], ["accounts", "Accounts"], ["HR", "HR"]];
const AREA_LABEL = Object.fromEntries(AREAS);
const APPROACHES = [
  ["aspirational", "Aspirational", "Reach for something new."],
  ["limitation", "Limitation", "Remove what's holding you back."],
];

const fmt = (x) => new Intl.NumberFormat("en-IN", { maximumFractionDigits: 1 }).format(x || 0);
const isRupee = (u) => ["₹", "rs", "rs.", "inr", "rupees"].includes((u || "").trim().toLowerCase());

/** A goal value in its unit. Rupees wear amber; nothing else does. */
export function Amount({ value, unit }) {
  if (isRupee(unit)) return <span className="money">₹{fmt(value)}</span>;
  const one = Number(value) === 1 && /[^s]s$/i.test(unit || "") ? unit.replace(/(ies|s)$/i, (m) => (m.toLowerCase() === "ies" ? "y" : "")) : unit;
  return <span>{fmt(value)}{one ? ` ${one}` : ""}</span>;
}

const iso = (d) => d.toLocaleDateString("en-CA");
function presets() {
  const t = new Date();
  const q = Math.floor(t.getMonth() / 3);
  const add = (d, n) => { const x = new Date(d); x.setDate(x.getDate() + n); return x; };
  return [
    ["This month", iso(t), iso(new Date(t.getFullYear(), t.getMonth() + 1, 0))],
    ["This quarter", iso(t), iso(new Date(t.getFullYear(), q * 3 + 3, 0))],
    ["Next 90 days", iso(t), iso(add(t, 89))],
  ];
}

const EXAMPLES = [
  { title: "Revenue this quarter", kind: "financial", metric: "custom", target: 500000, unit: "₹", preset: 1 },
  { title: "New inquiries this month", kind: "functional", area: "marketing", metric: "leads", target: 30, unit: "inquiries", preset: 0 },
  { title: "New customers this month", kind: "functional", area: "sales", metric: "won", target: 8, unit: "customers", preset: 0 },
  { title: "Read 3 books on selling", kind: "learning", metric: "custom", target: 3, unit: "books", preset: 1 },
];

/** A row of choices that behave like radio buttons. */
function Choice({ label, options, value, onChange }) {
  const hint = options.find(([k]) => k === value)?.[2];
  return (
    <div className="stack" style={{ gap: 8 }}>
      <span className="label">{label}</span>
      <div className="row" role="radiogroup" aria-label={label} style={{ gap: 8 }}>
        {options.map(([k, l]) => <button key={k} type="button" role="radio" aria-checked={value === k} className={`btn sm ${value === k ? "primary" : "ghost"}`} onClick={() => onChange(k)}>{l}</button>)}
      </div>
      {hint && <span className="hint">{hint}</span>}
    </div>
  );
}

/** `goal` = editing a saved goal; `initial` = a new goal pre-filled from an example. */
function GoalForm({ goal, initial, onClose, onSaved }) {
  const [p0] = useState(presets);
  const src = goal || initial;
  const [g, setG] = useState(src ? { title: src.title, kind: src.kind || "financial", area: src.area || "", approach: src.approach || "aspirational",
    metric: src.metric, target: String(src.target), unit: src.unit, start: src.start, end: src.end }
    : { title: "", kind: "financial", area: "", approach: "aspirational", metric: "custom", target: "", unit: "₹", start: p0[1][1], end: p0[1][2] });
  const target = Number(g.target);
  const ready = g.title.trim().length >= 3 && target > 0 && g.start && g.end && g.end >= g.start && (g.kind !== "functional" || g.area);
  const setMetric = (metric) => setG({ ...g, metric, unit: !g.unit || g.unit === UNIT_FOR[g.metric] ? UNIT_FOR[metric] : g.unit });
  const save = async () => {
    const body = { ...g, title: g.title.trim(), target, unit: g.unit.trim(), area: g.kind === "functional" ? g.area : "" };
    const saved = goal ? await api(`/goals/${goal.id}`, { method: "PATCH", body }) : await api("/goals", { method: "POST", body });
    onSaved(saved);
  };
  return (
    <Modal onClose={onClose}>
      <span className="kicker">{goal ? "Edit goal" : "New goal"}</span>
      <Field label="Goal"><input className="input" value={g.title} maxLength={140} placeholder="Revenue this quarter" onChange={(e) => setG({ ...g, title: e.target.value })} /></Field>
      <Choice label="Kind of goal" options={KINDS} value={g.kind} onChange={(kind) => setG({
        ...g, kind, unit: kind === "financial" && !(g.unit || "").trim() ? "₹" : kind !== "financial" && isRupee(g.unit) && g.metric === "custom" ? "" : g.unit,
      })} />
      {g.kind === "functional" && (
        <Field label="Which part of the business">
          <select className="select" value={g.area} onChange={(e) => setG({ ...g, area: e.target.value })}>
            <option value="">Choose…</option>
            {AREAS.map(([k, l]) => <option key={k} value={k}>{l}</option>)}
            {g.area && !AREA_LABEL[g.area] && <option value={g.area}>{g.area}</option>}
          </select>
        </Field>
      )}
      <Choice label="Approach" options={APPROACHES} value={g.approach} onChange={(approach) => setG({ ...g, approach })} />
      <Field label="How we measure it" hint={g.metric === "custom" ? "You add the latest number each week — the newest check-in is the progress." : "Counted for you from the hub, between the two dates."}>
        <select className="select" value={g.metric} onChange={(e) => setMetric(e.target.value)}>{METRICS.map(([k, l]) => <option key={k} value={k}>{l}</option>)}</select>
      </Field>
      <div className="grid2">
        <Field label="Target" hint={target > 0 ? <>That's <Amount value={target} unit={g.unit} /></> : null}>
          <input className="input" type="number" min="0" step="any" inputMode="decimal" value={g.target} onChange={(e) => setG({ ...g, target: e.target.value })} /></Field>
        <Field label="Unit" hint="₹ for money, or a word like orders, customers"><input className="input" value={g.unit} maxLength={30} onChange={(e) => setG({ ...g, unit: e.target.value })} /></Field>
      </div>
      <div className="chips">{p0.map(([label, s, e]) => <button key={label} type="button" className="chip" onClick={() => setG({ ...g, start: s, end: e })}>{label}</button>)}</div>
      <div className="grid2">
        <Field label="From"><input className="input" type="date" value={g.start} onChange={(e) => setG({ ...g, start: e.target.value })} /></Field>
        <Field label="To"><input className="input" type="date" value={g.end} min={g.start} onChange={(e) => setG({ ...g, end: e.target.value })} /></Field>
      </div>
      <div className="row">
        <Busy className="btn primary" disabled={!ready} run={save}>{goal ? "Save" : "Set goal"}</Busy>
        <button className="btn ghost" onClick={onClose}>Cancel</button>
      </div>
    </Modal>
  );
}

function GoalCard({ g, onChange, onEdit }) {
  const { refresh, toast } = useHub();
  const [val, setVal] = useState("");
  const [note, setNote] = useState("");
  const [label, cls] = g.ended && g.status !== "done" ? ["Ended", "token grey"] : STATUS[g.status];
  const checkins = [...(g.checkins || [])].reverse().slice(0, 3);
  return (
    <div className={`panel ${g.status === "done" ? "active" : ""}`}>
      <div className="row between">
        <span className="label">{g.metric_label} · {day(g.start)} – {day(g.end)}</span>
        <span className={cls}>{label}</span>
      </div>
      <div className="row" style={{ gap: 6 }}>
        <Pill kind="on">{g.kind_label || "Financial"}</Pill>
        {g.kind === "functional" && g.area && <Pill>{AREA_LABEL[g.area] || g.area}</Pill>}
        {g.approach === "limitation" && <Pill>Limitation</Pill>}
      </div>
      <h3>{g.title}</h3>
      <div className="row" style={{ alignItems: "baseline", gap: 8 }}>
        <span className="num" style={{ fontSize: 30, overflowWrap: "anywhere" }}><Amount value={g.current} unit={g.unit} /></span>
        <span className="small muted">of <Amount value={g.target} unit={g.unit} /></span>
      </div>
      <div className="goal-bar" role="img" aria-label={`${fmt(g.pct)}% done; ${fmt(g.expected_pct)}% of the time has gone`}>
        <i style={{ width: `${Math.min(g.pct, 100)}%` }} />
        {!g.ended && g.status !== "done" && <b style={{ left: `${Math.min(g.expected_pct, 100)}%` }} title={`Where you'd be at an even pace: ${fmt(g.expected_pct)}%`} />}
      </div>
      <span className="small muted">
        {fmt(g.pct)}% done · {fmt(g.expected_pct)}% of the time gone · {g.ended ? "finished" : `${g.days_left} ${g.days_left === 1 ? "day" : "days"} left`}
      </span>

      {g.metric === "custom" ? (
        <div className="stack" style={{ gap: 8 }}>
          <div className="checkin">
            <input className="input" type="number" step="any" inputMode="decimal" aria-label="Latest number" placeholder="Latest number" value={val} onChange={(e) => setVal(e.target.value)} />
            <input className="input" aria-label="Note" placeholder="Note (optional)" maxLength={300} value={note} onChange={(e) => setNote(e.target.value)} />
            <Busy className="btn sm" disabled={val === "" || Number.isNaN(Number(val))} run={async () => {
              onChange(await api(`/goals/${g.id}/checkins`, { method: "POST", body: { value: Number(val), note } })); setVal(""); setNote(""); toast("Checked in");
            }}>Check in</Busy>
          </div>
          {checkins.length > 0 ? (
            <div className="timeline">{checkins.map((c, i) => (
              <div key={i} className={i === 0 ? "done" : ""}><span className="small"><Amount value={c.value} unit={g.unit} />{c.note ? ` — ${c.note}` : ""}</span><span className="small muted">{ago(c.at)}</span></div>
            ))}</div>
          ) : <span className="small muted">No check-ins yet. Add the latest number every Monday.</span>}
        </div>
      ) : <span className="small muted">Counts itself from {COUNTS_FROM[g.metric]}.</span>}

      {g.coach && (
        <div className="prompt">
          <span className="label">Three actions · {ago(g.coach.at)}</span>
          <ol style={{ margin: "8px 0 0", paddingLeft: 20, display: "grid", gap: 6, whiteSpace: "normal" }}>
            {g.coach.actions.map((a, i) => <li key={i}>{a.action}{a.why && <div className="small muted">{a.why}</div>}</li>)}
          </ol>
        </div>
      )}
      <div className="row">
        <Busy className="btn sm ai" run={async () => { onChange(await api(`/goals/${g.id}/coach`, { method: "POST" })); refresh(); }}>
          {g.coach ? "Fresh actions" : "3 actions to hit this"}
        </Busy>
        <button className="btn sm ghost" onClick={onEdit}>Edit</button>
        <Busy className="btn sm ghost" run={async () => { if (!window.confirm("Delete this goal and its check-ins?")) return; await api(`/goals/${g.id}`, { method: "DELETE" }); onChange(null); }}>Delete</Busy>
      </div>
      <span className="small muted">Actions use 1 AI run and are written from your Business Brain and this goal's numbers.</span>
    </div>
  );
}

export default function Goals() {
  const [goals, reload] = useLoad("/goals");
  const [form, setForm] = useState(null); // null | "new" | a saved goal (edit) | an example (new, pre-filled)
  const [kind, setKind] = useState("all");
  if (!goals) return null;
  const kindOf = (g) => g.kind || "financial";
  const shown = kind === "all" ? goals : goals.filter((g) => kindOf(g) === kind);
  const tabs = [["all", `All (${goals.length})`], ...KINDS.map(([k, l]) => [k, `${l} (${goals.filter((g) => kindOf(g) === k).length})`])];
  const running = goals.filter((g) => !g.ended);
  const ok = running.filter((g) => g.status !== "behind").length;
  const title = !goals.length ? <>Set the number that <em>matters</em>.</>
    : !running.length ? <>Time to set the next <em>goal</em>.</>
    : <>{ok} of {running.length} {running.length === 1 ? "goal" : "goals"} on <em>track</em>.</>;
  const fromExample = (x) => { const p = presets()[x.preset]; return { ...x, start: p[1], end: p[2] }; };
  const blankOf = (k) => fromExample({ title: "", kind: k, area: "", approach: "aspirational", metric: "custom", target: "", unit: k === "financial" ? "₹" : "", preset: 1 });
  const kindName = KINDS.find(([k]) => k === kind)?.[1].toLowerCase();
  return (
    <Sheet kicker="Structure · Goals" id="ST-03" pillar="Structure" title={title}
      sub="Financial, functional and learning goals. Inquiries, customers won and website views count themselves; for anything else, like revenue, check in once a week."
      actions={<button className="btn primary" onClick={() => setForm(kind === "all" ? "new" : blankOf(kind))}>New goal</button>}>
      {!goals.length && (
        <div className="empty">
          <h2>Three goals are plenty.</h2>
          <p className="muted">Pick one financial goal, one for how a part of the business runs, and one thing to learn. Start from an example:</p>
          <div className="chips" style={{ justifyContent: "center" }}>
            {EXAMPLES.map((x) => <button key={x.title} className="chip" onClick={() => setForm(fromExample(x))}>{x.title}</button>)}
          </div>
        </div>
      )}
      {goals.length > 0 && (
        <>
          <Tabs tabs={tabs} value={kind} onChange={setKind} />
          <div className="row small muted"><span className="goal-key"><i /> where you'd be today at an even pace</span></div>
          {shown.length > 0 ? (
            <div className="grid2" style={{ alignItems: "start" }}>
              {shown.map((g) => <GoalCard key={g.id} g={g} onEdit={() => setForm(g)} onChange={() => reload()} />)}
            </div>
          ) : (
            <div className="empty">
              <p className="muted">No {kindName} goals yet. {KINDS.find(([k]) => k === kind)?.[2]}</p>
              <button className="btn" onClick={() => setForm(blankOf(kind))}>New {kindName} goal</button>
            </div>
          )}
        </>
      )}
      {form && (
        <GoalForm key={form === "new" ? "new" : form.id || `${form.kind}:${form.title}`}
          goal={form !== "new" && form.id ? form : null} initial={form !== "new" && !form.id ? form : null}
          onClose={() => setForm(null)} onSaved={() => { setForm(null); reload(); }} />
      )}
    </Sheet>
  );
}
