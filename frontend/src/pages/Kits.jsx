import React, { useCallback, useEffect, useRef, useState } from "react";
import VoiceNote from "../components/VoiceNote.jsx";
import KitPage from "../kit.jsx";
import { ago, api } from "../api.js";
import { Busy, Field, FormError, Gate, Modal, Sheet, Switch, canAct, useHub } from "../ui.jsx";
import { Loading, Pill } from "../v2.jsx";
import "./sops.css";

const FUNCTIONS = ["Marketing", "Sales", "Operations", "Product development", "Accounts and finance", "People (HR)", "Management"];

/* ═════════════════════════ SOPs: the library, scenario SOPs and the whole-business set (v2.1, 3.10) ═════════════════════════ */
const SOP_FUNCTIONS = ["Sales", "Marketing", "Operations/Delivery", "Accounts", "HR", "Customer care"];
const SOP_FIELDS = [
  { key: "title", label: "Title" }, { key: "function", label: "Function", type: "select", options: [["", "—"], ...SOP_FUNCTIONS.map((f) => [f, f])] },
  { key: "purpose", label: "Purpose", type: "textarea", rows: 2 }, { key: "owner_role", label: "Who does it" },
  { key: "trigger", label: "What starts it", hint: "For a situation: what happens that means this SOP is needed." },
  { key: "when", label: "When to use it" },
  { key: "steps", label: "Steps", type: "objlist", item: "step", fields: [{ key: "title", label: "Step" }, { key: "detail", label: "How", type: "textarea", rows: 2 }] },
  { key: "scripts", label: "What to say", type: "objlist", item: "message", fields: [{ key: "label", label: "For" }, { key: "text", label: "Words to use", type: "textarea", rows: 3 }] },
  { key: "done_when", label: "Done when", type: "textarea", rows: 2 },
  { key: "escalation", label: "When to bring someone in", type: "textarea", rows: 2 },
  { key: "checklist", label: "Checklist", type: "list" }, { key: "mistakes", label: "Mistakes to avoid", type: "list" },
];
const SOP_HEAD = { kicker: "Scale · SOP library", id: "SC-04", pillar: "Scale", title: <>Write it once. Done the same <em>way</em>.</> };
const SOP_ABOUT = "Describe a process or a situation in a line, or talk it through in a voice note. The AI turns it into steps anyone on your team can follow. Or map your whole business and write every SOP in one go.";

/** "This is a situation, not a process": a scenario SOP (what starts it, the steps, the words to use, done, escalation). */
function ScenarioToggle({ set }) {
  const [on, setOn] = useState(false);
  return (
    <div className="stack" style={{ gap: 6 }}>
      <Switch checked={on} label="This is a situation, not a process"
        onChange={(v) => { setOn(v); set((x) => ({ ...x, mode: v ? "scenario" : "process" })); }} />
      {on && <span className="small muted">Describe the situation in the first box, like "A customer says delivery is late". You get what starts it, the steps, the words to use, what done looks like and when to bring someone in.</span>}
    </div>
  );
}

function Versions({ doc, onClose, onRestored }) {
  const { me, toast } = useHub();
  const [list, setList] = useState(null);
  const [err, setErr] = useState("");
  useEffect(() => { api(`/kits/sop/${doc.id}/versions`).then(setList).catch((e) => setErr(e.message)); }, [doc.id]);
  return (
    <Modal onClose={onClose}>
      <span className="kicker">SOP · Versions</span>
      <h2>Earlier <em>versions</em></h2>
      <p className="sub">Each time this SOP is saved, the version before is kept here. Restoring one keeps today's version too.</p>
      <FormError msg={err} />
      {!list ? (!err && <Loading />) : !list.length ? <p className="muted">No earlier versions yet.</p> : (
        <div className="stack sop-versions">
          {list.map((v) => (
            <div key={v.id} className="panel flat sop-version">
              <div className="row between">
                <b>{v.title}</b>
                <span className="small muted">{ago(v.at)}</span>
              </div>
              <span className="small muted">{(v.content?.steps || []).length} step{(v.content?.steps || []).length === 1 ? "" : "s"}{(v.content?.scripts || []).length ? ` · ${v.content.scripts.length} message${v.content.scripts.length === 1 ? "" : "s"}` : ""}</span>
              {canAct(me, "manager") && (
                <div className="row"><Busy className="btn sm" run={async () => {
                  await api(`/kits/sop/${doc.id}/versions/${v.id}/restore`, { method: "POST" });
                  toast("Restored");
                  onClose();
                  onRestored();
                }}>Restore this version</Busy></div>
              )}
            </div>
          ))}
        </div>
      )}
      <button className="btn ghost" onClick={onClose}>Close</button>
    </Modal>
  );
}

