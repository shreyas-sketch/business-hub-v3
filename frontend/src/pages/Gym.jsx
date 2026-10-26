import React, { useEffect, useRef, useState } from "react";
import VoiceNote from "../components/VoiceNote.jsx";
import { ago, api, day, localPhone } from "../api.js";
import { Busy, Field, FormError, Sheet, useHub, useLoad } from "../ui.jsx";
import { Empty, Loading } from "../v2.jsx";
import "./growth.css";

/* The AI customer's brief is written for the AI ("You push on price…"); this is what the salesperson reads. */
const ABOUT = {
  haggler: "Likes the offer but pushes hard on price, quotes a cheaper competitor and threatens to walk away.",
  thinker: "Interested, but keeps putting it off: needs to ask the family, is in no hurry, wants time.",
  comparer: "Has two other quotes and asks pointed questions about why they should choose you.",
  unhappy: "Bought before, something went wrong, and is testing whether you care.",
};
const clock = (s) => `${Math.floor(s / 60)}:${String(Math.round(s % 60)).padStart(2, "0")}`;

function ScoreCard({ s }) {
  return (
    <div className="panel active">
      <div className="row between" style={{ alignItems: "flex-end" }}>
        <div className="stack" style={{ gap: 6 }}>
          <span className="label">Overall</span>
          <span className="bigscore">{s.overall}<span className="small muted"> / 10</span></span>
        </div>
      </div>
      <div className="score-rows">
        {s.scores.map((x) => (
          <div key={x.criterion} className="score-row">
            <span>{x.criterion}</span>
            <b>{x.score}/10</b>
            <div className="bar" aria-hidden="true"><i style={{ width: `${x.score * 10}%` }} /></div>
            {x.note && <span className="small muted note">{x.note}</span>}
          </div>
        ))}
      </div>
      {s.best && <div className="stack" style={{ gap: 6 }}><span className="label">Best moment</span><p>{s.best}</p></div>}
      <div className="stack" style={{ gap: 6 }}>
        <span className="label">Three fixes for next time</span>
        <ol className="read-list">{s.fixes.map((f, i) => <li key={i}>{f}</li>)}</ol>
      </div>
    </div>
  );
}

function Practice({ session, onUpdate, onClose }) {
  const { refresh } = useHub();
  const [text, setText] = useState("");
  const [err, setErr] = useState("");
  const box = useRef(null);
  const count = session.messages.length;
  useEffect(() => { if (box.current) box.current.scrollTop = box.current.scrollHeight; }, [count]);
  const open = session.status === "open";
  const said = session.messages.some((m) => m.role === "salesperson");
  const call = session.kind === "call";
  return (
    <div className="stack" style={{ gap: 18 }}>
      <div className="row between" style={{ alignItems: "flex-start" }}>
        <div className="stack" style={{ gap: 6, minWidth: 0 }}>
          <span className="label">{call ? "Real call" : "Role-play"} · {ago(session.created_at)}</span>
          <h2>{session.title}</h2>
          {!call && ABOUT[session.scenario] && <p className="small muted">{ABOUT[session.scenario]}</p>}
        </div>
        <button className="btn sm ghost" onClick={onClose}>Back to the gym</button>
      </div>
      <div className="bubbles chat-msgs" ref={box}>
        {session.messages.map((m, i) => (
          <div key={i} className={`bubble ${m.role === "customer" ? "" : "out"}`}>
            {m.text}
            <span className="small muted">{m.role === "customer" ? "Customer" : call ? "Salesperson" : "You"}</span>
          </div>
        ))}
      </div>
      {open && session.customer_done && <p className="small" style={{ color: "var(--aqua)" }}>The customer has agreed a next step. Finish to get your score.</p>}
      {open && (
        <div className="panel">
          <Field label="Your reply">
            <textarea className="textarea" rows={3} maxLength={1500} value={text} onChange={(e) => setText(e.target.value)}
              placeholder="Say it the way you would to a real customer" />
          </Field>
          <div className="gym-voice"><VoiceNote purpose="gym" onText={(t) => setText((x) => (x.trim() ? `${x.trim()} ` : "") + t)} /></div>
          <FormError msg={err} />
          <div className="row">
            <Busy className="btn primary ai" disabled={!text.trim()} onError={setErr} run={async () => {
              onUpdate(await api(`/gym/sessions/${session.id}/say`, { method: "POST", body: { text: text.trim() } }));
              setText("");
              refresh();
            }}>Say it</Busy>
            <Busy className="btn ai" disabled={!said} onError={setErr} run={async () => {
              onUpdate(await api(`/gym/sessions/${session.id}/finish`, { method: "POST" }));
              refresh();
            }}>Finish and score</Busy>
          </div>
          <span className="small muted">Each customer reply uses one AI token, and so does the score. A voice note is turned into text first (one run).</span>
        </div>
      )}
      {session.score && <ScoreCard s={session.score} />}
    </div>
  );
}

