import React, { useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { ago, api, day, tokenWords } from "../api.js";
import { Busy, Field, FormError, Modal, Sheet, Stat, useHub, useLoad } from "../ui.jsx";
import { Empty, Loading, Tabs } from "../v2.jsx";
import "./callcoach.css";

/* v2.4 Call coach (Sales engine). Paste a call or upload a recording, pick who made it, check who said what, and get a score
   out of 10 with what went well and 3 fixes — each with the line from the call it is about. Every number here is counted
   from the scored calls themselves. A recording is sent as it is (never as a form file), written out, and not kept. */

const MB = 1024 * 1024;
const AUDIO_ACCEPT = "audio/*,.mp3,.m4a,.mp4,.aac,.wav,.ogg,.oga,.opus,.webm,.flac";
const PASTE_HINT = "Customer: Hello, I want a modular kitchen. What is the price?\nMe: Namaste sir. What size is the kitchen, and when do you need it?";
const whoName = (role, member) => (role === "customer" ? "Customer" : member || "Salesperson");
const score10 = (n) => (n === null || n === undefined ? "—" : `${Number(n).toFixed(1).replace(/\.0$/, "")}`);

/** The recording goes as the request body (not a form upload), so the server never buffers it to a file. */
async function sendRecording(file, memberId) {
  const ext = (file.name.includes(".") ? file.name.split(".").pop() : "").toLowerCase().slice(0, 8);
  const res = await fetch(`/api/call-coach/recording?member_id=${encodeURIComponent(memberId)}&ext=${encodeURIComponent(ext)}`, {
    method: "POST", credentials: "same-origin", headers: { "content-type": file.type || "application/octet-stream" }, body: file,
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    const d = data.detail;
    const e = new Error(tokenWords(typeof d === "string" ? d : d?.message || "That recording couldn't be sent. Please try again."));
    e.status = res.status;
    throw e;
  }
  return data;
}

/* ───────────── the team list ───────────── */
function Team({ members, onChange }) {
  const [name, setName] = useState("");
  const [editing, setEditing] = useState(false);
  const [err, setErr] = useState("");
  return (
    <div className="panel cc-team">
      <div className="row between">
        <span className="label">Your team</span>
        {members.length > 0 && <button className="btn sm ghost" onClick={() => setEditing(!editing)}>{editing ? "Done" : "Edit names"}</button>}
      </div>
      {!members.length && <p className="small muted">Type the names of the people who talk to customers — once. No logins needed. Then pick a name each time you score a call.</p>}
      {editing ? (
        <div className="cc-edit-list">{members.map((m) => <EditMember key={m.id} m={m} onChange={onChange} />)}</div>
      ) : members.length > 0 && (
        <div className="cc-chips">{members.map((m) => <span key={m.id} className="cc-chip">{m.name}</span>)}</div>
      )}
      <div className="cc-add">
        <input className="input" value={name} maxLength={40} placeholder="A name, like Ravi" aria-label="Team member's name"
          onChange={(e) => setName(e.target.value)} onKeyDown={(e) => { if (e.key === "Enter") e.currentTarget.nextSibling?.click(); }} />
        <Busy className="btn sm" disabled={name.trim().length < 2} onError={setErr} run={async () => {
          const r = await api("/call-coach/members", { method: "POST", body: { name } });
          setName("");
          onChange(r.members);
        }}>Add</Busy>
      </div>
      <FormError msg={err} />
    </div>
  );
}

function EditMember({ m, onChange }) {
  const [name, setName] = useState(m.name);
  const [err, setErr] = useState("");
  return (
    <div className="cc-edit">
      <input className="input" value={name} maxLength={40} aria-label={`Rename ${m.name}`} onChange={(e) => setName(e.target.value)} />
      <div className="row">
        <Busy className="btn sm" disabled={name.trim() === m.name || name.trim().length < 2} onError={setErr}
          run={async () => onChange((await api(`/call-coach/members/${m.id}`, { method: "PATCH", body: { name } })).members)}>Save</Busy>
        <Busy className="btn sm ghost" onError={setErr} run={async () => {
          if (!window.confirm(`Take ${m.name} off the list? Their past calls stay, under their name.`)) return;
          onChange((await api(`/call-coach/members/${m.id}`, { method: "DELETE" })).members);
        }}>Remove</Busy>
      </div>
      <FormError msg={err} />
    </div>
  );
}

/* ───────────── score a call: paste or upload ───────────── */
function ScorePanel({ data, memberId, setMemberId, onReview, onScored }) {
  const { refresh } = useHub();
  const [tab, setTab] = useState("paste");
  const [text, setText] = useState("");
  const [file, setFile] = useState(null);
  const [err, setErr] = useState("");
  const rec = data.recording;
  const member = data.members.find((m) => m.id === memberId);
  const left = data.limit.left;
  const noMember = !member;
  const tooBig = file && file.size > rec.max_mb * MB;
  return (
    <div className="panel active cc-score">
      <span className="label">Score a call</span>
      <Field label="Who made the call?">
        <select className="select" value={memberId} onChange={(e) => setMemberId(e.target.value)}>
          {!data.members.length && <option value="">Add a name to your team first</option>}
          {data.members.map((m) => <option key={m.id} value={m.id}>{m.name}</option>)}
        </select>
      </Field>
      <Tabs value={tab} onChange={(t) => { setTab(t); setErr(""); }} tabs={[["paste", "Paste"], ["upload", "Upload recording"]]} />
      {left <= 0 && <p className="small cc-warn">You've scored this month's {data.limit.limit} calls. More open on the 1st.</p>}

      {tab === "paste" ? (
        <div className="stack">
          <p className="small muted">One line each, with who said it: “Customer:” for the customer and “Me:” or {member ? `“${member.name.split(" ")[0]}:”` : "the person's name"} for your side. A WhatsApp chat copied out works too.</p>
          <textarea className="textarea" rows={8} maxLength={40000} value={text} onChange={(e) => setText(e.target.value)} placeholder={PASTE_HINT} aria-label="The call, pasted" />
          <FormError msg={err} />
          <div className="cc-actions">
            <Busy className="btn primary ai" disabled={noMember || text.trim().length < 10 || left <= 0} onError={setErr} run={async () => {
              const r = await api("/call-coach/score", { method: "POST", body: { member_id: memberId, text } });
              setText("");
              refresh();
              onScored(r.call);
            }}>Score this call · {data.costs.paste} AI token</Busy>
            <Busy className="btn ghost" disabled={noMember || text.trim().length < 10} onError={setErr} run={async () => {
              const r = await api("/call-coach/read", { method: "POST", body: { member_id: memberId, text } });
              onReview({ member_id: memberId, lines: r.lines, source: "paste", unsure: r.unsure, labels: r.labels });
            }}>Check who said what first</Busy>
          </div>
          <span className="small muted">Checking is free. Scoring uses {data.costs.paste} AI token.</span>
        </div>
      ) : !rec.on ? (
        <div className="cc-note">
          <p className="small">{rec.note}</p>
        </div>
      ) : (
        <div className="stack">
          <p className="small muted">An MP3, M4A, WAV, OGG, WEBM or FLAC file, up to {rec.max_mb} MB — about {rec.minutes} minutes of a phone call.
            We write it out and mark who said what. You check it, then score it.</p>
          <label className={`btn sm cc-file ${file ? "has" : ""}`}>
            {file ? "Choose another file" : "Choose the recording"}
            <input type="file" accept={AUDIO_ACCEPT} hidden onChange={(e) => { setFile(e.target.files?.[0] || null); setErr(""); e.target.value = ""; }} />
          </label>
          {file && <span className="small cc-filename">{file.name} · {(file.size / MB).toFixed(1)} MB</span>}
          {tooBig && <p className="form-err">That file is bigger than {rec.max_mb} MB. Upload a shorter part of the call — up to about {rec.minutes} minutes.</p>}
          <FormError msg={err} />
          <div className="cc-actions">
            <Busy className="btn primary ai" disabled={noMember || !file || tooBig || left <= 0} onError={setErr} run={async () => {
              const r = await sendRecording(file, memberId);
              setFile(null);
              refresh();
              onReview({ ...r.draft, draft_id: r.draft.id, source: "recording" });
            }}>Write out the call · 1 AI token</Busy>
          </div>
          <p className="small muted">A recording uses {data.costs.recording} AI tokens: 1 to write out the call and mark who said what, 1 to score it.</p>
          <p className="small cc-rule">{data.lines.allowed}</p>
          <p className="small muted">{data.lines.not_kept}</p>
        </div>
      )}
      <span className="small muted">{left} of {data.limit.limit} calls left to score this month.</span>
    </div>
  );
}

/** A text box that grows to show all of its line (a written-out call has long and short lines). */
function GrowText({ value, onChange, ...p }) {
  const ref = useRef(null);
  useLayoutEffect(() => {
    const el = ref.current;
    if (!el) return;
    el.style.height = "auto";
    el.style.height = `${el.scrollHeight + 2}px`;
  }, [value]);
  return <textarea ref={ref} rows={1} value={value} onChange={onChange} {...p} />;
}

/* ───────────── check who said what ───────────── */
function Review({ draft, data, onDone, onCancel }) {
  const { refresh } = useHub();
  const [lines, setLines] = useState(draft.lines.map((l, i) => ({ ...l, key: i })));
  const [memberId, setMemberId] = useState(draft.member_id);
  const [err, setErr] = useState("");
  const [next, setNext] = useState(draft.lines.length);
  const member = data.members.find((m) => m.id === memberId);
  const set = (k, patch) => setLines((xs) => xs.map((l) => (l.key === k ? { ...l, ...patch } : l)));
  const recording = draft.source === "recording";
  const used = lines.filter((l) => l.text.trim());
  const both = used.some((l) => l.role === "customer") && used.some((l) => l.role === "salesperson");
  return (
    <div className="panel active cc-review">
      <div className="row between">
        <span className="label">Check who said what</span>
        <span className="small muted">{lines.length} lines</span>
      </div>
      <p className="small muted">
        {recording ? (draft.labelled_by === "ai" ? "The AI marked who said each line." : "Each line was marked by simple rules (who greets, who asks the price), so check them carefully.") : "This is how we read your paste."}
        {" "}Tap a name to switch it. Fix any word. Then score.
      </p>
      {draft.unsure && <p className="small cc-warn">We couldn't tell which of {draft.labels.map((l) => `“${l}”`).join(" and ")} is your team member. We guessed — please check.</p>}
      <Field label="Who made the call?">
        <select className="select" value={memberId} onChange={(e) => setMemberId(e.target.value)}>
          {data.members.map((m) => <option key={m.id} value={m.id}>{m.name}</option>)}
        </select>
      </Field>
      <div className="cc-lines">
        {lines.map((l) => (
          <div key={l.key} className={`cc-line ${l.role}`}>
            <div className="cc-line-top">
              <button type="button" className="cc-who" aria-label={`Said by ${whoName(l.role, member?.name)}. Tap to switch.`}
                onClick={() => set(l.key, { role: l.role === "customer" ? "salesperson" : "customer" })}>
                {whoName(l.role, member?.name)} <span aria-hidden="true">⇄</span>
              </button>
              <button type="button" className="cc-x" aria-label="Delete this line" onClick={() => setLines((xs) => xs.filter((x) => x.key !== l.key))}>×</button>
            </div>
            <GrowText className="textarea cc-line-text" maxLength={4000} value={l.text} aria-label="What was said"
              onChange={(e) => set(l.key, { text: e.target.value })} />
          </div>
        ))}
      </div>
      <button className="btn sm ghost cc-addline" onClick={() => {
        setLines((xs) => [...xs, { role: xs.length && xs[xs.length - 1].role === "customer" ? "salesperson" : "customer", text: "", key: next }]);
        setNext(next + 1);
      }}>+ Add a line</button>
      <FormError msg={err} />
      <div className="cc-actions">
        <Busy className="btn primary ai" disabled={!member || !both || data.limit.left <= 0} onError={setErr} run={async () => {
          const body = { member_id: memberId, lines: used.map((l) => ({ role: l.role, text: l.text.trim() })), ...(draft.draft_id ? { draft_id: draft.draft_id } : {}) };
          const r = await api("/call-coach/score", { method: "POST", body });
          refresh();
          onDone(r.call);
        }}>Score this call · 1 AI token</Busy>
        {recording ? (
          <Busy className="btn ghost" run={async () => {
            if (!window.confirm("Throw away this written-out call? You'd need to upload the recording again.")) return;
            await api(`/call-coach/drafts/${draft.draft_id}`, { method: "DELETE" });
            onCancel(true);
          }}>Throw it away</Busy>
        ) : <button className="btn ghost" onClick={() => onCancel(false)}>Back</button>}
      </div>
      {!both && <span className="small cc-warn">Mark at least one line as the customer's and one as {member?.name || "your team member"}'s.</span>}
      <span className="small muted">{recording ? "Writing it out used 1 AI token. Scoring uses 1 more." : "Scoring uses 1 AI token."}
        {recording && draft.until ? ` This written call waits here until ${day(draft.until)}.` : ""}</span>
    </div>
  );
}

/* ───────────── the result ───────────── */
function Moment({ m, member }) {
  if (!m) return <p className="small muted cc-moment none">This one is about the whole call.</p>;
  return (
    <figure className="cc-moment">
      <figcaption className="label">From the call</figcaption>
      {m.lines.map((l, i) => (
        <blockquote key={i} className={l.hit ? "hit" : ""}>
          <span className="small">{whoName(l.who, member)}</span>
          “{l.text}”
        </blockquote>
      ))}
    </figure>
  );
}

function ResultCard({ call, onAddToDeal, onClose }) {
  const s = call.score;
  return (
    <div className="panel active cc-result">
      <div className="cc-result-head">
        <div className="stack" style={{ gap: 4, minWidth: 0 }}>
          <span className="label">{call.member} · {call.input === "recording" ? "Recording" : "Pasted call"} · {ago(call.scored_at)}</span>
          <span className="cc-big"><b>{score10(s.overall)}</b><span className="small muted"> / 10</span></span>
        </div>
        {onClose && <button className="btn sm ghost" onClick={onClose}>Close</button>}
      </div>
      <div className="cc-crit">
        {s.scores.map((x) => (
          <div key={x.criterion} className="cc-crit-row">
            <span>{x.criterion}</span>
            <b>{x.score}/10</b>
            <div className="cc-bar" aria-hidden="true"><i style={{ width: `${x.score * 10}%` }} /></div>
            {x.note && <span className="small muted cc-crit-note">{x.note}</span>}
          </div>
        ))}
      </div>
      {s.best && <div className="cc-best"><span className="label">What went well</span><p>{s.best}</p></div>}
      <div className="stack">
        <span className="label">{["", "One fix", "Two fixes", "Three fixes"][s.fixes.length] || `${s.fixes.length} fixes`} for next time</span>
        <ol className="cc-fixes">
          {s.fixes.map((f, i) => (
            <li key={i}><p>{f}</p><Moment m={s.moments?.[i]} member={call.member} /></li>
          ))}
        </ol>
      </div>
      <details className="cc-transcript">
        <summary>Read the whole call</summary>
        <div className="cc-lines read">
          {call.lines.map((l, i) => (
            <div key={i} className={`cc-line ${l.role}`}><span className="small cc-who-read">{whoName(l.role, call.member)}</span><p>{l.text}</p></div>
          ))}
        </div>
      </details>
      <div className="cc-deal">
        {call.filed?.length > 0 && (
          <p className="small cc-ok">Added to {call.filed.map((f) => f.deal || "a deal").join(", ")} · {ago(call.filed[0].at)}</p>
        )}
        <button className="btn sm" onClick={() => onAddToDeal(call)}>{call.filed?.length ? "Add it to another deal" : "Add this call to the deal"}</button>
        <span className="small muted">Its summary, next step and follow-up date go into the deal.</span>
      </div>
    </div>
  );
}

/* ───────────── add this call to the deal ───────────── */
function DealPicker({ call, onClose, onAdded }) {
  const [deals, setDeals] = useState(null);
  const [q, setQ] = useState("");
  const [err, setErr] = useState("");
  const [pick, setPick] = useState("");
  useEffect(() => {
    api("/crm/deals").then((d) => setDeals(d.deals || [])).catch((e) => { setDeals([]); setErr(e.message); });
  }, []);
  const shown = useMemo(() => {
    const t = q.trim().toLowerCase();
    const open = (deals || []).filter((d) => d.status !== "won" && d.status !== "lost");
    const rest = (deals || []).filter((d) => d.status === "won" || d.status === "lost");
    return [...open, ...rest].filter((d) => !t || `${d.name} ${d.company} ${d.phone}`.toLowerCase().includes(t)).slice(0, 40);
  }, [deals, q]);
  return (
    <Modal onClose={onClose}>
      <span className="kicker">Call coach</span>
      <h2>Add this call to a <em>deal</em>.</h2>
      <p className="small muted">The call's summary goes in as a note, with the next step and a follow-up date if one was said. A new stage or value waits for your tap.</p>
      {deals === null ? <Loading /> : (
        <div className="stack">
          <input className="input" type="search" value={q} onChange={(e) => setQ(e.target.value)} placeholder="Search a name or company" aria-label="Search deals" />
          {shown.length ? (
            <div className="cc-deals" role="listbox" aria-label="Deals">
              {shown.map((d) => (
                <button key={d.id} type="button" role="option" aria-selected={pick === d.id} className={`cc-deal-row ${pick === d.id ? "on" : ""}`} onClick={() => setPick(d.id)}>
                  <b>{d.name}</b>
                  <span className="small muted">{[d.company, d.status === "won" ? "Won" : d.status === "lost" ? "Lost" : "Open"].filter(Boolean).join(" · ")}</span>
                </button>
              ))}
            </div>
          ) : !err && <p className="small muted">{q ? "No deal matches." : "No deals yet. Add the deal in Pipeline first."}</p>}
          <FormError msg={err} />
          <div className="row">
            <Busy className="btn primary ai" disabled={!pick} onError={setErr} run={async () => {
              try {
                await api(`/crm/deals/${pick}/conversation`, { method: "POST", body: { text: call.text, source: "call", call_id: call.id } });
              } catch (e) {
                if (e.status === 405 || (e.status === 404 && /^not found$/i.test(e.message))) {
                  throw new Error("Adding a call to a deal isn't switched on in this hub yet. Your score is saved — try again later.");
                }
                throw e;
              }
              onAdded(deals.find((d) => d.id === pick));
            }}>Add to this deal · 1 AI token</Busy>
            <button className="btn ghost" onClick={onClose}>Cancel</button>
          </div>
        </div>
      )}
    </Modal>
  );
}

/* ───────────── each person, and history ───────────── */
function Trend({ p }) {
  if (!p.count) return <span className="small muted">No calls scored yet. Score one of {p.name.split(" ")[0]}'s calls to start.</span>;
  const lastLine = `Last ${p.last_n} ${p.last_n === 1 ? "call" : "calls"}: ${score10(p.last5)}/10`;
  if (p.prev5 === null) return <span className="small muted">{lastLine}. A trend shows after the 6th call.</span>;
  const diff = Math.abs(p.last5 - p.prev5).toFixed(1).replace(/\.0$/, "");
  const word = p.trend === "up" ? `Up ${diff}` : p.trend === "down" ? `Down ${diff}` : "About the same";
  return (
    <span className="small">
      {lastLine} · the {p.prev_n} before: {score10(p.prev5)}/10 · <b className={`cc-trend ${p.trend}`}>{p.trend === "up" ? "▲ " : p.trend === "down" ? "▼ " : ""}{word}</b>
    </span>
  );
}

function People({ onOpen }) {
  const [data] = useLoad("/call-coach/people");
  if (!data) return <Loading />;
  const t = data.team;
  return (
    <div className="stack">
      {data.people.length ? data.people.map((p) => (
        <div key={p.member_id || p.name} className="panel cc-person">
          <div className="row between">
            <b>{p.name}{p.removed && <span className="small muted"> · removed</span>}</b>
            <span className="small muted">{p.count} {p.count === 1 ? "call" : "calls"}</span>
          </div>
          <Trend p={p} />
          {p.calls.length > 0 && (
            <div className="cc-scores" aria-label={`${p.name}'s scores, newest first`}>
              {p.calls.slice(0, 12).map((c) => (
                <button key={c.id} type="button" className="cc-score-chip" onClick={() => onOpen(c.id)} title={day(c.scored_at)}>
                  {score10(c.overall)}<small>{new Date(c.scored_at).toLocaleDateString("en-IN", { day: "numeric", month: "short" })}</small>
                </button>
              ))}
            </div>
          )}
        </div>
      )) : <Empty>Add your team's names above, then score a call. Each person's scores show here.</Empty>}

      <div className="panel">
        <span className="label">Where the team loses points · {t.month}</span>
        {t.calls ? (
          <>
            <p className="small muted">{t.calls} {t.calls === 1 ? "call" : "calls"} scored this month · team average {score10(t.average)}/10. Lowest first.</p>
            <div className="cc-weak">
              {t.criteria.map((r) => (
                <div key={r.criterion} className="cc-weak-row">
                  <div className="row between"><b>{r.criterion}</b><span>{score10(r.average)}/10</span></div>
                  <div className="cc-bar" aria-hidden="true"><i style={{ width: `${r.average * 10}%` }} /></div>
                  <span className="small muted">{r.calls} {r.calls === 1 ? "call" : "calls"} · {r.fixes} {r.fixes === 1 ? "fix" : "fixes"} about this</span>
                  {r.example && <span className="small cc-example">“{r.example}”</span>}
                </div>
              ))}
            </div>
            {t.other_fixes > 0 && <span className="small muted">{t.other_fixes} other {t.other_fixes === 1 ? "fix" : "fixes"} didn't fit one of these.</span>}
          </>
        ) : <p className="small muted">No calls scored this month yet. Score a call to see where the team loses points.</p>}
      </div>
    </div>
  );
}

function History({ members, onOpen }) {
  const [who, setWho] = useState("");
  const [items, setItems] = useState(null);
  const [more, setMore] = useState(false);
  const load = async (before = "") => {
    const qs = new URLSearchParams({ ...(who ? { member_id: who } : {}), ...(before ? { before } : {}) }).toString();
    const r = await api(`/call-coach/calls${qs ? `?${qs}` : ""}`);
    setItems((xs) => (before ? [...(xs || []), ...r.calls] : r.calls));
    setMore(r.more);
  };
  useEffect(() => { setItems(null); load(); }, [who]); // eslint-disable-line react-hooks/exhaustive-deps
  return (
    <div className="stack">
      <select className="select cc-filter" value={who} onChange={(e) => setWho(e.target.value)} aria-label="Whose calls">
        <option value="">Everyone</option>
        {members.map((m) => <option key={m.id} value={m.id}>{m.name}</option>)}
      </select>
      {items === null ? <Loading /> : items.length ? (
        <div className="cc-history">
          {items.map((c) => (
            <button key={c.id} type="button" className="cc-hrow" onClick={() => onOpen(c.id)}>
              <span className="cc-hscore">{score10(c.overall)}</span>
              <span className="cc-hwho"><b>{c.member}</b><span className="small muted">{c.fix}</span></span>
              <span className="small muted cc-hwhen">{day(c.scored_at)}<br />{c.input === "recording" ? "Recording" : "Pasted"}</span>
            </button>
          ))}
        </div>
      ) : <Empty>No calls scored yet.</Empty>}
      {more && <Busy className="btn sm ghost" run={() => load(items[items.length - 1].scored_at)}>Show older calls</Busy>}
    </div>
  );
}

/* ───────────── the page ───────────── */
export default function CallCoach() {
  const { toast } = useHub();
  const [params, setParams] = useSearchParams();
  const callId = params.get("call");
  const [data, reload] = useLoad("/call-coach");
  const [memberId, setMemberId] = useState("");
  const [review, setReview] = useState(null);
  const [opened, setOpened] = useState(null);
  const [openErr, setOpenErr] = useState("");
  const [dealFor, setDealFor] = useState(null);
  const [tab, setTab] = useState("people");
  const [stamp, setStamp] = useState(0);   // reloads the lists below after a new score

  useEffect(() => {
    if (data && !data.members.some((m) => m.id === memberId)) setMemberId(data.members[0]?.id || "");
  }, [data]); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    setOpenErr("");
    if (!callId) { setOpened(null); return; }
    if (opened?.id === callId) return;
    api(`/call-coach/calls/${callId}`).then((r) => setOpened(r.call)).catch((e) => { setOpened(null); setOpenErr(e.message); });
  }, [callId]); // eslint-disable-line react-hooks/exhaustive-deps

  const open = (id) => { setParams({ call: id }); window.scrollTo({ top: 0, behavior: "smooth" }); };
  const showCall = (call) => { setOpened(call); setParams({ call: call.id }); setReview(null); reload(); setStamp((x) => x + 1); window.scrollTo({ top: 0, behavior: "smooth" }); };
  const close = () => { setParams({}); setOpened(null); };

  if (!data) return <Sheet kicker="Sales engine · Call coach" id="SE-08" title={<>Every call, scored. Three <em>fixes</em>.</>}><Loading /></Sheet>;
  const setMembers = (members) => { reload(); if (!members.some((m) => m.id === memberId)) setMemberId(members[0]?.id || ""); };
  const m = data.month;
  return (
    <Sheet kicker="Sales engine · Call coach" id="SE-08" pillar="Sales"
      title={<>Every call, scored. Three <em>fixes</em>.</>}
      sub="Paste a call or upload a recording, and pick who made it. You get a score out of 10, what went well, and 3 fixes — each with the line from the call it is about.">
      <div className="statgrid">
        <Stat label={`Calls scored · ${m.label}`} value={m.calls} note={m.calls ? undefined : "None yet"} />
        <Stat label="Team average" value={m.average === null ? "—" : `${score10(m.average)}/10`} note={m.calls ? `From ${m.calls} ${m.calls === 1 ? "call" : "calls"}` : "Shows after the first call"} />
        <Stat label="Calls left this month" value={`${data.limit.left} of ${data.limit.limit}`} />
      </div>

      {callId && openErr && <FormError msg={openErr} />}
      {opened && <ResultCard call={opened} onAddToDeal={setDealFor} onClose={close} />}

      <Team members={data.members} onChange={setMembers} />

      {review ? (
        <Review key={review.draft_id || "paste"} draft={review} data={data} onDone={showCall}
          onCancel={(dropped) => { setReview(null); if (dropped) reload(); }} />
      ) : (
        <ScorePanel data={data} memberId={memberId} setMemberId={setMemberId} onReview={setReview} onScored={showCall} />
      )}

      {!review && data.drafts.length > 0 && (
        <div className="panel flat">
          <span className="label">Written out, waiting for you to check</span>
          {data.drafts.map((d) => (
            <div key={d.id} className="row between cc-draft">
              <span className="small"><b>{d.member}</b> · {d.lines} lines · {ago(d.at)}</span>
              <Busy className="btn sm" run={async () => {
                const r = await api(`/call-coach/drafts/${d.id}`);
                setReview({ ...r.draft, draft_id: r.draft.id, source: "recording" });
              }}>Check and score</Busy>
            </div>
          ))}
        </div>
      )}

      <Tabs value={tab} onChange={setTab} tabs={[["people", "Each person"], ["history", "All scored calls"]]} />
      {tab === "people" ? <People key={`p${stamp}`} onOpen={open} /> : <History key={`h${stamp}`} members={data.members} onOpen={open} />}

      {dealFor && (
        <DealPicker call={dealFor} onClose={() => setDealFor(null)} onAdded={(d) => {
          setDealFor(null);
          toast(`Added to ${d?.name || "the deal"}.`);
          api(`/call-coach/calls/${dealFor.id}`).then((r) => setOpened(r.call)).catch(() => {});
        }} />
      )}
    </Sheet>
  );
}