function Assign({ doc, assigned, onClose, onSaved }) {
  const { me, toast } = useHub();
  const [role, setRole] = useState(assigned?.role || doc.content?.owner_role || "");
  const [who, setWho] = useState(assigned?.user_id || "");
  const [people, setPeople] = useState(null);
  const [err, setErr] = useState("");
  const team = !!me?.features?.team_logins && !!(me?.features?.tasks || me?.features?.meetings);
  useEffect(() => { if (team) api("/team/people").then((p) => setPeople(p.filter((x) => x.role !== "owner"))).catch(() => setPeople([])); }, [team]);
  const save = async () => {
    const r = await api(`/kits/sop/${doc.id}/assign`, { method: "PUT", body: { role: role.trim(), user_id: who || null } });
    onSaved(r.assigned || null);
    toast("Assigned");
    onClose();
  };
  return (
    <Modal onClose={onClose}>
      <span className="kicker">SOP · Assign</span>
      <h2>Who follows <em>this</em>?</h2>
      <Field label="Role" hint="e.g. Sales executive, Store manager">
        <input className="input" maxLength={60} value={role} onChange={(e) => setRole(e.target.value)} />
      </Field>
      {team ? (
        <Field label="Team member (optional)">
          {!people ? <Loading /> : (
            <select className="select" value={who} onChange={(e) => setWho(e.target.value)}>
              <option value="">Nobody in particular</option>
              {people.map((p) => <option key={p.id} value={p.id}>{p.name} · {p.role}</option>)}
            </select>
          )}
        </Field>
      ) : <p className="small muted">From Growth Mentorship you can give it to a team member by name.</p>}
      <FormError msg={err} />
      <div className="row">
        <Busy className="btn primary" run={save} onError={setErr}>Save</Busy>
        <button className="btn ghost" onClick={onClose}>Cancel</button>
      </div>
    </Modal>
  );
}

/** Buttons on each SOP: who follows it, print, versions, assign. */
function SopActions({ doc, onRestored }) {
  const { me } = useHub();
  const [modal, setModal] = useState(null);
  const [assigned, setAssigned] = useState(doc.assigned || null);
  return (
    <>
      {(assigned?.name || assigned?.role) && <Pill kind="on">Followed by {assigned.name || assigned.role}</Pill>}
      {doc.content?.function && <Pill>{doc.content.function}</Pill>}
      <a className="btn sm ghost" href={`/api/kits/sop/${doc.id}/print`} target="_blank" rel="noreferrer">Print / PDF</a>
      <button className="btn sm ghost" onClick={() => setModal("versions")}>Versions</button>
      {canAct(me, "manager") && <button className="btn sm ghost" onClick={() => setModal("assign")}>Assign</button>}
      {modal === "versions" && <Versions doc={doc} onClose={() => setModal(null)} onRestored={onRestored} />}
      {modal === "assign" && <Assign doc={doc} assigned={assigned} onClose={() => setModal(null)} onSaved={setAssigned} />}
    </>
  );
}

const clean = (procs) => procs.map(({ key, function: fn, title, selected }) => ({ key: key || null, function: fn, title: title.trim(), selected: !!selected }));

