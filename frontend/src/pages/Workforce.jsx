import React, { useState } from "react";
import { Link } from "react-router-dom";
import { ago, api } from "../api.js";
import { Busy, Field, FormError, Modal, Sheet, Stat, canAct, useHub, useLoad } from "../ui.jsx";
import { Loading, Pill, Tabs, ToolOutput } from "../v2.jsx";
import "./growth.css";

const OUTPUT = {
  draft: "A written draft for you", message: "Messages ready to send", reply: "Replies for customer chats", calendar: "Meeting booking messages",
  quote: "Quotation lines (the hub works out tax)", briefing: "Your morning briefing", recovery: "Payment reminders", invoice: "Invoice lines (the hub works out tax)",
};
const CONN = { email: "your email", messaging: "your WhatsApp number", calendar: "your calendar", accounting: "Tally / your accounts" };

function Job({ r }) {
  const { refresh } = useHub();
  const [brief, setBrief] = useState("");
  const [out, setOut] = useState(null);
  const [err, setErr] = useState("");
  const [past, reloadPast] = useLoad(`/tools/role:${r.id}/history`);
  const [view, setView] = useState(null);
  return (
    <div className="stack" style={{ borderTop: "1px solid var(--ink-line)", paddingTop: 14 }}>
      <Field label={r.ask}>
        <textarea className="textarea" rows={4} maxLength={6000} value={brief} onChange={(e) => setBrief(e.target.value)} />
      </Field>
      <FormError msg={err} />
      <div className="row">
        <Busy className="btn primary ai" disabled={brief.trim().length < 2} onError={setErr} run={async () => {
          const res = await api(`/tools/role:${r.id}/run`, { method: "POST", body: { inputs: { brief: brief.trim() } } });
          setOut(res.output);
          setView(null);
          refresh();
          reloadPast();
        }}>Start the job</Busy>
        <span className="small muted">Uses one AI run. The draft is also saved in My outputs.</span>
      </div>
      {out && <ToolOutput out={out} />}
      {past?.length > 0 && (
        <div className="stack">
          <span className="label">Earlier work</span>
          <div className="listbox">
            {past.slice(0, 8).map((h) => (
              <button key={h.id} className={view?.id === h.id ? "on" : ""} onClick={() => { setView(view?.id === h.id ? null : h); setOut(null); }}>
                <span style={{ overflowWrap: "anywhere" }}>{h.title}</span><span className="small muted">{ago(h.created_at)}</span>
              </button>
            ))}
          </div>
          {view?.content && <ToolOutput out={view.content} />}
        </div>
      )}
    </div>
  );
}

function RoleCard({ r, owner, open, onToggle, onChange }) {
  const { toast } = useHub();
  const move = async (path, msg) => { onChange(await api(`/workforce/${path}`, { method: "POST", body: { role_id: r.id } })); toast(msg); };
  return (
    <div className={`wf-role ${r.hired ? "on" : ""} ${open ? "wide" : ""}`}>
      <div className="row between" style={{ alignItems: "flex-start", flexWrap: "nowrap" }}>
        <h3 style={{ minWidth: 0 }}>{r.name}</h3>
        {r.hired && <Pill kind="on">Hired</Pill>}
      </div>
      <p className="small muted">{r.summary}</p>
      {r.hero && (
        <div className="row" style={{ gap: 8 }}>
          <Pill kind="on">Fully wired</Pill>
          {r.route && <Link className="small" to={r.route}>Open its page</Link>}
        </div>
      )}
      <div className="wf-meta">
        <span>Makes: <b>{OUTPUT[r.output] || OUTPUT.draft}</b></span>
        <span>{r.conn.length ? <>Works through <b>{r.conn.map((c) => CONN[c] || c).join(" and ")}</b> (it drafts without it)</> : "Needs nothing connected"}</span>
        {r.risk === "financial" ? <span className="money">Money: always waits for your approval</span>
          : r.risk === "customer" ? <span>Anything a customer sees <b>waits for your approval</b></span>
          : <span>Drafts for you and your team only</span>}
        <span>Saves about {r.minutes} min a job{r.drafts ? ` · ${r.drafts} ${r.drafts === 1 ? "job" : "jobs"} done` : ""}</span>
      </div>
      <div className="row">
        {r.hired && <button className={`btn sm ${open ? "ghost" : "primary"}`} onClick={onToggle}>{open ? "Close" : "Give it a job"}</button>}
        {owner && (r.hired
          ? <Busy className="btn sm ghost" run={() => move("release", `${r.name} released`)}>Release</Busy>
          : <Busy className="btn sm" run={() => move("hire", `${r.name} hired`)}>Hire</Busy>)}
      </div>
      {open && r.hired && <Job r={r} />}
    </div>
  );
}

