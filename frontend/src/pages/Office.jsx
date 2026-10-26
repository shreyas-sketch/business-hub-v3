import React, { useEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { ago, api } from "../api.js";
import { Busy, Sheet, Switch, canAct, useHub, useLoad } from "../ui.jsx";
import { Pill } from "../v2.jsx";
import "./growth.css";

const EXAMPLES = [
  "Follow up with every lead that's still waiting",
  "Write next week's posts about our festive offer",
  "Write an SOP for handling a new order and assign tasks to the team",
  "Remind customers who owe us money",
  "We need to hire a sales executive",
  "What should I focus on to grow faster this month?",
];
const PER_DEPARTMENT = 6;
const KIT_PATH = { sop: "/sops", jd: "/hiring", role: "/roles", review: "/reviews" };
const STEP_CLASS = { done: "done", waiting: "wait", failed: "fail", skipped: "fail" };
const STATUS_LABEL = { queued: "Queued", running: "Working…", done: "Done", waiting: "Waiting for you", failed: "Couldn't finish", skipped: "Skipped" };
const RUN_LABEL = { running: "Working", done: "Done", partial: "Partly done", failed: "Didn't finish", stopped: "Interrupted" };

function Result({ refs }) {
  if (!refs) return null;
  return (
    <div className="row" style={{ gap: 8 }}>
      {refs.approvals?.length > 0 && <Link className="btn sm" to="/approvals">Open the Send list</Link>}
      {refs.outputs?.length > 0 && <Link className="btn sm ghost" to="/outputs">See in My outputs</Link>}
      {refs.kits?.length > 0 && KIT_PATH[refs.kit_kind] && <Link className="btn sm ghost" to={KIT_PATH[refs.kit_kind]}>Open it</Link>}
      {refs.tasks?.length > 0 && <Link className="btn sm ghost" to="/tasks">See tasks</Link>}
    </div>
  );
}

function Run({ run }) {
  return (
    <div className="panel active">
      <div className="row between">
        <span className="label">“{run.instruction}”</span>
        <span className={`token ${run.status === "running" ? "" : "grey"}`}>{RUN_LABEL[run.status] || run.status}</span>
      </div>
      <p className="small muted">{run.summary}</p>
      <div className="timeline">
        {run.steps.map((s) => (
          <div key={s.n} className={STEP_CLASS[s.status] || ""}>
            <span className="small"><b>{s.staff_title}</b> · {s.label} <span className="muted">· {STATUS_LABEL[s.status] || s.status}</span></span>
            {s.why && s.status === "queued" && <span className="small muted">{s.why}</span>}
            {s.note && <span className="small">{s.note}</span>}
            <Result refs={s.refs} />
          </div>
        ))}
      </div>
    </div>
  );
}

/** The AI staff hired into this department on the Workforce page. */
function Hired({ roles, owner }) {
  return (
    <div className="stack" style={{ gap: 8 }}>
      <div className="row between">
        <span className="label">AI staff here · {roles.length} of {PER_DEPARTMENT}</span>
        {owner && <Link className="small" to="/workforce">{roles.length ? "Change" : "Hire"}</Link>}
      </div>
      {roles.length
        ? <div className="wf-names">{roles.map((r) => <Pill key={r.id} kind="on">{r.name}</Pill>)}</div>
        : <span className="small muted">Nobody hired here yet{owner ? "" : " — the owner picks them on the Workforce page"}.</span>}
    </div>
  );
}

function StaffCard({ s, owner, canActAlone, reload }) {
  const { toast } = useHub();
  const patch = async (body) => { await api(`/office/staff/${s.key}`, { method: "PATCH", body }); reload(); toast("Saved"); };
  return (
    <div className={`panel ${s.on ? "" : "flat"}`}>
      <div className="row between" style={{ flexWrap: "nowrap", alignItems: "flex-start" }}>
        <h3 style={{ minWidth: 0 }}>{s.title}</h3>
        {owner ? <Switch checked={s.on} onChange={(on) => patch({ on })} label={s.on ? "On" : "Off"} /> : <span className="token grey">{s.on ? "On" : "Off"}</span>}
      </div>
      <p className="small muted">{s.what}</p>
      <div className="row" style={{ gap: 6 }}>{s.tools.map((t) => <span key={t.name} className="token grey" title={t.description}>{t.label}</span>)}</div>
      {s.key !== "chief" && <Hired roles={s.roles || []} owner={owner} />}
      {s.customer_facing && (
        owner ? (
          <label className="field"><span className="label">Messages to customers</span>
            <select className="select" value={s.autonomy} disabled={!canActAlone || !s.on} onChange={(e) => patch({ autonomy: e.target.value })}>
              <option value="ask">Ask me first</option>
              <option value="act">Send on their own</option>
            </select>
          </label>
        ) : <span className="small muted">{s.autonomy === "act" ? "Messages customers on their own" : "Asks before messaging customers"}</span>
      )}
      {s.customer_facing && <span className="small muted">Money messages always wait for you.</span>}
      <span className="small muted">{s.actions_this_week} action{s.actions_this_week === 1 ? "" : "s"} this week</span>
    </div>
  );
}

function Standup({ standup, owner, reload }) {
  const c = standup?.content;
  return (
    <div className="panel">
      <div className="row between">
        <span className="label">Morning standup{standup ? ` · ${ago(standup.created_at)}` : ""}</span>
        {owner && <Busy className="btn sm ghost ai" run={async () => { await api("/office/standup", { method: "POST" }); reload(); }}>Write today's standup</Busy>}
      </div>
      {!c ? <p className="small muted">Your Chief of Staff writes one every morning at 9:30.</p> : (
        <>
          <h3>{c.headline}</h3>
          <div className="grid3">
            {[["Yesterday", c.yesterday], ["Today", c.today], ["Needs you", c.needs_you]].map(([label, items]) => (
              <div key={label} className="stack" style={{ gap: 6 }}>
                <span className="label">{label}</span>
                {(items || []).length ? items.map((x, i) => <span key={i} className="small">• {x}</span>) : <span className="small muted">Nothing.</span>}
              </div>
            ))}
          </div>
        </>
      )}
    </div>
  );
}

function OfficeInner() {
  const { me, refresh } = useHub();
  const [data, reload] = useLoad("/office");
  const [text, setText] = useState("");
  const [current, setCurrent] = useState(null);
  const timer = useRef(null);
  useEffect(() => () => clearTimeout(timer.current), []);
  useEffect(() => { if (data && !current && data.runs[0]) setCurrent(data.runs[0]); }, [data]);
  const follow = (run) => {
    setCurrent(run);
    clearTimeout(timer.current);
    if (run.status === "running") timer.current = setTimeout(async () => follow(await api(`/office/runs/${run.id}`)), 1500);
    else { reload(); refresh(); }
  };
  if (!data) return null;
  const owner = canAct(me, "owner");
  return (
    <Sheet kicker="AI team · AI office" id="AG-02" pillar="Scale" title={<>Your AI office, <em>at work</em>.</>}
      sub="Tell your Chief of Staff what you need, in plain words. The office plans it across five departments, does the work in your hub, and asks before it messages a customer. Money messages always wait for you.">
      <div className="panel active">
        <span className="label">Give the office an instruction</span>
        <textarea className="textarea" rows={3} value={text} maxLength={1500} onChange={(e) => setText(e.target.value)}
          placeholder="e.g. Follow up with every lead that's still waiting, and remind customers who owe us money" aria-label="Instruction" />
        <div className="row" style={{ gap: 6 }}>{EXAMPLES.map((x) => <button key={x} className="btn sm ghost chip" onClick={() => setText(x)}>{x}</button>)}</div>
        <div className="row">
          <Busy className="btn primary ai" disabled={text.trim().length < 5} run={async () => { const run = await api("/office/runs", { method: "POST", body: { instruction: text } }); setText(""); follow(run); }}>Give instruction</Busy>
          <span className="small muted">Planning uses one AI token; each step that writes something uses one more.</span>
        </div>
      </div>
      {current && <Run run={current} />}
      <div className="row between">
        <span className="label">Chief of Staff and five departments</span>
        <span className="small muted">
          {data.hired || 0} of {PER_DEPARTMENT * 5} AI staff hired{owner ? <> · <Link to="/workforce">{data.hired ? "Manage your workforce" : "Pick your workforce"}</Link></> : ""}
        </span>
      </div>
      <div className="grid3">{data.staff.map((s) => <StaffCard key={s.key} s={s} owner={owner} canActAlone={data.can_act} reload={reload} />)}</div>
      <Standup standup={data.standup} owner={owner} reload={reload} />
      {data.runs.length > 1 && (
        <div className="stack">
          <span className="label">Earlier instructions</span>
          <div className="listbox">
            {data.runs.map((r) => (
              <button key={r.id} className={current?.id === r.id ? "on" : ""} onClick={() => follow(r)}>
                <span>{r.instruction}</span><span className="small muted">{RUN_LABEL[r.status] || r.status} · {ago(r.created_at)}</span>
              </button>
            ))}
          </div>
        </div>
      )}
    </Sheet>
  );
}

export default function Office() {
  return <OfficeInner />;
}
