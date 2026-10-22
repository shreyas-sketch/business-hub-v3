import React, { useEffect, useRef, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { api, day } from "../api.js";
import { Busy, Field, Gate, Sheet, Switch, canAct, useHub, useLoad } from "../ui.jsx";
import "./team.css";

const TYPES = {
  strategic: { label: "Strategic", when: "Once a month or once a quarter",
    what: "Step back as a team: wins and setbacks, honest feedback for each person, goals until the next one, what could stop you, and the plan week by week." },
  tactical: { label: "Tactical", when: "Every week",
    what: "Check last week's goals person by person, applaud who delivered, set clear goals for the coming week, and clear what's in the way." },
};

const today = () => new Date().toLocaleDateString("en-CA"); // YYYY-MM-DD, local time

function People({ people, value, onChange, readOnly }) {
  if (readOnly) return <span className="small">{people.filter((p) => value.includes(p.id)).map((p) => p.name).join(", ") || <span className="muted">No one listed</span>}</span>;
  return (
    <div className="chips">
      {people.map((p) => {
        const on = value.includes(p.id);
        return (
          <label key={p.id} className={`chip ${on ? "on" : ""}`}>
            <input type="checkbox" checked={on} onChange={() => onChange(on ? value.filter((x) => x !== p.id) : [...value, p.id])} />
            {p.name}{p.role === "owner" ? " (owner)" : ""}
          </label>
        );
      })}
    </div>
  );
}

function NewMeeting({ type, people, onCreated, onCancel }) {
  const t = TYPES[type];
  const [v, setV] = useState({ date: today(), title: "", attendees: people.map((p) => p.id) });
  return (
    <div className="panel active">
      <span className="label">New {t.label.toLowerCase()} meeting · {t.when}</span>
      <p className="small muted">{t.what}</p>
      <div className="grid2">
        <Field label="Date"><input className="input" type="date" value={v.date} onChange={(e) => setV({ ...v, date: e.target.value })} /></Field>
        <Field label="Name (optional)"><input className="input" value={v.title} maxLength={120} placeholder={type === "strategic" ? "October review" : "Monday check-in"} onChange={(e) => setV({ ...v, title: e.target.value })} /></Field>
      </div>
      <Field label="Who's in the meeting"><People people={people} value={v.attendees} onChange={(attendees) => setV({ ...v, attendees })} /></Field>
      <div className="row">
        <Busy className="btn primary" disabled={!v.date} run={async () => onCreated(await api("/meetings", { method: "POST", body: { type, ...v } }))}>Start with the agenda</Busy>
        <button className="btn ghost" onClick={onCancel}>Cancel</button>
      </div>
    </div>
  );
}

function MeetingView({ id, people, onChanged, onDeleted }) {
  const { me, refresh, toast } = useHub();
  const editor = canAct(me, "manager");
  const [m, setM] = useState(null);
  const [notes, setNotes] = useState({});
  const [decisions, setDecisions] = useState("");
  const [saved, setSaved] = useState("");
  const dirty = useRef(new Set());
  const inflight = useRef(Promise.resolve());

  const load = (d) => { setM(d); setNotes(Object.fromEntries(d.sections.map((s) => [s.key, s.notes]))); setDecisions((d.decisions || []).join("\n")); };
  useEffect(() => { setM(null); dirty.current = new Set(); api(`/meetings/${id}`).then(load).catch((e) => { toast(e.message, "err"); onDeleted(); }); }, [id]);

  // Every save is queued, so a note saved on blur always lands before the actions agent reads the meeting.
  const patch = (body, quiet) => {
    const p = inflight.current.then(() => api(`/meetings/${id}`, { method: "PATCH", body }));
    inflight.current = p.catch(() => {});
    return p.then((d) => { if (!quiet) setSaved("Saved"); return d; }).catch((e) => { setSaved(""); toast(e.message, "err"); throw e; });
  };
  const saveNotes = () => {
    const keys = [...dirty.current];
    if (!keys.length) return inflight.current;
    dirty.current = new Set();
    setSaved("Saving…");
    return patch({ notes: Object.fromEntries(keys.map((k) => [k, notes[k] || ""])) });
  };
  const flush = async () => { await saveNotes(); await inflight.current; };
  const saveMeta = async (body) => { const d = await patch(body); setM((old) => ({ ...old, ...d, sections: old.sections })); onChanged(); };

  if (!m) return <div className="panel muted">Loading…</div>;
  const t = TYPES[m.type];
  const filled = m.sections.filter((s) => (notes[s.key] || "").trim()).length;

  return (
    <div className="stack">
      <div className="panel">
        <div className="row between">
          <span className="row"><span className="token">{t.label}</span><span className="small muted">{t.when}</span></span>
          {editor ? <Switch checked={m.status === "closed"} label="Closed" onChange={(c) => saveMeta({ status: c ? "closed" : "open" }).catch(() => {})} />
            : <span className="token grey">{m.status === "closed" ? "Closed" : "Open"}</span>}
        </div>
        {editor ? (
          <div className="grid2">
            <Field label="Name"><input className="input" value={m.title} maxLength={120} onChange={(e) => setM({ ...m, title: e.target.value })}
              onBlur={(e) => e.target.value.trim() && saveMeta({ title: e.target.value }).catch(() => {})} /></Field>
            <Field label="Date"><input className="input" type="date" value={m.date} onChange={(e) => { if (e.target.value) { setM({ ...m, date: e.target.value }); saveMeta({ date: e.target.value }).catch(() => {}); } }} /></Field>
          </div>
        ) : <div><h2>{m.title}</h2><span className="small muted">{day(m.date)}</span></div>}
        <Field label="Who's in the meeting">
          <People people={people} value={m.attendees} readOnly={!editor} onChange={(attendees) => { setM({ ...m, attendees }); saveMeta({ attendees }).catch(() => {}); }} />
        </Field>
      </div>

      <div className="row between">
        <span className="label">Agenda · {filled} of {m.sections.length} with notes</span>
        {editor && <span className="save-state" aria-live="polite">{saved}</span>}
      </div>
      {editor && <p className="small muted">Keep each part to its time. When time's up, move to the next one — the team soon learns to come prepared. Over time, let different people lead different parts.</p>}
      <div className="agenda">
        {m.sections.map((s, i) => (
          <div key={s.key} className="agenda-item">
            <span className="n" aria-hidden="true">{String(i + 1).padStart(2, "0")}</span>
            <h3>{s.title}</h3>
            <p className="why">{s.prompt}</p>
            {editor ? (
              <textarea className="textarea" aria-label={`Notes: ${s.title}`} rows={3} maxLength={4000} value={notes[s.key] || ""} placeholder="Notes as you go. Write who will do what, and by when."
                onChange={(e) => { dirty.current.add(s.key); setSaved(""); setNotes({ ...notes, [s.key]: e.target.value }); }}
                onBlur={() => { saveNotes().catch(() => {}); }} />
            ) : <div className={`read small ${notes[s.key] ? "" : "muted"}`}>{notes[s.key] || "No notes yet."}</div>}
          </div>
        ))}
      </div>

      <div className="panel active">
        <div className="row between">
          <span className="label">Decisions and actions</span>
          {editor && (
            <Busy className="btn primary ai" run={async () => {
              await flush();
              const d = await api(`/meetings/${id}/actions`, { method: "POST" });
              load(d); onChanged(); refresh();
              toast(d.added ? `${d.added} ${d.added === 1 ? "task" : "tasks"} added to Tasks` : "No new actions found in the notes");
            }}>Pull out actions</Busy>
          )}
        </div>
        {editor && <p className="small muted">Uses one AI run. The AI reads your notes, lists what you decided, and turns each action into a task for the person named. Running it again only adds new ones.</p>}
        <Field label="What we decided">
          {editor ? (
            <textarea className="textarea" rows={Math.max(3, Math.min(10, (decisions.match(/\n/g) || []).length + 2))} value={decisions} placeholder="One decision per line"
              onChange={(e) => setDecisions(e.target.value)}
              onBlur={() => saveMeta({ decisions: decisions.split("\n").map((x) => x.trim()).filter(Boolean) }).catch(() => {})} />
          ) : m.decisions?.length ? <ul className="small" style={{ margin: 0, paddingLeft: 18 }}>{m.decisions.map((d, i) => <li key={i}>{d}</li>)}</ul>
            : <span className="small muted">Nothing recorded yet.</span>}
        </Field>
        <div className="stack" style={{ gap: 8 }}>
          <span className="label">Actions</span>
          {m.actions?.length ? (
            <div className="timeline">
              {m.actions.map((a, i) => (
                <div key={a.task_id || i}>
                  <b>{a.task}</b>
                  <span className="small muted">{a.owner_name || "No one yet"}{a.due ? ` · due ${day(a.due)}` : ""}</span>
                </div>
              ))}
            </div>
          ) : <span className="small muted">{editor ? "Write your notes above, then pull out the actions." : "No actions yet."}</span>}
          {m.actions?.length > 0 && <div><Link className="btn sm ghost" to={editor ? "/tasks?scope=all" : "/tasks"}>Open in Tasks</Link></div>}
        </div>
      </div>

      {me.workspace.role === "owner" && (
        <div><Busy className="btn sm ghost" run={async () => {
          if (!window.confirm("Delete this meeting and its notes? Tasks already created stay in Tasks.")) return;
          await api(`/meetings/${id}`, { method: "DELETE" }); toast("Meeting deleted"); onDeleted();
        }}>Delete meeting</Busy></div>
      )}
    </div>
  );
}

function MeetingsInner() {
  const { me } = useHub();
  const editor = canAct(me, "manager");
  const [params, setParams] = useSearchParams();
  const [list, reload] = useLoad("/meetings");
  const [people] = useLoad("/team/people");
  const [sel, setSel] = useState(params.get("id"));
  const [creating, setCreating] = useState(null);
  const pick = (id) => { setSel(id); setCreating(null); setParams(id ? { id } : {}, { replace: true }); };
  useEffect(() => { if (list && list.length && !sel && !creating) pick(list[0].id); }, [list]);

  return (
    <Sheet kicker="Scale · Meetings" id="SC-08" pillar="Scale" title={<>Meet with a <em>plan</em>.</>}
      sub="Two meetings, each with a set agenda. Take notes as you go — then the AI pulls out what you decided and turns every action into a task with a name and a date.">
      <div className="split">
        <div className="stack">
          {editor && (
            <div className="stack" style={{ gap: 8 }}>
              <button className={`btn ${creating === "tactical" || (list && !list.length) ? "primary" : ""}`} onClick={() => { setCreating("tactical"); setSel(null); }}>+ Weekly tactical</button>
              <button className={`btn ${creating === "strategic" ? "primary" : ""}`} onClick={() => { setCreating("strategic"); setSel(null); }}>+ Strategic</button>
            </div>
          )}
          <div className="listbox" role="list">
            {list?.map((x) => (
              <button key={x.id} className={sel === x.id ? "on" : ""} onClick={() => pick(x.id)}>
                <span>{x.title}</span>
                <span className="small muted">{TYPES[x.type].label} · {day(x.date)}{x.actions ? ` · ${x.actions} ${x.actions === 1 ? "action" : "actions"}` : ""}{x.status === "closed" ? " · closed" : ""}</span>
              </button>
            ))}
            {list && !list.length && <span className="small muted">No meetings yet.</span>}
          </div>
        </div>
        <div>
          {creating && people ? (
            <NewMeeting type={creating} people={people} onCancel={() => { setCreating(null); if (list?.length) pick(list[0].id); }}
              onCreated={(d) => { reload().then(() => pick(d.id)); }} />
          ) : sel && people ? (
            <MeetingView key={sel} id={sel} people={people} onChanged={reload} onDeleted={() => { pick(null); reload(); }} />
          ) : list && people ? (
            <div className="grid2">
              {Object.entries(TYPES).map(([k, t]) => (
                <div key={k} className="panel">
                  <span className="label">{t.label} · {t.when}</span>
                  <p className="small">{t.what}</p>
                  {editor ? <div><button className="btn sm" onClick={() => setCreating(k)}>Plan one</button></div>
                    : <span className="small muted">Your manager or the owner sets these up.</span>}
                </div>
              ))}
            </div>
          ) : <div className="panel muted">Loading…</div>}
        </div>
      </div>
    </Sheet>
  );
}

export default function Meetings() {
  return (
    <Gate feature="meetings" role="staff" kicker="Scale · Meetings" id="SC-08"
      what="Strategic and weekly tactical meetings with a set agenda. The AI turns your notes into decisions and tasks.">
      <MeetingsInner />
    </Gate>
  );
}
