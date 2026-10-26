import React, { useEffect, useRef, useState } from "react";
import { api } from "../api.js";
import { Busy, FormError, Logo } from "../ui.jsx";
import "./data.css";

/* v2.3 Data engine — the key-person questionnaire an employee fills in on their phone, from the link the owner sent.
   No login (outside the hub's shell). Answers save as they type; "Send my answers" hands them to the owner. */

export default function KeyPersonForm() {
  const token = window.location.pathname.split("/")[2] || "";
  const [f, setF] = useState(null);
  const [err, setErr] = useState("");
  const [vals, setVals] = useState({});
  const [saved, setSaved] = useState("");
  const [sent, setSent] = useState(false);
  const dirty = useRef(false);

  useEffect(() => {
    api(`/kp/${encodeURIComponent(token)}`).then((d) => { setF(d); setVals(d.answers || {}); setSent(d.submitted); })
      .catch((e) => setErr(e.message));
  }, [token]);

  // save a draft a moment after they stop typing
  useEffect(() => {
    if (!f || sent || !dirty.current) return undefined;
    const t = setTimeout(async () => {
      try { await api(`/kp/${encodeURIComponent(token)}`, { method: "PUT", body: { answers: vals } }); setSaved("Saved"); dirty.current = false; }
      catch (e) { setSaved(""); setErr(e.message); }
    }, 1500);
    return () => clearTimeout(t);
  }, [vals]);

  if (!f) {
    return (
      <div className="kpf">
        <header><Logo h={30} /></header>
        {err ? <><h1>This link isn't working.</h1><p className="sub">{err}</p></> : <p className="muted">Loading…</p>}
      </div>
    );
  }
  const answered = f.questions.filter((q) => (vals[q.key] || "").trim()).length;
  if (sent) {
    return (
      <div className="kpf">
        <header><Logo h={30} /><span className="small muted">{f.business}</span></header>
        <h1>Thank you, {f.first_name}.</h1>
        <p className="sub">Your answers are with {f.business}. They'll use them to write down how your work is done.</p>
      </div>
    );
  }
  let sec = null;
  return (
    <div className="kpf">
      <header><Logo h={30} /><span className="small muted">{f.business}</span></header>
      <div className="stack" style={{ gap: 10 }}>
        <span className="kicker">{f.role}</span>
        <h1>Hi {f.first_name}, how do you do your work?</h1>
        <p className="small note">{f.note}</p>
        <p className="small muted">Write the way you'd explain it to a new colleague — short lines are fine. Your answers save as you type, so you can come back later.
          {" "}<a href={`/api/kp/${encodeURIComponent(token)}/questions.pdf`}>Download the questions (PDF)</a></p>
      </div>
      {f.questions.map((q, i) => {
        const head = q.section !== sec ? (sec = q.section) : null;
        return (
          <div key={q.key} className="kp-q">
            {head && <div className="kp-sec">{head}</div>}
            <label className="small" htmlFor={`kp-${q.key}`}><b>{i + 1}. {q.q}</b></label>
            {q.hint && <span className="small muted">{q.hint}</span>}
            <textarea id={`kp-${q.key}`} className="textarea" maxLength={f.max_answer || 3000} value={vals[q.key] || ""}
              onChange={(e) => { dirty.current = true; setSaved("Saving…"); setVals({ ...vals, [q.key]: e.target.value }); }} />
          </div>
        );
      })}
      <FormError msg={err} />
      <div className="kpf-send">
        <span className="small muted">{answered} of {f.questions.length} answered{saved ? ` · ${saved}` : ""}</span>
        <Busy className="btn primary" disabled={answered < 3} onError={(e) => setErr(e.message)}
          run={async () => { await api(`/kp/${encodeURIComponent(token)}`, { method: "PUT", body: { answers: vals, submit: true } }); setSent(true); }}>
          Send my answers</Busy>
      </div>
    </div>
  );
}
