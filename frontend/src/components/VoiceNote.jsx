import React, { useEffect, useRef, useState } from "react";
import { Locked, canAct, useHub } from "../ui.jsx";
import "../pages/team.css";

/** Record or upload a voice note; the server transcribes it (one AI token) and we hand the text to onText(transcript). */
const MAX_SECONDS = 300;
const MAX_BYTES = 10 * 1024 * 1024;
const RECORD_TYPES = ["audio/webm;codecs=opus", "audio/webm", "audio/mp4", "audio/ogg;codecs=opus", "audio/ogg"];
// Phones sometimes hand over WhatsApp voice notes (.opus) and recordings with no type; name them so the server accepts them.
const BY_EXTENSION = { opus: "audio/ogg", ogg: "audio/ogg", oga: "audio/ogg", m4a: "audio/mp4", mp4: "audio/mp4", aac: "audio/aac",
  mp3: "audio/mpeg", wav: "audio/wav", webm: "audio/webm", flac: "audio/flac" };
const clock = (s) => `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`;

function micMessage(err) {
  const name = err?.name || "";
  if (name === "NotAllowedError" || name === "SecurityError" || name === "PermissionDeniedError")
    return "Your browser blocked the microphone. Allow microphone access for this site (tap the lock next to the web address), or upload a voice note instead.";
  if (name === "NotFoundError" || name === "DevicesNotFoundError") return "No microphone found on this device. Upload a voice note instead.";
  if (name === "NotReadableError") return "Another app is using the microphone. Close it and try again, or upload a voice note instead.";
  return "Recording didn't start. Please upload a voice note instead.";
}

// What each kind of voice note is for: the plan feature and the lowest role that may send it (mirrors VOICE_USES on the server).
const USES = { sop: ["voice_sop", "manager", "Turn a voice note into an SOP", "Explain the steps the way you would to a new person."],
  meeting: ["meetings", "staff", "Meeting notes from a voice note", "Record the meeting, or upload the recording."],
  onboarding: ["business_brain", "owner", "Fill the Business Brain by voice", "Tell us about your business in any language."],
  polish: ["polish_voice", "staff", "Speak in any language, get polished English", "Say it in Hindi, Hinglish, Gujarati, Marathi or English."],
  quote: ["quotations", "manager", "Draft a quotation by voice", "Say the customer's name and each item with quantity and rate."],
  gym: ["training_gym", "staff", "Practise out loud", "Say your reply the way you would to the customer."] };

export default function VoiceNote({ onText, purpose = "sop", hint }) {
  const { me, refresh } = useHub() || {};
  const [state, setState] = useState("idle"); // idle | recording | sending
  const [elapsed, setElapsed] = useState(0);
  const [error, setError] = useState("");
  const [snippet, setSnippet] = useState("");
  const recorder = useRef(null);
  const stream = useRef(null);
  const timer = useRef(null);
  const discard = useRef(false);
  const alive = useRef(true);

  const releaseMic = () => {
    clearInterval(timer.current);
    timer.current = null;
    stream.current?.getTracks().forEach((t) => t.stop());
    stream.current = null;
  };
  const stop = () => {
    clearInterval(timer.current);
    timer.current = null;
    if (recorder.current && recorder.current.state !== "inactive") recorder.current.stop();
    recorder.current = null;
  };
  useEffect(() => () => { alive.current = false; discard.current = true; stop(); releaseMic(); }, []);

  if (!me) return null;
  const [feature, role, lockedWhat, defaultHint] = USES[purpose] || USES.sop;
  if (!me.features?.[feature]) return <Locked feature={feature} what={lockedWhat} />;
  if (!canAct(me, role)) return null;

  const send = async (blob, name) => {
    if (!blob.size) { setError("That recording is empty. Please try again."); return; }
    if (blob.size > MAX_BYTES) { setError("That voice note is too big. Please keep it under 10 MB."); return; }
    setState("sending");
    setError("");
    try {
      const form = new FormData();
      form.append("file", blob, name);
      const res = await fetch(`/api/voice/transcribe?for=${purpose}`, { method: "POST", body: form, credentials: "same-origin" });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) {
        const d = data.detail;
        throw new Error(typeof d === "string" ? d : d?.message || "We couldn't read that voice note. Please try again.");
      }
      if (!alive.current) return;
      setSnippet(data.text);
      onText(data.text);
      refresh?.();
    } catch (e) {
      if (alive.current) setError(e.message || "We couldn't read that voice note. Please try again.");
    } finally {
      if (alive.current) setState("idle");
    }
  };

  const start = async () => {
    setError("");
    setSnippet("");
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
        releaseMic();
        if (discard.current) return;
        const mime = r.mimeType || type || "audio/webm";
        const ext = mime.includes("mp4") ? "m4a" : mime.includes("ogg") ? "ogg" : "webm";
        send(new Blob(chunks, { type: mime }), `voice-note.${ext}`);
      };
      r.start(1000);
      recorder.current = r;
      setElapsed(0);
      setState("recording");
      const began = Date.now();
      timer.current = setInterval(() => {
        const secs = Math.floor((Date.now() - began) / 1000);
        setElapsed(Math.min(secs, MAX_SECONDS));
        if (secs >= MAX_SECONDS) stop();
      }, 500);
    } catch (e) {
      releaseMic();
      setState("idle");
      setError(micMessage(e));
    }
  };

  const pick = (e) => {
    const file = e.target.files?.[0];
    e.target.value = "";
    if (!file) return;
    setSnippet("");
    const ext = (file.name.split(".").pop() || "").toLowerCase();
    const known = file.type && (file.type.startsWith("audio/") || file.type === "video/webm");
    const type = known ? file.type : BY_EXTENSION[ext];
    if (!type) { setError("That doesn't look like a voice note. Please choose an audio file."); return; }
    send(known ? file : new Blob([file], { type }), file.name);
  };

  const busy = state !== "idle";
  return (
    <div className="voice">
      <div className="row between">
        <span className="label">Or talk it through</span>
        {state === "recording" && <span className="row small" aria-live="polite"><span className="rec-dot" aria-hidden="true" /> Recording {clock(elapsed)} of {clock(MAX_SECONDS)}</span>}
      </div>
      <div className="row">
        {state === "recording"
          ? <button type="button" className="btn primary ai" onClick={stop}>Stop and use this</button>
          : <button type="button" className="btn ai" disabled={busy} onClick={start}>Record a voice note</button>}
        <label className={`btn ghost file-pick ai ${busy ? "off" : ""}`}>
          or upload a voice note
          <input type="file" accept="audio/*,video/webm,.opus,.m4a" disabled={busy} onChange={pick} aria-label="Upload a voice note" />
        </label>
      </div>
      {state === "sending" && <span className="small muted" aria-live="polite">Transcribing…</span>}
      {error && <span className="small" role="alert">{error}</span>}
      {snippet && <span className="small muted">Added to your notes: “{snippet.slice(0, 160)}{snippet.length > 160 ? "…" : ""}”</span>}
      <span className="small muted">{hint || defaultHint} Up to {MAX_SECONDS / 60} minutes. Uses one AI token.</span>
    </div>
  );
}
