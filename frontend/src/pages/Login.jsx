import React, { useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { api, attribution } from "../api.js";
import { Logo, useHub } from "../ui.jsx";

export default function Login() {
  const { refresh, toast, nav } = useHub();
  const [params] = useSearchParams();
  const [phone, setPhone] = useState("");
  const [code, setCode] = useState("");
  const [sent, setSent] = useState(null);
  const [busy, setBusy] = useState(false);

  const send = async (e) => {
    e?.preventDefault(); setBusy(true);
    try { setSent(await api("/auth/otp", { method: "POST", body: { phone } })); }
    catch (err) { toast(err.message, "err"); } finally { setBusy(false); }
  };
  const verify = async (e) => {
    e.preventDefault(); setBusy(true);
    const a = attribution();
    try {
      await api("/auth/verify", { method: "POST", body: { phone, code, ref: a.ref, src: a.src, cohort: a.c } });
      sessionStorage.removeItem("ah_attr");
      await refresh();
      const next = params.get("next") || "";
      nav(/^\/[a-z]/.test(next) ? next : "/", { replace: true });  // only paths inside the hub; "/" goes to the owner's landing page
    } catch (err) { toast(err.message, "err"); } finally { setBusy(false); }
  };

  return (
    <div className="public" style={{ maxWidth: 560 }}>
      <header><Link to="/" className="mark"><Logo h={40} /><span>Back</span></Link></header>
      <section className="title-sheet">
        <span className="kicker">{sent ? "Step 2 of 2" : "Step 1 of 2"}</span>
        <h1 style={{ fontSize: 38 }}>{sent ? <>Enter the <em>code</em>.</> : <>Log in with your <em>mobile</em>.</>}</h1>
        {!sent ? (
          <form className="stack" onSubmit={send}>
            <label className="field"><span className="label">Mobile number</span>
              <input className="input" inputMode="tel" autoComplete="tel" placeholder="98200 00000" value={phone} onChange={(e) => setPhone(e.target.value)} required autoFocus />
            </label>
            <p className="hint">We'll send a 6-digit code on WhatsApp (or SMS).</p>
            <button className="btn primary" disabled={busy}>{busy ? "Sending…" : "Send code"}</button>
          </form>
        ) : (
          <form className="stack" onSubmit={verify}>
            <p className="sub">Sent to {phone} on {sent.via.includes("sms") ? "SMS" : "WhatsApp"}.</p>
            {sent.dev_code && <p className="prompt mono">Development mode — code: {sent.dev_code}</p>}
            <input className="input mono" inputMode="numeric" autoComplete="one-time-code" maxLength={6} placeholder="6-digit code"
              value={code} onChange={(e) => setCode(e.target.value.replace(/\D/g, ""))} required autoFocus style={{ letterSpacing: ".3em", fontSize: 20 }} />
            <button className="btn primary" disabled={busy || code.length !== 6}>{busy ? "Checking…" : "Log in"}</button>
            <button type="button" className="btn ghost" onClick={() => { setSent(null); setCode(""); }}>Change number</button>
          </form>
        )}
      </section>
    </div>
  );
}
