import React, { useEffect, useRef, useState } from "react";
import { Link, Route, Routes, useNavigate, useParams, useSearchParams } from "react-router-dom";
import { api } from "../api.js";
import { FormError, useHub } from "../ui.jsx";
import "./free.css";
import "./day1.css";

/* The ₹99 webinar tool, no login: /report?c=CODE → building → ready → "save with WhatsApp code" → the full report.
   The server keeps the run, so closing the tab loses nothing. */

const MONEY = /(₹\s?\d[\d,]*(?:\.\d+)?(?:\s?(?:L|Cr|lakh|crore)\b)?(?:\s?[–-]\s?\d[\d,]*(?:\.\d+)?(?:\s?(?:L|Cr|lakh|crore)\b)?)?)/;

/** Report text: [systems words] in aqua, rupee amounts in amber. */
export function Rich({ text }) {
  return (text || "").split(/\[([^[\]]*)\]/).map((part, i) =>
    i % 2 ? <span key={i} className="d1-sys">{part}</span>
      : part.split(MONEY).map((p, j) => (j % 2 ? <span key={`${i}-${j}`} className="money">{p}</span> : <React.Fragment key={`${i}-${j}`}>{p}</React.Fragment>)));
}

function Frame({ program, children }) {
  return (
    <div className="public d1">
      <header><span className="mark"><b>{program || "Business AI"}</b><span>Your report</span></span></header>
      <section className="panel d1-sheet">{children}</section>
      <p className="small muted d1-foot">No login, nothing to install. Only this report and its save link are ever sent to your WhatsApp.</p>
    </div>
  );
}

function Seg({ label, value, options, onChange }) {
  return (
    <div className="fr-seg d1-seg" role="group" aria-label={label}>
      {options.map((o) => <button key={o.value} type="button" aria-pressed={value === o.value} onClick={() => onChange(o.value)}>{o.label}</button>)}
    </div>
  );
}

const STEPS = ["You", "Website", "10 questions", "Your numbers"];