function ScoreCall({ onScored }) {
  const { refresh } = useHub();
  const [t, setT] = useState("");
  const [err, setErr] = useState("");
  return (
    <div className="panel">
      <span className="label">Score a real call</span>
      <p className="small muted">Paste a call or a WhatsApp chat with who said what: “Customer:” for the customer and “Me:” for you, one line each.</p>
      <textarea className="textarea" rows={6} maxLength={20000} value={t} onChange={(e) => setT(e.target.value)} aria-label="Call transcript"
        placeholder={"Customer: Your price is too high. Another shop quoted less.\nMe: I understand. What size is your kitchen, and when do you need it?"} />
      <FormError msg={err} />
      <div className="row">
        <Busy className="btn primary ai" disabled={t.trim().length < 10} onError={setErr} run={async () => {
          onScored(await api("/gym/score-call", { method: "POST", body: { transcript: t } }));
          setT("");
          refresh();
        }}>Score this call</Busy>
        <span className="small muted">Uses one AI token.</span>
      </div>
    </div>
  );
}

function AiCalls({ calls, onScored }) {
  const { refresh } = useHub();
  const [shown, setShown] = useState(null);
  return (
    <div className="stack">
      <span className="label">Recent AI Telecaller calls</span>
      {calls.map((c) => (
        <div key={c.id} className="panel flat">
          <div className="row between">
            <b>{c.name || localPhone(c.to) || "A call"}</b>
            <span className="small muted">{day(c.started_at)}{c.duration ? ` · ${clock(c.duration)}` : ""}</span>
          </div>
          {c.summary && <p className="small">{c.summary}</p>}
          <details className="g-details">
            <summary>Read the conversation</summary>
            <div className="bubbles">
              {(c.transcript || []).filter((x) => x.message).map((x, i) => (
                <div key={i} className={`bubble ${x.role === "user" ? "" : "out"}`}>
                  {x.message}
                  <span className="small muted">{x.role === "user" ? "Customer" : "AI Telecaller"}</span>
                </div>
              ))}
            </div>
          </details>
          <div className="row">
            {c.score ? (
              <button className="btn sm ghost" onClick={() => setShown(shown === c.id ? null : c.id)}>
                {shown === c.id ? "Hide the score" : `See the score · ${c.score.overall}/10`}
              </button>
            ) : (
              <Busy className="btn sm ai" run={async () => { onScored(await api("/gym/score-call", { method: "POST", body: { call_id: c.id } })); refresh(); }}>Score this call</Busy>
            )}
          </div>
          {shown === c.id && c.score && <ScoreCard s={c.score} />}
        </div>
      ))}
    </div>
  );
}

function History({ sessions, onOpen }) {
  if (!sessions.length) return <Empty>Your practice sessions and scored calls show here. Start with the price haggler — it's the one most owners find hardest.</Empty>;
  return (
    <table className="t cards">
      <thead><tr><th>Session</th><th>When</th><th>Score</th><th /></tr></thead>
      <tbody>{sessions.map((s) => (
        <tr key={s.id}>
          <td><b>{s.title}</b><div className="small muted">{s.kind === "call" ? "Real call" : "Role-play"}</div></td>
          <td data-l="When" className="small muted">{ago(s.created_at)}</td>
          <td data-l="Score">{s.score ? <b>{s.score.overall}/10</b> : <span className="small muted">Not finished</span>}</td>
          <td><button className="btn sm ghost" onClick={() => onOpen(s)}>{s.status === "open" ? "Carry on" : "See the score"}</button></td>
        </tr>
      ))}</tbody>
    </table>
  );
}

export default function Gym() {
  const { refresh } = useHub();
  const [data, reload] = useLoad("/gym");
  const [current, setCurrent] = useState(null);
  const show = (s) => { setCurrent(s); reload(); setTimeout(() => window.scrollTo({ top: 0 }), 0); };
  return (
    <Sheet kicker="Scale · Sales Training Gym" id="GR-04" pillar="Scale" title={<>Practise the hard <em>conversations</em>.</>}
      sub="An AI customer haggles, stalls, compares and complains. Reply the way you would on a real call, then get a score out of 10 with three fixes.">
      {!data ? <Loading /> : current ? (
        <Practice session={current} onUpdate={(s) => { setCurrent(s); reload(); }} onClose={() => setCurrent(null)} />
      ) : (
        <>
          <span className="label">Start a role-play</span>
          <div className="grid2">
            {data.scenarios.map((s) => (
              <div key={s.key} className="panel">
                <h3>{s.title}</h3>
                <p className="small muted">{ABOUT[s.key] || s.brief}</p>
                <div>
                  <Busy className="btn sm primary ai" run={async () => { show(await api("/gym/sessions", { method: "POST", body: { scenario: s.key } })); refresh(); }}>
                    Start the role-play
                  </Busy>
                </div>
              </div>
            ))}
          </div>
          <ScoreCall onScored={show} />
          {data.calls.length > 0 && <AiCalls calls={data.calls} onScored={show} />}
          <span className="label">Your sessions</span>
          <History sessions={data.sessions} onOpen={show} />
        </>
      )}
    </Sheet>
  );
}
