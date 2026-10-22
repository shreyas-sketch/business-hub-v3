import React, { useEffect, useState } from "react";
import { api } from "../api.js";
import { Busy, Field, FormError, Logo, useHub } from "../ui.jsx";
import { VoiceInput } from "./Writer.jsx";
import "./free.css";

const STEPS = ["Your business", "What you sell", "Your customers", "How customers reach you"];
const EMPTY = { name: "", city: "", industry: "", offers: [{ name: "", price: "", unit: "" }], ideal_customer: "", why_us: "", proof: "", whatsapp: "", email: "", language: "English" };
const LANGS = ["English", "Hinglish", "Hindi", "Gujarati", "Marathi", "Tamil", "Telugu", "Kannada", "Bengali"];
const TEXT_KEYS = ["name", "city", "industry", "ideal_customer", "why_us", "proof", "whatsapp", "email", "language"];
// The 8 website checks (same as the Business Score's website part)
const CHECKS = [["https", "Secure address (https)"], ["mobile", "Works on phones"], ["title", "Title and description for Google"],
  ["contact", "Phone or email visible"], ["whatsapp", "Chat on WhatsApp button"], ["form", "Inquiry form"],
  ["proof", "Reviews or testimonials"], ["light", "Opens fast"]];
const WAYS = [
  ["website", "I have a website", "We read your website and fill everything in. You check it."],
  ["voice", "Tell us by voice", "Talk for a minute about your business — in Hindi, English or your own language."],
  ["type", "Type it", "Write a few lines, the way you'd explain your business to a new customer."],
];

/** What the website reader found: 8 checks that also count in the Business Score. */
function WebsiteChecks({ scan }) {
  const passed = CHECKS.filter(([k]) => scan.checks?.[k]).length;
  return (
    <div className="panel flat">
      <div className="row between">
        <span className="label">What we checked on your website · {passed} of {CHECKS.length}</span>
        <span className="row" style={{ gap: 8 }}>
          {scan.logo && <img className="fr-logo" src={scan.logo} alt="Logo found on your website" />}
          {scan.colour && <span className="fr-swatch" style={{ background: scan.colour }} title={`Your colour ${scan.colour}`} aria-label={`Your colour ${scan.colour}`} />}
        </span>
      </div>
      <ul className="fr-checks">
        {CHECKS.map(([k, label]) => (
          <li key={k}>
            <span className={scan.checks?.[k] ? "ok" : "no"} aria-hidden="true">{scan.checks?.[k] ? "✓" : "✕"}</span>
            <span>{label}<span className="fr-sr">{scan.checks?.[k] ? ": yes" : ": not yet"}</span></span>
          </li>
        ))}
      </ul>
      <span className="small muted">These checks also count in your Business Score.</span>
    </div>
  );
}