function Form() {
  const [params] = useSearchParams();
  const nav = useNavigate();
  const code = params.get("c") || "";
  const [form, setForm] = useState(null);
  const [step, setStep] = useState(0);
  const [err, setErr] = useState("");
  const [busy, setBusy] = useState(false);
  const [v, setV] = useState({ phone: "", name: "", city: "", website: "", noSite: false, consent: false, answers: {}, numbers: {} });
  const top = useRef(null);
  useEffect(() => { api(`/day1/form?c=${encodeURIComponent(code)}`).then(setForm).catch(() => setForm({ valid: false })); }, [code]);
  useEffect(() => { top.current?.focus(); }, [step]);
  if (!form) return <Frame><p className="muted">Loading…</p></Frame>;
  if (!form.valid || !form.open) {
    return (
      <Frame program={form.program}>
        <span className="kicker">{form.program || "Business AI"} · your report</span>
        <h1>{form.valid ? <>This report is <em>closed</em>.</> : <>This link isn't <em>valid</em>.</>}</h1>
        <p className="sub">{form.valid ? "The report for this webinar has closed." : "Please use the link shared in the webinar."}</p>
      </Frame>
    );
  }
  const set = (patch) => { setV({ ...v, ...patch }); setErr(""); };
  const digits = v.phone.replace(/\D/g, "").replace(/^(91|0)(?=\d{10}$)/, "");
  const ok = [
    /^[6-9]\d{9}$/.test(digits) && v.name.trim().length > 0 && v.consent,
    v.noSite || v.website.trim().length > 3,
    form.questions.every((q) => v.answers[q.key]),
    form.numbers.every((n) => v.numbers[n.key] !== undefined && v.numbers[n.key] !== ""),
  ];
  const why = ["Add your WhatsApp number and first name, and tick the box.", "Add your website, or tick “I don't have a website”.",
    "Answer every question.", "Answer every number — your best guess is fine."][step];
  const next = () => { if (!ok[step]) { setErr(why); return; } setErr(""); setStep(step + 1); window.scrollTo({ top: 0 }); };
  const submit = async () => {
    if (!ok[3]) { setErr(why); return; }
    setBusy(true); setErr("");
    try {
      const r = await api("/day1/runs", { method: "POST", body: { cohort: form.cohort, phone: digits, name: v.name, city: v.city || null,
        website: v.noSite ? null : v.website, answers: v.answers, numbers: v.numbers, consent: v.consent } });
      nav(`/report/r/${r.token}`, { state: { eta: r.eta_seconds } });
    } catch (e) { setErr(e.message); } finally { setBusy(false); }
  };

  return (
    <Frame program={form.program}>
      <span className="kicker">{form.program} · your report</span>
      <h1 tabIndex={-1} ref={top}>{["Where does your business ", "Does your website ", "How does the business ", "Your "][step]}<em>{["stand", "work for you", "run", "numbers"][step]}</em>?</h1>
      <ol className="d1-steps" aria-label="Steps">{STEPS.map((s, i) => <li key={s} className={i < step ? "done" : i === step ? "now" : ""}>{s}</li>)}</ol>

      {step === 0 && (
        <div className="stack">
          <p className="sub">Three minutes, no login. Your one-page report comes to your WhatsApp: a score, what you're missing in rupees, and what to fix first.</p>
          <label className="field"><span className="label">WhatsApp number</span>
            <input className="input" inputMode="tel" autoComplete="tel" placeholder="98200 00000" value={v.phone} onChange={(e) => set({ phone: e.target.value })} />
            <span className="hint">10 digits. The report is sent here, and nothing else.</span></label>
          <div className="grid2">
            <label className="field"><span className="label">First name</span>
              <input className="input" autoComplete="given-name" placeholder="Riya" value={v.name} onChange={(e) => set({ name: e.target.value })} /></label>
            <label className="field"><span className="label">City (optional)</span>
              <input className="input" autoComplete="address-level2" placeholder="Pune" value={v.city} onChange={(e) => set({ city: e.target.value })} /></label>
          </div>
          <label className="d1-consent"><input type="checkbox" checked={v.consent} onChange={(e) => set({ consent: e.target.checked })} /><span>{form.consent}</span></label>
        </div>
      )}

      {step === 1 && (
        <div className="stack">
          <p className="sub">We check {form.website_checks} things on your website: is it secure, does it work on a phone, can a visitor reach you. Nothing else is read or kept.</p>
          <label className="field"><span className="label">Your website</span>
            <input className="input" inputMode="url" placeholder="www.yourbusiness.in" value={v.website} disabled={v.noSite} onChange={(e) => set({ website: e.target.value })} /></label>
          <label className="d1-consent"><input type="checkbox" checked={v.noSite} onChange={(e) => set({ noSite: e.target.checked, website: "" })} /><span>I don't have a website</span></label>
        </div>
      )}

      {step === 2 && (
        <div>
          <p className="sub">Answer honestly — yes, partly or no.</p>
          {form.questions.map((q) => (
            <div className="fr-q" key={q.key}>
              <div className="stack" style={{ gap: 4 }}><span className="label">{q.area}</span><p>{q.q}</p></div>
              <Seg label={q.area} value={v.answers[q.key]} options={form.answers} onChange={(a) => set({ answers: { ...v.answers, [q.key]: a } })} />
            </div>
          ))}
        </div>
      )}

      {step === 3 && (
        <div className="stack">
          <p className="sub">These work out what you're missing in rupees. Your best guess is fine.</p>
          {form.numbers.map((n) => (
            <div className="fr-q" key={n.key}>
              <div className="stack" style={{ gap: 4 }}><span className="label">{n.label}</span>{n.hint && <span className="hint">{n.hint}</span>}</div>
              {n.kind === "choice"
                ? <Seg label={n.label} value={v.numbers[n.key]} options={n.choices} onChange={(a) => set({ numbers: { ...v.numbers, [n.key]: a } })} />
                : <input className="input d1-num" type="number" inputMode="numeric" min={n.min} max={n.max} aria-label={n.label}
                    placeholder={n.key === "aov" ? "₹" : "0"} value={v.numbers[n.key] ?? ""}
                    onChange={(e) => set({ numbers: { ...v.numbers, [n.key]: e.target.value === "" ? "" : Number(e.target.value) } })} />}
            </div>
          ))}
        </div>
      )}

      <FormError msg={err} />
      <div className="row">
        {step > 0 && <button className="btn ghost" type="button" onClick={() => { setStep(step - 1); setErr(""); }}>Back</button>}
        {step < 3 ? <button className="btn primary" type="button" onClick={next}>Next</button>
          : <button className="btn primary" type="button" disabled={busy} onClick={submit}>{busy ? "Sending…" : "Make my report"}</button>}
      </div>
    </Frame>
  );
}

function Status() {
  const { token } = useParams();
  const [run, setRun] = useState(null);
  const [gone, setGone] = useState(false);
  useEffect(() => {
    let stop = false;
    let timer;
    const tick = async () => {
      try {
        const r = await api(`/day1/runs/${token}`);
        if (stop) return;
        setRun(r);
        if (r.status === "queued" || r.status === "building") timer = setTimeout(tick, 2000);
      } catch { if (!stop) setGone(true); }
    };
    tick();
    return () => { stop = true; clearTimeout(timer); };
  }, [token]);
  if (gone) return <Frame><h1>We couldn't find this <em>report</em>.</h1></Frame>;
  if (!run) return <Frame><p className="muted">Loading…</p></Frame>;
  if (run.status === "queued" || run.status === "building") {
    return (
      <Frame>
        <span className="kicker">Your report</span>
        <h1>Building your report<em>…</em></h1>
        <p className="sub" role="status">About {run.eta_seconds} seconds. You can close this page — the report is saved and comes to your WhatsApp.</p>
        <div className="d1-wait" aria-hidden="true"><i /></div>
      </Frame>
    );
  }
  if (run.status === "failed") {
    return <Frame><h1>{run.first_name}, this report couldn't be <em>built</em>.</h1><p className="sub">{run.error}</p></Frame>;
  }
  return (
    <Frame>
      <span className="kicker">{run.eyebrow}</span>
      <h1>{run.first_name}, here is where you <em>stand</em>.</h1>
      <div className="grid2" style={{ alignItems: "start" }}>
        <div className="panel">
          <span className="label">{run.number_label}</span>
          <div className="fr-score"><span className="num" style={{ fontSize: 56 }}>{run.score}</span><span className="of">/100</span></div>
          <span className="small muted">{run.band}</span>
        </div>
        <div className="panel">
          <span className="label">What you're missing · Estimate</span>
          <p className="d1-gap"><Rich text={run.gap_line} /></p>
        </div>
      </div>
      <div className="panel flat">
        <span className="label">Where you're lacking</span>
        <ol className="d1-list">{run.findings.map((f) => <li key={f}>{f}</li>)}</ol>
      </div>
      <p className="sub">Your full one-page report — the arithmetic, what to fix and what you'd have in 30 days — comes to your WhatsApp as an image.</p>
      <Link className="btn primary" to={`/report/save/${token}`}>{run.saved ? "See the full report" : "See the full report now"}</Link>
    </Frame>
  );
}

function Save() {
  const { token } = useParams();
  const { refresh } = useHub();
  const [full, setFull] = useState(null);
  const [sent, setSent] = useState(null);
  const [code, setCode] = useState("");
  const [err, setErr] = useState("");
  const [busy, setBusy] = useState(false);
  const [checked, setChecked] = useState(false);
  const load = () => api(`/day1/runs/${token}/full`).then((r) => { setFull(r); return r; });
  useEffect(() => { load().catch(() => {}).finally(() => setChecked(true)); }, [token]);  // eslint-disable-line react-hooks/exhaustive-deps

  const send = async () => {
    setBusy(true); setErr("");
    try { setSent(await api(`/day1/runs/${token}/code`, { method: "POST" })); } catch (e) { setErr(e.message); } finally { setBusy(false); }
  };
  const verify = async (e) => {
    e.preventDefault(); setBusy(true); setErr("");
    try { await api(`/day1/runs/${token}/verify`, { method: "POST", body: { code } }); await load(); refresh(); }
    catch (e2) { setErr(e2.message); } finally { setBusy(false); }
  };

  if (!checked) return <Frame><p className="muted">Loading…</p></Frame>;
  if (full) {
    const r = full.report;
    return (
      <Frame>
        <span className="kicker">{r ? r.eyebrow : "Your report"}</span>
        <h1>Saved. Here is your full <em>report</em>.</h1>
        {full.status === "done" ? (
          <>
            <div className="d1-full">
              <img src={full.png_url} alt={`${r.title}. Score ${full.score} out of 100. ${r.missing.line}.`} width="1080" height="1920" />
              <div className="stack">
                <a className="btn primary" href={full.png_url} download>Download the image</a>
                <a className="btn" href={full.pdf_url} target="_blank" rel="noopener">Open the PDF</a>
                <p className="small muted">It's saved in your free Business AI Action Hub. Set up your Business Brain next — the hub reads your website and builds you a page that catches enquiries.</p>
                <Link className="btn money" to="/start">Open my free hub</Link>
              </div>
            </div>
          </>
        ) : <p className="sub">Your report is still being built. It will appear here in a moment — refresh this page.</p>}
      </Frame>
    );
  }
  return (
    <Frame>
      <span className="kicker">Save your report</span>
      <h1>Save it with your WhatsApp <em>code</em>.</h1>
      <p className="sub">We send a 6-digit code to the WhatsApp number you typed for this report. It saves the full report — image and PDF — in a free account. No password.</p>
      {!sent ? (
        <button className="btn primary" disabled={busy} onClick={send}>{busy ? "Sending…" : "Send my code"}</button>
      ) : (
        <form className="stack" onSubmit={verify}>
          <p className="small muted">Sent on {sent.via.includes("sms") ? "SMS" : "WhatsApp"}. It works for 10 minutes.</p>
          {sent.dev_code && <p className="prompt mono">Development mode — code: {sent.dev_code}</p>}
          <input className="input mono" inputMode="numeric" autoComplete="one-time-code" maxLength={6} placeholder="6-digit code" aria-label="6-digit code"
            value={code} onChange={(e) => setCode(e.target.value.replace(/\D/g, ""))} autoFocus style={{ letterSpacing: ".3em", fontSize: 20 }} />
          <div className="row">
            <button className="btn primary" disabled={busy || code.length !== 6}>{busy ? "Checking…" : "Save my report"}</button>
            <button type="button" className="btn ghost" disabled={busy} onClick={send}>Send a new code</button>
          </div>
        </form>
      )}
      <FormError msg={err} />
    </Frame>
  );
}

export default function Day1() {
  return (
    <Routes>
      <Route path="/report" element={<Form />} />
      <Route path="/report/r/:token" element={<Status />} />
      <Route path="/report/save/:token" element={<Save />} />
      <Route path="*" element={<Form />} />
    </Routes>
  );
}
