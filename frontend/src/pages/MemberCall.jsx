import React, { useState } from "react";
import { Link } from "react-router-dom";
import { ago, api } from "../api.js";
import { Busy, Field, FormError, LoadError, Sheet, useHub, useLoad } from "../ui.jsx";
import { Empty, Loading, Pill } from "../v2.jsx";
import "./member.css";

const IST = { timeZone: "Asia/Kolkata" };
const when = (iso) => `${new Date(iso).toLocaleString("en-IN", { ...IST, weekday: "long", day: "numeric", month: "long", hour: "numeric", minute: "2-digit" })} IST`;
const whenShort = (iso) => new Date(iso).toLocaleDateString("en-IN", { ...IST, day: "numeric", month: "short", year: "numeric" });
const safeLink = (u) => (/^https?:\/\//i.test(u || "") ? u : null);

function until(iso) {
  const ms = Date.parse(iso) - Date.now();
  if (ms <= 0) return "On now";
  const h = ms / 3600000;
  if (h < 1) return `In ${Math.max(1, Math.round(ms / 60000))} min`;
  if (h < 24) return `In ${Math.round(h)} ${Math.round(h) === 1 ? "hour" : "hours"}`;
  const d = Math.round(h / 24);
  return d === 1 ? "Tomorrow" : `In ${d} days`;
}

function UpcomingCall({ c, onSent }) {
  const { toast } = useHub();
  const [text, setText] = useState("");
  const [err, setErr] = useState("");
  const started = Date.parse(c.starts_at) <= Date.now();
  const mine = c.my_questions || [];
  const link = safeLink(c.join_link);
  const send = async () => {
    await api(`/member-call/${c.id}/questions`, { method: "POST", body: { text: text.trim() } });
    setText("");
    toast("Question sent");
    await onSent();
  };
  return (
    <div className="panel active">
      <div className="row between">
        <span className="label">{when(c.starts_at)}</span>
        <span className="row" style={{ gap: 6 }}>
          {c.tier && !["free", "lite"].includes(c.tier) && <Pill>For {c.tier_name}</Pill>}
          <Pill kind="on">{until(c.starts_at)}</Pill>
        </span>
      </div>
      <h2>{c.title}</h2>
      {c.notes && <p className="sub" style={{ whiteSpace: "pre-wrap" }}>{c.notes}</p>}
      {link ? <div className="row"><a className="btn primary" href={link} target="_blank" rel="noreferrer">Join the call</a></div>
        : <span className="small muted">The join link will show here before the call.</span>}
      <hr className="rule" />
      <span className="label">Your questions · {mine.length} of 3 sent</span>
      {mine.length > 0 && (
        <ol className="read-list">{mine.map((q) => <li key={q.id}>{q.text} <span className="small muted">· {ago(q.at)}</span></li>)}</ol>
      )}
      {started ? <p className="small muted">This call has started. Send your questions for the next one.</p>
        : mine.length >= 3 ? <p className="small muted">You've sent 3 questions for this call — that's the limit, so everyone gets a turn.</p>
        : (
          <div className="stack">
            <Field label="Send a question" hint="Ask about your own business — numbers, customers, pricing, team. Up to 3 per call.">
              <textarea className="textarea" maxLength={600} placeholder="e.g. How should I price my Diwali package without hurting my margins?"
                value={text} onChange={(e) => setText(e.target.value)} />
            </Field>
            <FormError msg={err} />
            <div className="row"><Busy className="btn" disabled={text.trim().length < 5} run={send} onError={setErr}>Send my question</Busy></div>
          </div>
        )}
    </div>
  );
}

export default function MemberCall() {
  const { me } = useHub();
  const [d, reload, err] = useLoad("/member-call");
  return (
    <Sheet kicker="Learn · Member call" id="MB-03" title={<>Bring your questions, <em>live</em>.</>}
      sub="Once a month, a live call for members. Send your questions before it, join on the day, and watch the recording after.">
      {!d ? (err ? <LoadError err={err} /> : <Loading />) : (
        <>
          {d.upcoming.length === 0 ? <Empty>The next call will show here — you'll get a notice.</Empty>
            : <div className="stack">{d.upcoming.map((c) => <UpcomingCall key={c.id} c={c} onSent={reload} />)}</div>}

          {d.past.length > 0 && (
            <div className="panel">
              <div className="row between">
                <span className="label">Past calls</span>
                {me?.features?.recordings && <Link to="/recordings" className="small">Watch the recordings</Link>}
              </div>
              <div className="m-calls">
                {d.past.map((c) => (
                  <div key={c.id} className="m-call-row">
                    <div className="stack" style={{ gap: 4 }}>
                      <b style={{ fontWeight: 500 }}>{c.title}</b>
                      {c.notes && <span className="small muted" style={{ whiteSpace: "pre-wrap" }}>{c.notes}</span>}
                      {c.my_questions?.length > 0 && <span className="small muted">You sent {c.my_questions.length} {c.my_questions.length === 1 ? "question" : "questions"}.</span>}
                    </div>
                    <span className="small muted">{whenShort(c.starts_at)}</span>
                  </div>
                ))}
              </div>
            </div>
          )}
        </>
      )}
    </Sheet>
  );
}
