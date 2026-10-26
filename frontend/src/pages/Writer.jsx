import React, { useEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { api, upload, waLink } from "../api.js";
import { Busy, Copy, Field, InviteLine, Locked, Sheet, useHub } from "../ui.jsx";
import { Tabs } from "../v2.jsx";
import TemplatePicker from "../components/Templates.jsx";
import "./free.css";

/* ───────── Voice recorder (also used by Onboarding) ─────────
   Records or uploads a voice note; the server transcribes it (one AI token) for `purpose` (onboarding, polish…),
   which decides the plan and role needed. */
const MAX_SECONDS = 300;
const MAX_BYTES = 10 * 1024 * 1024;
const RECORD_TYPES = ["audio/webm;codecs=opus", "audio/webm", "audio/mp4", "audio/ogg;codecs=opus", "audio/ogg"];
const BY_EXTENSION = { opus: "audio/ogg", ogg: "audio/ogg", oga: "audio/ogg", m4a: "audio/mp4", mp4: "audio/mp4", aac: "audio/aac",
  mp3: "audio/mpeg", wav: "audio/wav", webm: "audio/webm", flac: "audio/flac" };
const clock = (s) => `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`;

function micMessage(err) {
  const name = err?.name || "";
  if (name === "NotAllowedError" || name === "SecurityError") return "Your browser blocked the microphone. Allow it for this site (tap the lock next to the web address), or upload a voice note instead.";
  if (name === "NotFoundError") return "No microphone found on this device. Upload a voice note instead.";
  if (name === "NotReadableError") return "Another app is using the microphone. Close it and try again, or upload a voice note instead.";
  return "Recording didn't start. Please upload a voice note instead.";
}

export function VoiceInput({ purpose, onText, label = "Or say it", hint }) {
  const { refresh } = useHub();
  const [state, setState] = useState("idle"); // idle | recording | sending
  const [secs, setSecs] = useState(0);
  const [error, setError] = useState("");
  const rec = useRef(null);
  const stream = useRef(null);
  const timer = useRef(null);
  const discard = useRef(false);
  const alive = useRef(true);

  const release = () => { clearInterval(timer.current); timer.current = null; stream.current?.getTracks().forEach((t) => t.stop()); stream.current = null; };
  const stop = () => { clearInterval(timer.current); if (rec.current && rec.current.state !== "inactive") rec.current.stop(); rec.current = null; };
  useEffect(() => () => { alive.current = false; discard.current = true; stop(); release(); }, []);

  const send = async (blob, name) => {
    if (!blob.size) { setError("That recording is empty. Please try again."); return; }
    if (blob.size > MAX_BYTES) { setError("That voice note is too big. Please keep it under 10 MB."); return; }
    setState("sending");
    setError("");
    try {
      const form = new FormData();
      form.append("file", blob, name);
      const r = await upload(`/voice/transcribe?for=${purpose}`, form);
      if (alive.current) onText(r.text);
      refresh();
    } catch (e) {
      if (alive.current && !e.handled) setError(e.message);
    } finally {
      if (alive.current) setState("idle");
    }
  };

  const start = async () => {
    setError("");
    if (!navigator.mediaDevices?.getUserMedia || typeof MediaRecorder === "undefined") {
      setError("Recording isn't available in this browser. Please upload a voice note instead.");
      return;
    }
    try {
      const s = await navigator.mediaDevices.getUserMedia({ audio: true });
      stream.current = s;
      const type = RECORD_TYPES.find((t) => MediaRecorder.isTypeSupported?.(t));
      const r = new MediaRecorder(s, type ? { mimeType: type } : undefined);
      const chunks = [];
      discard.current = false;
      r.ondataavailable = (e) => { if (e.data?.size) chunks.push(e.data); };
      r.onstop = () => {
        release();
        if (discard.current) return;
        const mime = r.mimeType || type || "audio/webm";
        send(new Blob(chunks, { type: mime }), `voice-note.${mime.includes("mp4") ? "m4a" : mime.includes("ogg") ? "ogg" : "webm"}`);
      };
      r.start(1000);
      rec.current = r;
      setSecs(0);
      setState("recording");
      const began = Date.now();
      timer.current = setInterval(() => {
        const n = Math.floor((Date.now() - began) / 1000);
        setSecs(Math.min(n, MAX_SECONDS));
        if (n >= MAX_SECONDS) stop();
      }, 500);
    } catch (e) {
      release();
      setState("idle");
      setError(micMessage(e));
    }
  };

  const pick = (e) => {
    const file = e.target.files?.[0];
    e.target.value = "";
    if (!file) return;
    const ext = (file.name.split(".").pop() || "").toLowerCase();
    const known = file.type && (file.type.startsWith("audio/") || file.type === "video/webm");
    const type = known ? file.type : BY_EXTENSION[ext];
    if (!type) { setError("That doesn't look like a voice note. Please choose an audio file."); return; }
    send(known ? file : new Blob([file], { type }), file.name);
  };

  const busy = state !== "idle";
  return (
    <div className="fr-voice">
      <div className="row between">
        <span className="label">{label}</span>
        {state === "recording" && <span className="row small" aria-live="polite"><span className="fr-dot" aria-hidden="true" /> Recording {clock(secs)} of {clock(MAX_SECONDS)}</span>}
      </div>
      <div className="row">
        {state === "recording"
          ? <button type="button" className="btn primary ai" onClick={stop}>Stop and use this</button>
          : <button type="button" className="btn ai" disabled={busy} onClick={start}>Record a voice note</button>}
        <label className={`btn ghost fr-pick ai ${busy ? "off" : ""}`}>
          or upload one
          <input type="file" accept="audio/*,video/webm,.opus,.m4a" disabled={busy} onChange={pick} aria-label="Upload a voice note" />
        </label>
      </div>
      {state === "sending" && <span className="small muted" aria-live="polite">Listening to your voice note…</span>}
      {error && <span className="small" role="alert">{error}</span>}
      <span className="small muted">{hint || "Speak in any language."} Up to {MAX_SECONDS / 60} minutes. Uses one AI token.</span>
    </div>
  );
}

/* ───────── AI Writer ───────── */
const TABS = [["posts", "Posts"], ["reply", "Reply to a customer"], ["ask", "Ask"], ["polish", "English polisher"]];
const EXAMPLES = {
  posts: "Festive season offers, and the question customers ask us most",
  reply: { customer_name: "Priya", phone: "", message: "Hi, I saw your page. What are your rates, and how soon can you start? Please share photos of work you've done." },
  ask: "How do I get more repeat customers this month?",
  polish: "sir aapka payment 2 month se pending hai, please is week clear kar dijiye. next order bhi confirm kar dena",
};
const KINDS = [["whatsapp", "WhatsApp", false], ["post", "Post", false], ["email", "Email", true], ["letter", "Letter", true]];
const TONES = [["warm", "Warm", false], ["firm", "Firm", true], ["formal", "Formal", true]];

const LockIcon = () => (
  <svg width="10" height="10" viewBox="0 0 10 10" aria-hidden="true" style={{ marginLeft: 6, verticalAlign: "-1px" }}>
    <rect x="1.5" y="4.5" width="7" height="5" fill="none" stroke="currentColor" /><path d="M3 4.5V3a2 2 0 0 1 4 0v1.5" fill="none" stroke="currentColor" />
  </svg>
);

function Choice({ options, value, onChange, open, label }) {
  return (
    <div className="fr-seg" role="group" aria-label={label}>
      {options.map(([k, l, member]) => {
        const locked = member && !open;
        return (
          <button key={k} type="button" aria-pressed={value === k} disabled={locked} onClick={() => onChange(k)}
            title={locked ? "Part of Membership" : undefined}>{l}{locked && <LockIcon />}</button>
        );
      })}
    </div>
  );
}

const Saved = () => <p className="small muted">Saved to <Link to="/outputs">My outputs</Link>.</p>;

function Example({ onClick }) {
  return <button type="button" className="btn sm ghost" onClick={onClick}>Try an example</button>;
}

export default function Writer() {
  const { me, refresh } = useHub();
  const [tab, setTab] = useState("posts");
  const [focus, setFocus] = useState("");
  const [msg, setMsg] = useState({ message: "", customer_name: "", phone: "" });
  const [question, setQuestion] = useState("");
  const [pol, setPol] = useState({ text: "", kind: "whatsapp", tone: "warm" });
  const [result, setResult] = useState({});
  const full = !!me.features.polish_voice;

  const run = async (key, path, body, extra = {}) => {
    const r = await api(path, { method: "POST", body });
    setResult((cur) => ({ ...cur, [key]: { ...r, ...extra } }));
    refresh();
  };
  const polished = result.polish;
  const polishedAll = polished ? (polished.subject ? `Subject: ${polished.subject}\n\n${polished.text}` : polished.text) : "";

  return (
    <Sheet kicker="Scale · AI Writer" id="SC-01" pillar="Scale" step={3} title={<>Let the AI do the <em>writing</em>.</>}>
      <div className="panel flat">
        <span className="label">What AI Writer does</span>
        <p>Your writing assistant: it writes posts, replies to customers, answers business questions, and turns rough Hindi/Hinglish/English
          into polished English — all from your Business Brain. Each job uses one AI token and is saved to My outputs.</p>
      </div>
      <Tabs tabs={TABS} value={tab} onChange={setTab} />

      {tab === "posts" && (
        <div className="stack">
          <Field label="Anything to focus on this week? (optional)" hint="e.g. a festival offer, a new service, a question customers often ask">
            <input className="input" value={focus} maxLength={120} onChange={(e) => setFocus(e.target.value)} /></Field>
          <div className="row">
            <Busy className="btn primary ai" run={() => run("posts", "/studio/posts", { focus })}>Write this week's posts</Busy>
            <Example onClick={() => setFocus(EXAMPLES.posts)} />
          </div>
          {result.posts && (
            <>
              <div className="grid2">{result.posts.posts.map((p, i) => (
                <div key={i} className="panel">
                  <div className="row between"><span className="label">{p.day}</span>
                    <Copy text={`${p.hook}\n\n${p.caption}${p.hashtags?.length ? "\n\n" + p.hashtags.join(" ") : ""}`} /></div>
                  <h3>{p.hook}</h3>
                  <p className="small" style={{ whiteSpace: "pre-wrap" }}>{p.caption}</p>
                  {p.hashtags?.length > 0 && <p className="small muted">{p.hashtags.join(" ")}</p>}
                </div>))}
              </div>
              <Saved /><InviteLine />
            </>
          )}
        </div>
      )}

      {tab === "reply" && (
        <div className="stack">
          <div className="grid2">
            <Field label="Customer's name (optional)"><input className="input" maxLength={80} value={msg.customer_name} onChange={(e) => setMsg({ ...msg, customer_name: e.target.value })} /></Field>
            <Field label="Their WhatsApp number (optional)" hint="So Send on WhatsApp opens their chat.">
              <input className="input" inputMode="tel" maxLength={24} value={msg.phone} onChange={(e) => setMsg({ ...msg, phone: e.target.value })} placeholder="98200 12345" /></Field>
          </div>
          <Field label="What the customer wrote">
            <textarea className="textarea" maxLength={2000} value={msg.message} onChange={(e) => setMsg({ ...msg, message: e.target.value })} placeholder="Paste their WhatsApp message" /></Field>
          <div className="row">
            <Busy className="btn primary ai" disabled={msg.message.trim().length < 2}
              run={() => run("reply", "/studio/reply", { message: msg.message, customer_name: msg.customer_name })}>Draft my reply</Busy>
            <TemplatePicker name={msg.customer_name} phone={msg.phone} className="btn ghost" />
            <Example onClick={() => setMsg(EXAMPLES.reply)} />
          </div>
          {result.reply && (
            <div className="panel active">
              <div className="row between"><span className="label">Your reply</span>
                <div className="row"><Copy text={result.reply.reply} />
                  <a className="btn sm" href={waLink(result.reply.reply, msg.phone)} target="_blank" rel="noreferrer">Send on WhatsApp</a></div>
              </div>
              <div className="prompt">{result.reply.reply}</div>
              <Saved /><InviteLine />
            </div>
          )}
        </div>
      )}

      {tab === "ask" && (
        <div className="stack">
          <Field label="Your question" hint="Answered for your business, from your Business Brain.">
            <textarea className="textarea" maxLength={1500} value={question} onChange={(e) => setQuestion(e.target.value)} placeholder="What should I fix first to get more inquiries?" /></Field>
          <div className="row">
            <Busy className="btn primary ai" disabled={question.trim().length < 5} run={() => run("ask", "/studio/ask", { question })}>Get my answer</Busy>
            <Example onClick={() => setQuestion(EXAMPLES.ask)} />
          </div>
          {result.ask && (
            <div className="panel">
              <div className="row between"><span className="label">Answer</span><Copy text={result.ask.answer} /></div>
              <div style={{ whiteSpace: "pre-wrap" }}>{result.ask.answer}</div>
              <Saved /><InviteLine />
            </div>
          )}
        </div>
      )}

      {tab === "polish" && (
        <div className="stack">
          <p className="sub">Write it the way it comes to you — Hindi, Hinglish or rough English. You get clear, respectful English, ready to send.</p>
          <div className="row">
            <span className="label" style={{ minWidth: 70 }}>Turn it into</span>
            <Choice label="Turn it into" options={KINDS} value={pol.kind} open={full} onChange={(kind) => setPol({ ...pol, kind })} />
          </div>
          <div className="row">
            <span className="label" style={{ minWidth: 70 }}>Tone</span>
            <Choice label="Tone" options={TONES} value={pol.tone} open={full} onChange={(tone) => setPol({ ...pol, tone })} />
          </div>
          <Field label="Your words">
            <textarea className="textarea" maxLength={6000} value={pol.text} onChange={(e) => setPol({ ...pol, text: e.target.value })}
              placeholder="sir payment pending hai, please is week clear kar do" /></Field>
          {full
            ? <VoiceInput purpose="polish" label="Or say it" hint="Speak in Hindi, Gujarati, Marathi, Hinglish or any language — you get polished English."
                onText={(t) => setPol((p) => ({ ...p, text: p.text ? `${p.text}\n${t}` : t }))} />
            : <Locked feature="polish_voice" what="Emails, letters, a firm or formal tone, and voice notes" />}
          <div className="row">
            <Busy className="btn primary ai" disabled={pol.text.trim().length < 3} run={() => run("polish", "/studio/polish", pol, { kind: pol.kind })}>Polish my English</Busy>
            <Example onClick={() => setPol({ ...pol, text: EXAMPLES.polish })} />
          </div>
          {polished && (
            <div className="panel active">
              <div className="row between"><span className="label">Polished</span>
                <div className="row">
                  <Copy text={polishedAll} />
                  {polished.kind === "email"
                    ? <a className="btn sm" href={`mailto:?subject=${encodeURIComponent(polished.subject || "")}&body=${encodeURIComponent(polished.text)}`}>Open in email</a>
                    : polished.kind !== "letter" && <a className="btn sm" href={waLink(polished.text)} target="_blank" rel="noreferrer">Send on WhatsApp</a>}
                </div>
              </div>
              {polished.subject && <p><span className="label">Subject</span> {polished.subject}</p>}
              <div className="prompt">{polished.text}</div>
              <Saved /><InviteLine />
            </div>
          )}
        </div>
      )}
    </Sheet>
  );
}