export default function Workforce() {
  const { me, toast } = useHub();
  const [loaded] = useLoad("/workforce");
  const [fresh, setFresh] = useState(null);
  const [tab, setTab] = useState("marketing");
  const [job, setJob] = useState(null);
  const [confirm, setConfirm] = useState(false);
  const [onlyHired, setOnlyHired] = useState(false);
  const owner = canAct(me, "owner");
  const w = fresh || loaded;

  const title = !w ? <>Your AI <em>workforce</em>.</> : w.hired ? <>{w.hired} AI staff at <em>work</em>.</> : <>Pick your AI <em>workforce</em>.</>;
  const dept = w?.departments.find((d) => d.id === tab) || w?.departments[0];
  const wired = w ? w.departments.flatMap((d) => d.roles).filter((r) => r.hero).length : 0;

  return (
    <Sheet kicker="AI team · Workforce" id="LW-01" pillar="Scale" title={title}
      sub={w ? `Up to ${w.per_department} AI staff in each of ${w.departments.length} departments — ${w.max} in all — picked from ${w.total_roles} roles. Swap any of them any time.`
        : "Up to 6 AI staff in each of 5 departments, picked from 82 roles."}>
      {!w ? <Loading /> : (
        <>
          <div className="statgrid">
            <Stat label="Hired" value={`${w.hired} of ${w.max}`} />
            <Stat label="Roles to pick from" value={w.total_roles} />
            <Stat label="Fully wired" value={wired} note="work inside your hub's own pages" />
          </div>
          <p className="small muted">Hired staff also take jobs from your Chief of Staff in the <Link to="/office">AI office</Link>. Anything a customer
            sees waits for your approval, and money messages always wait for you.</p>
          {owner && (
            <div className="row">
              <button className="btn" onClick={() => setConfirm(true)}>Start with the recommended {w.max}</button>
              <span className="small muted">Six in each department, including every fully wired role.</span>
            </div>
          )}
          <Tabs tabs={w.departments.map((d) => [d.id, `${d.name} · ${d.hired}/${w.per_department}`])} value={dept.id} onChange={(t) => { setTab(t); setJob(null); }} />
          <div className="row between">
            <p className="sub">{dept.blurb}.</p>
            <span className="small muted">{dept.hired} of {w.per_department} hired{dept.hired >= w.per_department ? " — release one to hire another" : ""}</span>
          </div>
          <div className="row" role="group" aria-label="Which roles to show">
            <button className={`btn sm ${onlyHired ? "ghost" : ""}`} aria-pressed={!onlyHired} onClick={() => setOnlyHired(false)}>All {dept.roles.length} roles</button>
            <button className={`btn sm ${onlyHired ? "" : "ghost"}`} aria-pressed={onlyHired} onClick={() => setOnlyHired(true)}>Hired ({dept.hired})</button>
          </div>
          {onlyHired && !dept.hired && <p className="small muted">Nobody hired in {dept.name} yet. Show all roles and pick up to {w.per_department}.</p>}
          <div className="wf-grid">
            {dept.roles.filter((r) => !onlyHired || r.hired).map((r) => (
              <RoleCard key={r.id} r={r} owner={owner} open={job === r.id} onChange={setFresh}
                onToggle={() => setJob(job === r.id ? null : r.id)} />
            ))}
          </div>
          {confirm && (
            <Modal onClose={() => setConfirm(false)}>
              <span className="kicker">Recommended workforce</span>
              <h2>Start with the recommended <em>{w.max}</em>?</h2>
              <p className="sub">Six AI staff in each department, including the {wired} that are fully wired into your hub.
                {w.hired ? ` This replaces the ${w.hired} you've picked now.` : ""} You can swap any of them later.</p>
              <div className="row">
                <Busy className="btn primary" run={async () => {
                  setFresh(await api("/workforce/recommended", { method: "POST" }));
                  setConfirm(false);
                  setJob(null);
                  toast(`${w.max} AI staff hired`);
                }}>Hire the recommended {w.max}</Busy>
                <button className="btn ghost" onClick={() => setConfirm(false)}>Not now</button>
              </div>
            </Modal>
          )}
        </>
      )}
    </Sheet>
  );
}