/** The whole business, mapped: every process by function, ticked, then written in a batch (2 AI tokens each). */
function SopSet({ onWritten }) {
  const { me, refresh, toast } = useHub();
  const [d, setD] = useState(null);
  const [procs, setProcs] = useState([]);
  const [dirty, setDirty] = useState(false);
  const [editing, setEditing] = useState(false);
  const [add, setAdd] = useState({ function: SOP_FUNCTIONS[0], title: "" });
  const [err, setErr] = useState("");
  const canWrite = canAct(me, "manager");
  const load = useCallback(() => api("/sop-set").then((x) => { setD(x); return x; }).catch((e) => setErr(e.message)), []);
  useEffect(() => { load(); }, [load]);
  useEffect(() => { if (d && !dirty) setProcs(d.processes || []); }, [d]); // eslint-disable-line react-hooks/exhaustive-deps
  const running = !!d?.batch?.running;
  useEffect(() => {
    if (!running) return undefined;
    const t = setInterval(load, 3000);
    return () => clearInterval(t);
  }, [running, load]);
  const wasRunning = useRef(false);
  useEffect(() => {
    if (wasRunning.current && !running && d?.batch) {
      refresh();
      onWritten?.();
      toast(`${d.batch.done} SOP${d.batch.done === 1 ? "" : "s"} written${d.batch.failed ? `, ${d.batch.failed} couldn't be written (their tokens were refunded)` : ""}`);
    }
    wasRunning.current = running;
  }, [d]); // eslint-disable-line react-hooks/exhaustive-deps

  if (!d) return err ? <FormError msg={err} /> : <Loading />;
  const change = (next) => { setProcs(next); setDirty(true); };
  const toWrite = procs.filter((p) => p.selected && !p.sop_id);
  const runs = toWrite.length * (d.runs_per_sop || 2);
  const left = me?.runs?.left ?? d.runs_left ?? 0;
  const short = runs > left;
  const saveMap = async () => {
    const r = await api("/sop-set/map", { method: "PUT", body: { processes: clean(procs) } });
    setDirty(false); setD(r); setProcs(r.processes || []);
    return r;
  };
  const map = async () => {
    if (d.exists && !window.confirm("Map your processes again from your Knowledge Hub? SOPs you've already written stay. Uses one AI token.")) return;
    const r = await api("/sop-set/map", { method: "POST" });
    setDirty(false); setEditing(false); setD(r); setProcs(r.processes || []);
    refresh();
  };
  const write = async () => {
    const r = dirty ? await saveMap() : d;
    const keys = (r.processes || []).filter((p) => p.selected && !p.sop_id).map((p) => p.key);
    if (!keys.length) throw new Error("Tick the processes you want SOPs for.");
    await api("/sop-set/write", { method: "POST", body: { keys } });
    toast(`Writing ${keys.length} SOP${keys.length === 1 ? "" : "s"} — you can leave this page`);
    wasRunning.current = true;   // a short batch can finish before the first look; still say it's done
    await load();
  };

  if (!d.exists) {
    return (
      <div className="panel active">
        <h3>Map every process in your business</h3>
        <p className="muted">One AI token reads your Knowledge Hub and lists the processes a business like yours runs — sales, marketing, delivery, accounts, HR and customer care. You tick the ones that apply, then the hub writes an SOP for each.</p>
        <FormError msg={err} />
        {canWrite ? <div className="row"><Busy className="btn primary ai" run={map} onError={setErr}>Map my processes</Busy><span className="small muted">Uses one AI token.</span></div>
          : <p className="small muted">Ask the business owner or a manager to map your processes.</p>}
      </div>
    );
  }
  const b = d.batch;
  return (
    <div className="stack">
      <div className="row between sop-set-bar">
        <span className="small muted">{d.without_sop} of {d.processes.length} processes don't have an SOP yet.</span>
        {canWrite && !running && (
          <span className="row" style={{ gap: 8 }}>
            <button className="btn sm ghost" onClick={() => setEditing(!editing)}>{editing ? "Done editing" : "Edit the list"}</button>
            <Busy className="btn sm ghost ai" run={map}>Map again</Busy>
          </span>
        )}
      </div>

      {b && (
        <div className={`panel ${b.running ? "active" : "flat"} sop-batch`}>
          {b.running ? (
            <>
              <span className="label">Writing your SOPs</span>
              <b>{b.done + b.failed} of {b.total} done{b.failed ? ` · ${b.failed} couldn't be written` : ""}</b>
              <div className="meter" role="progressbar" aria-valuemin={0} aria-valuemax={b.total} aria-valuenow={b.done + b.failed}>
                <i style={{ width: `${Math.round(((b.done + b.failed) / Math.max(b.total, 1)) * 100)}%` }} />
              </div>
              <span className="small muted">Each SOP takes two AI tokens: the steps, then the words to use. You can leave this page.</span>
            </>
          ) : (
            <span className="small muted">
              Last batch{b.finished_at ? ` (${ago(b.finished_at)})` : ""}: {b.done} written{b.failed ? `, ${b.failed} couldn't be written — their tokens were refunded, so you can try them again` : ""}.
            </span>
          )}
        </div>
      )}

      {SOP_FUNCTIONS.map((fn) => {
        const rows = procs.map((p, i) => [p, i]).filter(([p]) => p.function === fn);
        if (!rows.length && !editing) return null;
        return (
          <div key={fn} className="panel sop-fn">
            <div className="row between"><h3>{fn}</h3><span className="small muted">{rows.filter(([p]) => p.sop_id).length} of {rows.length} written</span></div>
            {rows.map(([p, i]) => (
              <div key={p.key || `new-${i}`} className={`sop-proc ${p.sop_id ? "done" : ""}`}>
                <input type="checkbox" aria-label={`Write an SOP for ${p.title}`} checked={!!p.selected || !!p.sop_id} disabled={!canWrite || !!p.sop_id || running}
                  onChange={(e) => change(procs.map((x, j) => (j === i ? { ...x, selected: e.target.checked } : x)))} />
                <div className="sop-proc-main">
                  {editing && !p.sop_id ? (
                    <input className="input" maxLength={120} value={p.title} aria-label="Process name"
                      onChange={(e) => change(procs.map((x, j) => (j === i ? { ...x, title: e.target.value } : x)))} />
                  ) : <span className="sop-proc-title">{p.title}</span>}
                  {p.why && !editing && <span className="small muted">{p.why}</span>}
                </div>
                <span className="row sop-proc-tags">
                  {p.sop_id ? <Pill kind="on">Written</Pill> : p.status === "failed" ? <Pill kind="warn">Try again</Pill> : null}
                  {editing && !p.sop_id && <button className="btn sm ghost" onClick={() => change(procs.filter((_, j) => j !== i))}>Remove</button>}
                </span>
              </div>
            ))}
          </div>
        );
      })}

      {editing && (
        <div className="panel flat">
          <span className="label">Add a process</span>
          <div className="sop-add">
            <select className="select" value={add.function} onChange={(e) => setAdd({ ...add, function: e.target.value })}>
              {SOP_FUNCTIONS.map((f) => <option key={f} value={f}>{f}</option>)}
            </select>
            <input className="input" maxLength={120} placeholder="e.g. Taking an order on WhatsApp" value={add.title} onChange={(e) => setAdd({ ...add, title: e.target.value })} />
            <button className="btn sm" disabled={add.title.trim().length < 3}
              onClick={() => { change([...procs, { key: null, function: add.function, title: add.title.trim(), selected: true, sop_id: null, why: "" }]); setAdd({ ...add, title: "" }); }}>Add</button>
          </div>
        </div>
      )}

      <FormError msg={err} />
      {canWrite && !running && procs.some((p) => !p.sop_id) && (
        <div className="row" style={{ gap: 8 }}>
          <button className="btn sm ghost" onClick={() => change(procs.map((p) => (p.sop_id ? p : { ...p, selected: true })))}>Tick all</button>
          <button className="btn sm ghost" onClick={() => change(procs.map((p) => (p.sop_id ? p : { ...p, selected: false })))}>Untick all</button>
        </div>
      )}
      {canWrite && (
        <div className="panel active sop-write">
          {dirty && <div className="row"><Busy className="btn sm" run={saveMap} onError={setErr}>Save the list</Busy><span className="small muted">You have changes that aren't saved.</span></div>}
          <b>{toWrite.length ? `Write ${toWrite.length} SOP${toWrite.length === 1 ? "" : "s"}` : "Tick the processes you want SOPs for"}</b>
          {toWrite.length > 0 && (
            <span className={`small ${short ? "sop-short" : "muted"}`}>
              That uses {runs} AI tokens ({d.runs_per_sop || 2} for each SOP). You have {left} left this month.
              {short ? " Untick a few, or wait for next month's tokens." : " A token that fails is refunded."}
            </span>
          )}
          <div className="row">
            <Busy className="btn primary ai" disabled={!toWrite.length || short || running} run={write} onError={setErr}>
              {running ? "Writing…" : toWrite.length ? `Write ${toWrite.length} SOP${toWrite.length === 1 ? "" : "s"}` : "Write SOPs"}
            </Busy>
          </div>
        </div>
      )}
    </div>
  );
}

function SopTabs({ tab, setTab, without }) {
  return (
    <span className="tabs sop-tabs" role="tablist">
      <button role="tab" aria-selected={tab === "library"} className={tab === "library" ? "on" : ""} onClick={() => setTab("library")}>Library</button>
      <button role="tab" aria-selected={tab === "set"} className={tab === "set" ? "on" : ""} onClick={() => setTab("set")}>
        Whole business{without ? ` · ${without} to write` : ""}
      </button>
    </span>
  );
}

export function Sops() {
  const { me } = useHub();
  const [tab, setTab] = useState("library");
  const [version, setVersion] = useState(0);   // a new key remounts the library, so a restored or new SOP shows
  const [without, setWithout] = useState(null);
  const on = !!me?.features?.sop_library;
  useEffect(() => {
    if (on) api("/sop-set").then((x) => setWithout(x.exists ? x.without_sop : null)).catch(() => {});
  }, [on, tab, version]);
  const sub = on ? <>{SOP_ABOUT}<SopTabs tab={tab} setTab={setTab} without={without} /></> : SOP_ABOUT;
  if (on && tab === "set") {
    return (
      <Gate feature="sop_library" role="staff" kicker={SOP_HEAD.kicker} id={SOP_HEAD.id} what={SOP_ABOUT}>
        <Sheet {...SOP_HEAD} sub={sub}><SopSet onWritten={() => setVersion((v) => v + 1)} /></Sheet>
      </Gate>
    );
  }
  return (
    <KitPage key={version} kind="sop" feature="sop_library" readRole="staff" writeRole="manager" {...SOP_HEAD} sub={sub} lockedWhat={SOP_ABOUT}
      cta="Write the SOP" empty="No SOPs yet. Start with the job you explain most often — or map your whole business."
      inputs={[{ key: "process", label: "Which process or situation?", placeholder: "Taking a new order on WhatsApp", required: true },
               { key: "notes", label: "How you do it today (optional)", type: "textarea", placeholder: "Type the steps the way you'd explain them to a new person." }]}
      inputsExtra={(set) => <><ScenarioToggle set={set} /><VoiceNote onText={(t) => set((v) => ({ ...v, notes: (v.notes ? v.notes + "\n" : "") + t }))} /></>}
      extraActions={(doc) => <SopActions doc={doc} onRestored={() => setVersion((v) => v + 1)} />}
      fields={SOP_FIELDS} />
  );
}

export function Hiring() {
  return (
    <KitPage kind="jd" feature="hiring" readRole="manager" writeRole="manager" kicker="Scale · Hiring" id="SC-07" pillar="Scale"
      title={<>Hire for the role, not the <em>rush</em>.</>}
      sub="Name the role. Get the job post, interview questions with what to listen for, and a scorecard to compare candidates."
      cta="Write the hiring kit" empty="No hiring kits yet."
      inputs={[{ key: "role", label: "Role", placeholder: "Sales executive", required: true },
               { key: "notes", label: "Anything specific (optional)", type: "textarea", placeholder: "Two-wheeler needed, Gujarati speaking, 10am–7pm" }]}
      fields={[{ key: "role", label: "Role" }, { key: "summary", label: "Job post summary", type: "textarea" },
               { key: "responsibilities", label: "Responsibilities", type: "list" }, { key: "requirements", label: "Requirements", type: "list" },
               { key: "kpis", label: "How success is measured", type: "list" },
               { key: "interview_questions", label: "Interview questions", type: "objlist", item: "question", fields: [{ key: "q", label: "Question" }, { key: "look_for", label: "Listen for" }] },
               { key: "scorecard", label: "Scorecard", type: "objlist", item: "criterion", fields: [{ key: "criterion", label: "Criterion" }, { key: "weight", label: "Weight (1–5)", type: "number", min: 1, max: 5 }] },
               { key: "salary_note", label: "Salary note", hint: "Left empty unless you add it." }]} />
  );
}

export function Decisions() {
  return (
    <KitPage kind="decision" feature="decision_log" readRole="owner" writeRole="owner" kicker="Structure · Decision log" id="ST-05" pillar="Structure"
      title={<>Automate, delegate or <em>keep</em>.</>}
      sub="List what you personally do every week. The AI sorts each task and gives you the next step. Track them to done."
      cta="Sort my tasks" empty="List your weekly tasks to start your decision log."
      inputs={[{ key: "tasks", label: "Tasks you do yourself, one per line", type: "textarea", rows: 8, required: true,
                 placeholder: "Reply to every WhatsApp inquiry\nMake quotations\nCollect payments\nPost on Instagram\nCheck stock" }]}
      fields={[{ key: "items", label: "Decisions", type: "objlist", item: "task", fields: [
        { key: "task", label: "Task" },
        { key: "verdict", label: "Decision", type: "select", options: [["automate", "Automate"], ["delegate", "Delegate"], ["keep", "Keep"]] },
        { key: "reason", label: "Why" }, { key: "next_step", label: "Next step" },
        { key: "status", label: "Status", type: "select", options: [["todo", "To do"], ["doing", "Doing"], ["done", "Done"]] }] }]} />
  );
}

export function Roles() {
  return (
    <KitPage kind="role" feature="role_clarity" readRole="staff" writeRole="owner" kicker="Structure · Role clarity" id="ST-06" pillar="Structure"
      title={<>Everyone can answer at <em>3am</em>.</>}
      sub="One page per role: what it's for, three or four responsibilities, the tasks for each in order, and how it's measured. Draft it, review it with the person, save each version."
      cta="Write the role" empty="No roles yet. Start with the person who handles your customers."
      inputs={[{ key: "role", label: "Role", placeholder: "Telecaller", required: true }, { key: "person_name", label: "Person in this role (optional)" },
               { key: "function", label: "Business function", type: "select", options: FUNCTIONS },
               { key: "notes", label: "What they do today (optional)", type: "textarea" }]}
      fields={[{ key: "title", label: "Role title" }, { key: "person_name", label: "Person" },
               { key: "function", label: "Function", type: "select", options: FUNCTIONS.map((f) => [f, f]) },
               { key: "level", label: "Level", type: "select", options: [["creator", "Creator — sets goals and strategy"], ["manager", "Manager — plans and reviews"], ["doer", "Doer — executes"]] },
               { key: "definition", label: "Role definition", type: "textarea", rows: 2 },
               { key: "responsibilities", label: "Responsibilities (max 4)", type: "objlist", item: "responsibility", fields: [{ key: "name", label: "Responsibility" }, { key: "tasks", label: "Tasks, in order", type: "list" }] },
               { key: "metrics", label: "How it's measured", type: "list" }, { key: "reports_to", label: "Reports to" },
               { key: "version", label: "Version", type: "number", min: 1, max: 20, hint: "1 self · 2 team feedback · 3 manager · 4 owner" }]} />
  );
}

const HEADINGS = [["relationships", "When relationships turn resistant"], ["energy", "When energy goes down"], ["commitment", "When commitment becomes conditional"], ["performance", "When performance is full of reasons"]];

export function Culture() {
  return (
    <KitPage kind="culture" feature="culture_plan" readRole="staff" writeRole="owner" kicker="Structure · Culture charter" id="ST-07" pillar="Structure"
      title={<>Our way. Not our <em>way</em>.</>}
      sub="List the situations where things go wrong at work. For each, agree how your team handles it — and how it doesn't. Put it on the wall."
      cta="Draft our charter" empty="No charter yet."
      inputs={[{ key: "notes", label: "Situations you've seen (optional)", type: "textarea", rows: 6, placeholder: "People gossip about each other\nDeliveries get delayed and nobody tells the customer\nThe team is idle in slow weeks" }]}
      fields={[{ key: "name", label: "Name of your way" }, { key: "purpose", label: "In one line", type: "textarea", rows: 2 },
               { key: "situations", label: "Situations", type: "objlist", item: "situation", fields: [
                 { key: "heading", label: "Heading", type: "select", options: HEADINGS }, { key: "situation", label: "Situation" },
                 { key: "our_way", label: "Our way", type: "textarea", rows: 2 }, { key: "not_our_way", label: "Not our way", type: "textarea", rows: 2 }] }]} />
  );
}

export function Competence() {
  return (
    <KitPage kind="competence" feature="competence_plan" readRole="staff" writeRole="owner" kicker="Structure · Competence" id="ST-08" pillar="Structure"
      title={<>Grow the people, grow the <em>results</em>.</>}
      sub="For each role: the skills, knowledge, self-image, traits and motives it needs — and a plan to build them from people, resources and experiences."
      cta="Write the plan" empty="No competence plans yet."
      inputs={[{ key: "role", label: "Role", placeholder: "Site supervisor", required: true }, { key: "notes", label: "Gaps you see (optional)", type: "textarea" }]}
      fields={[{ key: "role", label: "Role" },
               { key: "attributes", label: "What the role needs", type: "group", fields: [
                 { key: "skills", label: "Skills", type: "list" }, { key: "knowledge", label: "Knowledge", type: "list" }, { key: "self_image", label: "Self-image", type: "list" },
                 { key: "traits", label: "Traits", type: "list" }, { key: "motives", label: "Motives", type: "list" }] },
               { key: "plan", label: "Development plan", type: "objlist", item: "row", fields: [
                 { key: "attribute", label: "To build" }, { key: "people", label: "People to learn from" }, { key: "resources", label: "Resources" },
                 { key: "experiences", label: "Experiences" }, { key: "by_when", label: "By when" }] }]} />
  );
}

export function Reviews() {
  const { me, toast, refresh } = useHub();
  const [version, setVersion] = React.useState(0);  // a new key remounts the list, so the new review loads and opens
  const about = "On the 1st of every month the review agent reads your leads, customers, goals, money owed and tasks, and writes an honest review with next steps.";
  const canRun = canAct(me, "owner") && me?.features?.monthly_review;
  const sub = !canRun ? about : (
    <>{about}<span style={{ display: "block", marginTop: 14 }}>
      <Busy className="btn primary ai" run={async () => {
        await api("/agents/monthly_review/run", { method: "POST" });
        toast("This month's review is ready");
        refresh();
        setVersion((v) => v + 1);
      }}>Write this month's review now</Busy>
      <span className="small muted" style={{ marginLeft: 12 }}>Uses one AI run.</span>
    </span></>
  );
  return (
    <KitPage key={version} kind="review" feature="monthly_review" readRole="manager" writeRole="owner" kicker="Scale · Monthly review" id="SC-09" pillar="Scale"
      title={<>Is the business getting <em>better</em>?</>}
      sub={sub} lockedWhat={about}
      cta="Write this month's review now" empty="Your first review arrives on the 1st. Or write one now."
      canCreate={false}
      inputs={[]}
      fields={[{ key: "headline", label: "Headline" }, { key: "numbers", label: "Numbers", type: "keyvalue" }, { key: "wins", label: "What went well", type: "list" },
               { key: "concerns", label: "Needs attention", type: "list" },
               { key: "recommendations", label: "Next steps", type: "objlist", item: "step", fields: [{ key: "action", label: "Action" }, { key: "why", label: "Why" }] },
               { key: "focus_next_month", label: "Focus next month", type: "textarea", rows: 2 }]} />
  );
}