export default function Onboarding() {
  const { me, refresh, toast, nav } = useHub();
  const way = new URLSearchParams(window.location.search).get("way");   // /start?way=website opens straight on that way
  const [mode, setMode] = useState(WAYS.some(([k]) => k === way) ? way : me.progress.profile ? "form" : "choose"); // choose | website | voice | type | form
  const [step, setStep] = useState(0);
  const [b, setB] = useState(EMPTY);
  const [busy, setBusy] = useState(false);
  const [url, setUrl] = useState("");
  const [words, setWords] = useState("");
  const [err, setErr] = useState("");
  const [scan, setScan] = useState(null);   // the website that was read: {url, checks, logo, colour}
  const [filled, setFilled] = useState(""); // "website" | "words" once a draft has filled the form
  useEffect(() => { api("/business").then((d) => d.name && setB({ ...EMPTY, ...d, offers: d.offers?.length ? d.offers : EMPTY.offers })).catch(() => {}); }, []);
  const set = (k) => (e) => setB({ ...b, [k]: e.target.value });
  const setOffer = (i, k) => (e) => setB({ ...b, offers: b.offers.map((o, j) => (j === i ? { ...o, [k]: e.target.value } : o)) });
  const canNext = [b.name.trim().length > 1 && b.industry.trim().length > 2, true, true, b.whatsapp.replace(/\D/g, "").length >= 10][step];

  // A draft from the website or the owner's words fills the form; the owner checks every step before saving.
  const fill = (p, how) => {
    setB((cur) => {
      const next = { ...cur };
      for (const k of TEXT_KEYS) if (p?.[k]) next[k] = p[k];
      if (p?.offers?.length) next.offers = p.offers.slice(0, 8).map((o) => ({ name: o.name || "", price: o.price || "", unit: o.unit || "" }));
      return next;
    });
    setFilled(how);
    setStep(0);
    setMode("form");
  };
  const readWebsite = async () => {
    const d = await api("/onboarding/website", { method: "POST", body: { url: url.trim() } });
    setScan({ url: d.url, checks: d.checks || {}, logo: d.logo, colour: d.colour });
    refresh();
    fill(d.profile, "website");
  };
  const readWords = async () => {
    const d = await api("/onboarding/words", { method: "POST", body: { text: words.trim() } });
    refresh();
    fill(d.profile, "words");
  };
  const choose = (m) => { setErr(""); setMode(m); };
  // on a phone the chosen way opens below the three cards: bring it into view
  useEffect(() => { if (["website", "voice", "type"].includes(mode)) document.getElementById("fr-way-panel")?.scrollIntoView({ block: "nearest", behavior: "smooth" }); }, [mode]);

  const finish = async () => {
    setBusy(true);
    try {
      await api("/business", { method: "PUT", body: { ...b, offers: b.offers.filter((o) => o.name.trim()) } });
      await refresh();
      if (me.progress.brand) { toast("Business Brain saved"); nav("/home", { replace: true }); }
      else nav("/brand?auto=1", { replace: true });
    } catch (e) { toast(e.message, "err"); } finally { setBusy(false); }
  };

  const inForm = mode === "form";
  const langs = LANGS.includes(b.language) ? LANGS : [...LANGS, b.language];
  return (
    <div className="public" style={{ maxWidth: 760 }}>
      <header>
        <div className="mark"><Logo h={40} /><span>Set up · {inForm ? `${step + 1} of ${STEPS.length}` : "Start"}</span></div>
        <div className="row">
          <span className="steps-glyphs" aria-hidden="true">{STEPS.map((_, i) => <i key={i} className={inForm && i < step ? "done" : inForm && i === step ? "now" : ""} />)}</span>
          {me.progress.profile && <button className="btn sm ghost" onClick={() => nav("/home")}>Close</button>}
        </div>
      </header>

      {!inForm && (
        <section className="title-sheet" style={{ gap: 20 }}>
          <span className="kicker">Business Brain · Start</span>
          <h1 style={{ fontSize: 36 }}>Tell us about your <em>business</em>.</h1>
          <p className="sub">Pick the easiest way. The AI fills in a draft; you check it and correct anything before it's saved. It only uses what you give it — it never makes up prices or claims.</p>
          <div className="fr-ways">
            {WAYS.map(([k, title, text]) => (
              <button key={k} type="button" className={`fr-way ${mode === k ? "on" : ""}`} aria-pressed={mode === k} onClick={() => choose(k)}>
                <b>{title}</b><span className="small muted">{text}</span>
              </button>
            ))}
          </div>

          {mode === "website" && (
            <div className="stack" id="fr-way-panel">
              <Field label="Your website address" hint="We read the home, about, services and contact pages. Uses one AI run.">
                <input className="input" inputMode="url" autoComplete="url" value={url} onChange={(e) => setUrl(e.target.value)} placeholder="www.yourbusiness.in" autoFocus /></Field>
              <FormError msg={err} />
              <div><Busy className="btn primary ai" disabled={url.trim().length < 4} onError={setErr} run={readWebsite}>Read my website</Busy></div>
            </div>
          )}

          {mode === "voice" && (
            <div className="stack" id="fr-way-panel">
              <VoiceInput purpose="onboarding" label="Your voice note"
                hint="Say what you sell, where, to whom, your prices if you like, and why customers choose you."
                onText={(t) => setWords((w) => (w ? `${w}\n${t}` : t))} />
              {words && <Field label="What we heard" hint="Correct anything we misheard. Filling your Business Brain uses one more AI run.">
                <textarea className="textarea" maxLength={8000} value={words} onChange={(e) => setWords(e.target.value)} /></Field>}
              <FormError msg={err} />
              {words && <div><Busy className="btn primary ai" disabled={words.trim().length < 20} onError={setErr} run={readWords}>Fill my Business Brain</Busy></div>}
            </div>
          )}

          {mode === "type" && (
            <div className="stack" id="fr-way-panel">
              <Field label="Your business in your words" hint="Any language is fine. A few lines are enough. Uses one AI run.">
                <textarea className="textarea" style={{ minHeight: 140 }} maxLength={8000} value={words} onChange={(e) => setWords(e.target.value)} autoFocus
                  placeholder="We are Shree Ganesh Interiors in Thane. We make modular kitchens and wardrobes with our own carpenters, from ₹1,500 per sq ft. Most customers are families who just got a new flat. They choose us for fixed timelines and a clear quotation." /></Field>
              <FormError msg={err} />
              <div><Busy className="btn primary ai" disabled={words.trim().length < 20} onError={setErr} run={readWords}>Fill my Business Brain</Busy></div>
            </div>
          )}

          <div className="row between">
            <button type="button" className="linkbtn small" onClick={() => { setFilled(""); setStep(0); setMode("form"); }}>Fill the form myself</button>
            {me.progress.profile && <span className="small muted">Your saved Business Brain stays as it is until you save.</span>}
          </div>
        </section>
      )}

      {inForm && (
        <section className="title-sheet" style={{ gap: 20 }}>
          <span className="kicker">Business Brain · {STEPS[step]}</span>
          {filled && step === 0 && (
            <div className="notice"><span>{filled === "website" ? "We filled this from your website." : "We filled this from your words."} Check each of the 4 steps and correct anything before you save.</span></div>
          )}
          {step === 0 && <>
            <h1 style={{ fontSize: 36 }}>Tell us about your <em>business</em>.</h1>
            <p className="sub">Four short steps. The AI only uses what you write here — it never makes up prices or claims.</p>
            {scan && <WebsiteChecks scan={scan} />}
            <Field label="Business name"><input className="input" value={b.name} onChange={set("name")} placeholder="Shree Ganesh Interiors" autoFocus /></Field>
            <Field label="City or area you serve"><input className="input" value={b.city} onChange={set("city")} placeholder="Thane, Mumbai" /></Field>
            <Field label="What does your business do?" hint="One line, the way you'd say it to a customer.">
              <input className="input" value={b.industry} onChange={set("industry")} placeholder="Home and office interiors with our own carpenters" /></Field>
            <button type="button" className="linkbtn small" style={{ justifySelf: "start" }} onClick={() => choose("choose")}>Read it from your website or a voice note instead</button>
          </>}
          {step === 1 && <>
            <h1 style={{ fontSize: 36 }}>What do you <em>sell</em>?</h1>
            <p className="sub">Add your main products or services. Prices are optional — leave them empty if you quote case by case.</p>
            {b.offers.map((o, i) => (
              <div key={i} className="grid3" style={{ gridTemplateColumns: "2fr 1fr 1fr" }}>
                <input className="input" value={o.name} onChange={setOffer(i, "name")} placeholder={i === 0 ? "Modular kitchen" : "Another product or service"} aria-label="Product or service" />
                <input className="input" value={o.price} onChange={setOffer(i, "price")} placeholder="₹ price (optional)" aria-label="Price" />
                <input className="input" value={o.unit} onChange={setOffer(i, "unit")} placeholder="per sq ft, onwards…" aria-label="Unit" />
              </div>
            ))}
            {b.offers.length < 8 && <button className="btn sm ghost" style={{ justifySelf: "start" }} onClick={() => setB({ ...b, offers: [...b.offers, { name: "", price: "", unit: "" }] })}>Add another</button>}
          </>}
          {step === 2 && <>
            <h1 style={{ fontSize: 36 }}>Who buys, and <em>why</em> you?</h1>
            <Field label="Your best customers" hint="e.g. Families who just got possession of a 2/3BHK"><input className="input" value={b.ideal_customer} onChange={set("ideal_customer")} /></Field>
            <Field label="Why customers choose you" hint="One reason per line. Only things that are true."><textarea className="textarea" value={b.why_us} onChange={set("why_us")} placeholder={"Fixed timelines\nTransparent quotation\nOwn factory"} /></Field>
            <Field label="Proof you can show (optional)" hint="Years in business, projects done, certifications — only if true."><input className="input" value={b.proof} onChange={set("proof")} /></Field>
          </>}
          {step === 3 && <>
            <h1 style={{ fontSize: 36 }}>Where should leads <em>reach</em> you?</h1>
            <Field label="WhatsApp number for customers" hint={`Website inquiries alert you on ${me.user.phone}. Customers will message this number.`}>
              <input className="input" inputMode="tel" value={b.whatsapp} onChange={set("whatsapp")} placeholder="98200 00000" /></Field>
            <Field label="Email on your website (optional)"><input className="input" type="email" value={b.email} onChange={set("email")} /></Field>
            <Field label="Language your customers prefer">
              <select className="select" value={b.language} onChange={set("language")}>
                {langs.map((l) => <option key={l}>{l}</option>)}
              </select></Field>
          </>}
          <div className="row between">
            {step > 0 ? <button className="btn ghost" onClick={() => setStep(step - 1)}>Back</button> : <span />}
            {step < STEPS.length - 1
              ? <button className="btn primary" disabled={!canNext} onClick={() => setStep(step + 1)}>Next</button>
              : <button className="btn primary" disabled={!canNext || busy} onClick={finish}>{busy ? "Saving…" : me.progress.brand ? "Save" : "Write my brand message"}</button>}
          </div>
        </section>
      )}
    </div>
  );
}
