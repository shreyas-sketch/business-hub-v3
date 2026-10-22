import React, { useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { api, attribution } from "../api.js";
import { Logo, useHub } from "../ui.jsx";

/** Two ways in: a 6-digit code on WhatsApp/SMS, or an email and password (for when a code can't reach you). */
export default function Login() {
  const { refresh, toast, nav } = useHub();
  const [params] = useSearchParams();
  const [way, setWay] = useState(params.get("way") === "email" ? "email" : "mobile");
  const [phone, setPhone] = useState("");
  const [code, setCode] = useState("");
  const [sent, setSent] = useState(null);
  const [codeFailed, setCodeFailed] = useState(false);
  const [busy, setBusy] = useState(false);
  const [mode, setMode] = useState(params.get("signup") ? "register" : "login");
  const [f, setF] = useState({ name: "", email: "", password: "", phone: "" });

  const done = async () => {
    sessionStorage.removeItem("ah_attr");
    await refresh();
    const next = params.get("next") || "";
    nav(/^\/[a-z]/.test(next) ? next : "/", { replace: true });  // only paths inside the hub; "/" goes to the owner's landing page
  };
  const send = async (e) => {
    e?.preventDefault(); setBusy(true); setCodeFailed(false);
    try { setSent(await api("/auth/otp", { method: "POST", body: { phone } })); }
    catch (err) { toast(err.message, "err"); setCodeFailed(err.message.includes("couldn't send")); } finally { setBusy(false); }
  };
  const verify = async (e) => {
    e.preventDefault(); setBusy(true);
    const a = attribution();
    try {
      await api("/auth/verify", { method: "POST", body: { phone, code, ref: a.ref, src: a.src, cohort: a.c } });
      await done();
    } catch (err) { toast(err.message, "err"); } finally { setBusy(false); }
  };
  const withEmail = async (e) => {
    e.preventDefault(); setBusy(true);
    const a = attribution();
    try {
      if (mode === "register") {
        await api("/auth/register", { method: "POST", body: { ...f, ref: a.ref, src: a.src, cohort: a.c } });
      } else {
        await api("/auth/login", { method: "POST", body: { email: f.email, password: f.password } });
      }
      await done();
    } catch (err) { toast(err.message, "err"); } finally { setBusy(false); }
  };
  const set = (k) => (e) => setF({ ...f, [k]: e.target.value });
  const switchTo = (w) => { setWay(w); setSent(null); setCode(""); setCodeFailed(false); };

  const title = way === "email"
    ? (mode === "register" ? <>Create your <em>free</em> hub.</> : <>Log in with your <em>email</em>.</>)
    : sent ? <>Enter the <em>code</em>.</> : <>Log in with your <em>mobile</em>.</>;

  return (
    <div className="public" style={{ maxWidth: 680 }}>
      <header><Link to="/" className="mark"><Logo h={40} /><span>Back</span></Link></header>
      <section className="title-sheet login-sheet">
        <span className="kicker">{way === "mobile" ? (sent ? "Step 2 of 2" : "Step 1 of 2") : mode === "register" ? "New account" : "Welcome back"}</span>
        <h1 style={{ fontSize: 38 }}>{title}</h1>
        {!sent && (
          <div className="tabs" role="tablist" aria-label="How to log in">
            {[["mobile", "Mobile number"], ["email", "Email"]].map(([k, l]) => (
              <button key={k} type="button" role="tab" aria-selected={way === k} className={way === k ? "on" : ""} onClick={() => switchTo(k)}>{l}</button>
            ))}
          </div>
        )}

        {way === "mobile" && !sent && (
          <form className="stack" onSubmit={send}>
            <label className="field"><span className="label">Mobile number</span>
              <input className="input" inputMode="tel" autoComplete="tel" placeholder="98200 00000" value={phone} onChange={(e) => setPhone(e.target.value)} required autoFocus />
            </label>
            <p className="hint">We'll send a 6-digit code on WhatsApp (or SMS).</p>
            <button className="btn primary" disabled={busy}>{busy ? "Sending…" : "Send code"}</button>
            {codeFailed && <p className="prompt">The code couldn't be sent just now. <button type="button" className="linkbtn" onClick={() => switchTo("email")}>Use your email instead</button>.</p>}
          </form>
        )}
        {way === "mobile" && sent && (
          <form className="stack" onSubmit={verify}>
            {sent.via === "demo" ? <p className="sub">This is a demo number, so the code is shown here instead of being sent.</p>
              : <p className="sub">Sent to {phone} on {sent.via.includes("sms") ? "SMS" : "WhatsApp"}.</p>}
            {sent.dev_code && <p className="prompt mono">{sent.via === "demo" ? "Demo" : "Development mode"} — code: {sent.dev_code}</p>}
            <input className="input mono" inputMode="numeric" autoComplete="one-time-code" maxLength={6} placeholder="6-digit code"
              value={code} onChange={(e) => setCode(e.target.value.replace(/\D/g, ""))} required autoFocus style={{ letterSpacing: ".3em", fontSize: 20 }} />
            <button className="btn primary" disabled={busy || code.length !== 6}>{busy ? "Checking…" : "Log in"}</button>
            <button type="button" className="btn ghost" onClick={() => { setSent(null); setCode(""); }}>Change number</button>
          </form>
        )}

        {way === "email" && (
          <form className="stack" onSubmit={withEmail}>
            {mode === "register" && (
              <label className="field"><span className="label">Your name</span>
                <input className="input" autoComplete="name" maxLength={80} value={f.name} onChange={set("name")} placeholder="Rakesh Patil" />
              </label>
            )}
            <label className="field"><span className="label">Email</span>
              <input className="input" type="email" autoComplete="email" maxLength={254} value={f.email} onChange={set("email")} required autoFocus placeholder="you@business.in" />
            </label>
            <label className="field"><span className="label">Password</span>
              <input className="input" type="password" autoComplete={mode === "register" ? "new-password" : "current-password"} minLength={mode === "register" ? 8 : undefined}
                maxLength={200} value={f.password} onChange={set("password")} required placeholder={mode === "register" ? "At least 8 characters" : ""} />
            </label>
            {mode === "register" && (
              <label className="field"><span className="label">Mobile number</span>
                <input className="input" inputMode="tel" autoComplete="tel" value={f.phone} onChange={set("phone")} required placeholder="98200 00000" />
                <span className="hint">Your WhatsApp number: new inquiries and alerts come here. You can also log in with it later.</span>
              </label>
            )}
            <button className="btn primary" disabled={busy}>{busy ? "One moment…" : mode === "register" ? "Create my free hub" : "Log in"}</button>
            <p className="hint">
              {mode === "register"
                ? <>Already have an account? <button type="button" className="linkbtn" onClick={() => setMode("login")}>Log in</button></>
                : <>New here? <button type="button" className="linkbtn" onClick={() => setMode("register")}>Create a free account</button></>}
            </p>
          </form>
        )}
      </section>
    </div>
  );
}
