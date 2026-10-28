import React, { useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../api.js";
import { Busy, Copy, Field, FormError, LoadError, Sheet, useHub, useLoad } from "../ui.jsx";
import { Empty, Loading, Pill } from "../v2.jsx";
import "./admin.css";

const TIERS = ["free", "lite", "program", "running", "growth", "office"];
const IST = "Asia/Kolkata";
const when = (iso) => new Date(iso).toLocaleString("en-IN", { timeZone: IST, weekday: "short", day: "numeric", month: "short", year: "numeric", hour: "numeric", minute: "2-digit" });
const todayIst = () => new Date().toLocaleDateString("en-CA", { timeZone: IST });
const blank = () => ({ title: "", date: "", time: "19:00", join_link: "", tier: "lite", notes: "" });

/** Questions grouped by business, in the order they came in. */
function byBusiness(questions) {
  const out = [];
  for (const q of questions) {
    let g = out.find((x) => x.ws === q.ws);
    if (!g) out.push((g = { ws: q.ws, business: q.business, items: [] }));
    g.items.push(q);
  }
  return out;
}

function ScheduleForm({ tierNames, onAdded }) {
  const { toast } = useHub();
  const [f, setF] = useState(blank);
  const [err, setErr] = useState("");
  const set = (k) => (e) => setF({ ...f, [k]: e.target.value });
  const ready = f.title.trim().length >= 3 && f.date && f.time;
  return (
    <div className="panel active">
      <span className="label">Schedule a call</span>
      <div className="grid3 fields-top">
        <Field label="Title"><input className="input" maxLength={120} value={f.title} onChange={set("title")} placeholder="October member call: getting paid on time" /></Field>
        <Field label="Date"><input className="input" type="date" min={todayIst()} value={f.date} onChange={set("date")} /></Field>
        <Field label="Time (India time)"><input className="input" type="time" value={f.time} onChange={set("time")} /></Field>
        <Field label="Who's invited" hint="Owners on this plan and every plan above it.">
          <select className="select" value={f.tier} onChange={set("tier")}>
            {TIERS.map((t) => <option key={t} value={t}>{t === "office" ? `${tierNames[t]} only` : `${tierNames[t]} and up`}</option>)}
          </select>
        </Field>
        <Field label="Join link (optional)" hint="Zoom, Google Meet or any link. Owners see it on their Member call page.">
          <input className="input mono" type="url" maxLength={500} value={f.join_link} onChange={set("join_link")} placeholder="https://zoom.us/j/…" />
        </Field>
      </div>
      <Field label="Notes for owners (optional)">
        <textarea className="textarea" rows={3} maxLength={1000} value={f.notes} onChange={set("notes")} placeholder="What the call covers, and what to bring." />
      </Field>
      <FormError msg={err} />
      <div className="row">
        <Busy className="btn primary" disabled={!ready} onError={setErr} run={async () => {
          await api("/admin/member-calls", { method: "POST", body: {
            title: f.title.trim(), starts_at: `${f.date}T${f.time}:00`, join_link: f.join_link.trim(), tier: f.tier, notes: f.notes.trim(),
          } });
          toast("Scheduled. Every invited owner gets a notice to send their questions.");
          setF(blank());
          onAdded();
        }}>Schedule and tell owners</Busy>
      </div>
    </div>
  );
}

function CallCard({ c, upcoming, onDeleted }) {
  const { toast } = useHub();
  const groups = byBusiness(c.questions || []);
  const asText = [c.title, when(c.starts_at), "",
    ...groups.map((g) => [g.business || "Business without a name", ...g.items.map((q) => `• ${q.text}`)].join("\n"))].join("\n\n");
  return (
    <div className={`panel ${upcoming ? "active" : ""}`}>
      <div className="row between" style={{ alignItems: "flex-start" }}>
        <div className="stack" style={{ gap: 4 }}>
          <span className="call-when">{when(c.starts_at)} IST</span>
          <h3>{c.title}</h3>
          <span className="small muted">{c.tier === "office" ? `${c.tier_name} only` : `${c.tier_name} and up`}</span>
        </div>
        <Pill kind={upcoming ? "on" : "off"}>{upcoming ? "Upcoming" : "Done"}</Pill>
      </div>
      {c.notes && <p className="small" style={{ whiteSpace: "pre-wrap" }}>{c.notes}</p>}
      {c.join_link && <p className="small">Join link: <a href={c.join_link} target="_blank" rel="noopener noreferrer" style={{ overflowWrap: "anywhere" }}>{c.join_link}</a></p>}
      <div className="row between">
        <span className="label">{c.questions?.length ? `${c.questions.length} ${c.questions.length === 1 ? "question" : "questions"} from ${groups.length} ${groups.length === 1 ? "business" : "businesses"}` : "No questions yet"}</span>
        <div className="row">
          {groups.length > 0 && <Copy text={asText} label="Copy questions" className="btn sm ghost" />}
          <Busy className="btn sm ghost" run={async () => {
            if (!window.confirm(`Delete "${c.title}" and its ${c.questions?.length || 0} questions? Owners won't see it any more.`)) return;
            await api(`/admin/member-calls/${c.id}`, { method: "DELETE" });
            toast("Deleted.");
            onDeleted();
          }}>Delete</Busy>
        </div>
      </div>
      {groups.length === 0 && upcoming && <p className="small muted">Owners send up to 3 questions each from their Member call page before the call starts.</p>}
      {groups.map((g) => (
        <div key={g.ws} className="qgroup">
          <Link to={`/admin/owners/${g.ws}`}><b>{g.business || "Business without a name"}</b></Link>
          {g.items.map((q) => <p key={q.id} className="small">• {q.text}</p>)}
        </div>
      ))}
    </div>
  );
}

export default function AdminCalls() {
  const { me } = useHub();
  const [calls, reload, error] = useLoad("/admin/member-calls");
  const tierNames = Object.fromEntries(TIERS.map((t) => [t, me?.tiers?.[t]?.name || t]));
  const cutoff = Date.now() - 3 * 3600 * 1000; // a call counts as upcoming until 3 hours after it starts
  const upcoming = (calls || []).filter((c) => new Date(c.starts_at).getTime() > cutoff).sort((a, b) => new Date(a.starts_at) - new Date(b.starts_at));
  const past = (calls || []).filter((c) => new Date(c.starts_at).getTime() <= cutoff);
  return (
    <Sheet kicker="Admin · Member calls" id="A-05" pillar="Admin" title={<>The monthly <em>member call</em>.</>}
      sub="Schedule the call, tell every invited owner, and read their questions by business before you go live.">
      <ScheduleForm tierNames={tierNames} onAdded={reload} />
      {error ? <LoadError err={error} /> : !calls ? <Loading /> : (
        <>
          <div className="stack">
            <span className="label">Coming up</span>
            {upcoming.length ? upcoming.map((c) => <CallCard key={c.id} c={c} upcoming onDeleted={reload} />)
              : <Empty>No call scheduled. Schedule the next one above; owners get a notice and can send questions straight away.</Empty>}
          </div>
          {past.length > 0 && (
            <div className="stack">
              <span className="label">Earlier calls</span>
              {past.map((c) => <CallCard key={c.id} c={c} onDeleted={reload} />)}
            </div>
          )}
        </>
      )}
    </Sheet>
  );
}
