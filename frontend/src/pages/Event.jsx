import React, { useEffect, useRef, useState } from "react";
import { Route, Routes, useNavigate, useParams, useSearchParams } from "react-router-dom";
import { api } from "../api.js";
import { FormError, Logo } from "../ui.jsx";
import "./free.css";
import "./day1.css";
import "./event.css";
import CompetitorInputs, { cleanCompetitors } from "../components/Competitors.jsx";

/* The live event link, no login: /event?e=CODE → website + GST + 3 taps → the pack builds in front of them →
   "Save it" with email + WhatsApp code → their hub, with everything inside. The server keeps the pack. */

function Frame({ children, wide }) {
  return (
    <div className={`public d1 ev${wide ? " ev-wide" : ""}`}>
      <header><span className="mark"><Logo h={40} /></span></header>
      {children}
      <p className="small muted d1-foot">No login until you choose to save. Nothing is sent to anyone from this page.</p>
    </div>
  );
}

function Seg({ label, value, options, onChange }) {
  return (
    <div className="fr-seg d1-seg" role="group" aria-label={label}>
      {options.map((o) => <button key={String(o.value)} type="button" aria-pressed={value === o.value} onClick={() => onChange(o.value)}>{o.label}</button>)}
    </div>
  );
}

const GST_RE = /^\d{2}[A-Z]{5}\d{4}[A-Z][1-9A-Z]Z[0-9A-Z]$/;

function Form() {
  const [params] = useSearchParams();
  const nav = useNavigate();
  const code = params.get("e") || "";
  const [form, setForm] = useState(null);
  const [v, setV] = useState({ website: "", noSite: false, business_name: "", about: "", city: "", gstin: "", noGst: false, state: "",
    entity: "", gst_scheme: "monthly", staff: "", consent: false, competitors: [""] });
  const [gst, setGst] = useState(null);
  const [err, setErr] = useState("");
  const [busy, setBusy] = useState(false);
  useEffect(() => { api(`/event/form?e=${encodeURIComponent(code)}`).then(setForm).catch(() => setForm({ valid: false })); }, [code]);
  const g = v.gstin.replace(/\s+/g, "").toUpperCase();
  useEffect(() => {
    if (v.noGst || !GST_RE.test(g)) { setGst(null); return undefined; }
    let stop = false;
    api("/event/gstin", { method: "POST", body: { gstin: g } }).then((r) => {
      if (stop) return;
      setGst(r);
      if (r.valid && r.entities.length === 1) setV((cur) => ({ ...cur, entity: r.entities[0].value }));
      else if (r.valid && !r.entities.some((e) => e.value === v.entity)) setV((cur) => ({ ...cur, entity: "" }));
    }).catch(() => {});
    return () => { stop = true; };
  }, [g, v.noGst]);  // eslint-disable-line react-hooks/exhaustive-deps

  if (!form) return <Frame><section className="panel d1-sheet"><p className="muted">Loading…</p></section></Frame>;
  if (!form.valid || !form.open) {
    return (
      <Frame><section className="panel d1-sheet">
        <span className="kicker">Business AI · event</span>
        <h1>{form.valid ? <>This link is <em>closed</em> right now.</> : <>This link isn't <em>valid</em>.</>}</h1>
        <p className="sub">{form.valid ? "It opens when the speaker starts the session. Keep this page and refresh it then." : "Please scan the code shown in the hall again."}</p>
      </section></Frame>
    );
  }
  const set = (patch) => { setV({ ...v, ...patch }); setErr(""); };
  const needState = v.noGst || (gst?.valid && !gst.state);
  const submit = async () => {
    setErr("");
    if (!v.noSite && v.website.trim().length < 4) return setErr("Type your website, or tick “I don't have a website”.");
    if (v.noSite && (v.business_name.trim().length < 2 || v.about.trim().length < 10)) return setErr("Type your business name and what you sell.");
    if (!v.noGst && !gst?.valid) return setErr(gst?.error || "Type your 15-character GST number, or tick “Not registered for GST”.");
    if (!v.entity) return setErr("Pick your type of business.");
    if (needState && !v.state) return setErr("Pick your state.");
    if (v.staff === "") return setErr("How many people work with you? Type 0 if it's just you.");
    if (!v.consent) return setErr("Tick the box to say this is your business.");
    setBusy(true);
    try {
      const r = await api("/event/runs", { method: "POST", body: {
        event: form.event, website: v.noSite ? null : v.website.trim(), business_name: v.noSite ? v.business_name.trim() : null,
        about: v.noSite ? v.about.trim() : null, city: v.noSite ? v.city.trim() : null, gstin: v.noGst ? null : g,
        state: needState ? v.state : null, gst_scheme: v.gst_scheme, staff: Number(v.staff) || 0, entity: v.entity, consent: v.consent,
        competitors: form.ai ? cleanCompetitors(v.competitors) : [] } });
      nav(`/event/r/${r.token}`);
    } catch (e) { setErr(e.message); } finally { setBusy(false); }
  };

  return (
    <Frame>
      <section className="panel d1-sheet">
        <span className="kicker">{form.name || "Business AI"} · your pack</span>
        <h1>Your business, built in about a <em>minute</em>.</h1>
        <p className="sub">Type your website and GST number. The hub builds your compliance calendar, checks your website, picks up your brand colours and starts your Knowledge Hub{form.ai ? " — then writes your brand message, a new website and ways to get more leads" : ""}. No login yet.</p>

        <div className="stack">
          <Field2 label="Your website">
            <input className="input" inputMode="url" autoComplete="url" placeholder="www.yourbusiness.in" value={v.website} disabled={v.noSite}
              onChange={(e) => set({ website: e.target.value })} />
          </Field2>
          <label className="d1-consent"><input type="checkbox" checked={v.noSite} onChange={(e) => set({ noSite: e.target.checked, website: "" })} /><span>I don't have a website</span></label>
          {v.noSite && (
            <div className="stack ev-indent">
              <div className="grid2">
                <Field2 label="Business name"><input className="input" maxLength={80} value={v.business_name} onChange={(e) => set({ business_name: e.target.value })} placeholder="Riya Tiffins" /></Field2>
                <Field2 label="City"><input className="input" maxLength={80} value={v.city} onChange={(e) => set({ city: e.target.value })} placeholder="Pune" /></Field2>
              </div>
              <Field2 label="What you sell, in a line or two">
                <textarea className="textarea" rows={2} maxLength={1000} value={v.about} onChange={(e) => set({ about: e.target.value })}
                  placeholder="Home-cooked lunch tiffins delivered to offices in Baner and Aundh" />
              </Field2>
            </div>
          )}
          {form.ai && (
            <div className="field">
              <span className="label">Competitor websites <span className="muted">(optional, up to 3)</span></span>
              <CompetitorInputs value={v.competitors} onChange={(competitors) => set({ competitors })} />
              <span className="hint">Your new website is written to stand out from them. Nothing is copied, and they're never named.</span>
            </div>
          )}
        </div>

        <div className="stack">
          <Field2 label="GST number" hint={gst?.valid ? `${gst.state || "State not in the number"} · ${gst.pan_kind}` : gst && g.length === 15 ? gst.error : "15 characters, on your GST certificate or invoices."}>
            <input className={`input mono ev-gst${gst?.valid ? " ok" : ""}`} autoCapitalize="characters" spellCheck={false} maxLength={18} placeholder="27ABCDE1234F1Z5"
              value={v.gstin} disabled={v.noGst} onChange={(e) => set({ gstin: e.target.value.toUpperCase() })} />
          </Field2>
          <label className="d1-consent"><input type="checkbox" checked={v.noGst} onChange={(e) => { set({ noGst: e.target.checked, gstin: "", entity: "" }); setGst(null); }} /><span>Not registered for GST</span></label>
        </div>

        {(gst?.valid || v.noGst) && (
          <div className="ev-taps">
            {gst?.valid && gst.ask_entity && (
              <div className="fr-q"><span className="label">Your GST number is for a {gst.pan_kind.toLowerCase()}. Which one?</span>
                <Seg label="Type of business" value={v.entity} options={gst.entities} onChange={(x) => set({ entity: x })} /></div>
            )}
            {v.noGst && (
              <div className="fr-q"><span className="label">Type of business</span>
                <select className="select" value={v.entity} onChange={(e) => set({ entity: e.target.value })}>
                  <option value="">Pick one…</option>{form.entities.map((x) => <option key={x.value} value={x.value}>{x.label}</option>)}
                </select></div>
            )}
            {needState && (
              <div className="fr-q"><span className="label">State</span>
                <select className="select" value={v.state} onChange={(e) => set({ state: e.target.value })}>
                  <option value="">Pick your state…</option>{form.states.map((s) => <option key={s} value={s}>{s}</option>)}
                </select></div>
            )}
            {gst?.valid && (
              <div className="fr-q"><span className="label">You file GST returns</span>
                <Seg label="GST returns" value={v.gst_scheme} options={[{ value: "monthly", label: "Every month" }, { value: "qrmp", label: "Every quarter" }]}
                  onChange={(x) => set({ gst_scheme: x })} /></div>
            )}
            <div className="fr-q"><span className="label">People who work with you, not counting you</span>
              <input className="input d1-num ev-staff" type="number" inputMode="numeric" min={0} max={100000} placeholder="0" aria-label="People who work with you"
                value={v.staff} onChange={(e) => set({ staff: e.target.value })} /></div>
          </div>
        )}

        <label className="d1-consent"><input type="checkbox" checked={v.consent} onChange={(e) => set({ consent: e.target.checked })} /><span>{form.consent}</span></label>
        <FormError msg={err} />
        <button className="btn primary ev-go" type="button" disabled={busy} onClick={submit}>{busy ? "Starting…" : "Build my pack"}</button>
      </section>
    </Frame>
  );
}

const Field2 = ({ label, hint, children }) => <label className="field"><span className="label">{label}</span>{children}{hint && <span className="hint">{hint}</span>}</label>;

const CHIP = { waiting: ["Waiting", "off"], working: ["Working…", "warn"], done: ["Ready", "on"], failed: ["Couldn't", "warn"], skipped: ["—", "off"], after_login: ["After you save", "off"] };

function Card({ title, area, step, children }) {
  const [label, kind] = CHIP[step?.status || "waiting"] || CHIP.waiting;
  return (
    <article className={`panel ev-card ${step?.status || "waiting"}`}>
      <div className="row between"><span className="label">{area}</span><span className={`pill ${kind}`}>{label}</span></div>
      <h2>{title}</h2>
      {step?.status === "done" || step?.status === "failed" ? children : step?.note ? <p className="small muted">{step.note}</p>
        : <div className="d1-wait" aria-hidden="true"><i /></div>}
      {step?.status === "done" && step?.note && <p className="small muted">{step.note}</p>}
    </article>
  );
}

const fmt = (iso) => new Date(`${iso}T00:00:00`).toLocaleDateString("en-IN", { day: "numeric", month: "short" });

function Pack() {
  const { token } = useParams();
  const [run, setRun] = useState(null);
  const [gone, setGone] = useState(false);
  const [pickBusy, setPickBusy] = useState(false);
  const [save, setSave] = useState(false);
  const [kick, setKick] = useState(0);
  const [saved, setSaved] = useState(null);     // what the save put in (kept here: later polls don't carry it)
  const saveRef = useRef(null);
  useEffect(() => {
    let stop = false;
    let timer;
    let misses = 0;
    const tick = async () => {
      try {
        const r = await api(`/event/runs/${token}`);
        if (stop) return;
        misses = 0;
        setRun(r);
        if (!["done", "failed"].includes(r.status) || r.pending) timer = setTimeout(tick, 2000);
      } catch (e) {
        if (stop) return;
        if (e.status === 404) { setGone(true); return; }
        misses += 1;              // a Wi-Fi blip in a full hall: keep trying, a little slower each time
        timer = setTimeout(tick, Math.min(2000 * (misses + 1), 10000));
      }
    };
    tick();
    return () => { stop = true; clearTimeout(timer); };
  }, [token, kick]);
  useEffect(() => { if (save) saveRef.current?.scrollIntoView({ behavior: "smooth", block: "start" }); }, [save]);
  if (gone) return <Frame><section className="panel d1-sheet"><h1>We couldn't find this <em>pack</em>.</h1><p className="sub">Open the event link again.</p></section></Frame>;
  if (!run) return <Frame><section className="panel d1-sheet"><p className="muted">Loading…</p></section></Frame>;
  if (run.status === "failed") return <Frame><section className="panel d1-sheet"><h1>This pack couldn't be <em>built</em>.</h1><p className="sub">{run.error}</p></section></Frame>;

  const s = run.steps || {};
  const keys = Object.keys(s).filter((k) => s[k].status !== "skipped");
  const ready = keys.filter((k) => ["done", "after_login", "failed"].includes(s[k].status)).length;
  const b = run.business || {};
  const colors = run.design?.colors;
  const pick = run.brand?.pick || 0;
  const doPick = async (i) => {
    setPickBusy(true);
    try { await api(`/event/runs/${token}/brand`, { method: "POST", body: { pick: i } }); setRun({ ...run, brand: { ...run.brand, pick: i } }); }
    catch { /* saved packs change in the hub */ } finally { setPickBusy(false); }
  };
  const img = (key) => `/api/event/runs/${token}/design/${key}.png?v=${pick}-${run.brand?.options?.length || 0}-${b.name?.length || 0}`;
  const comp = run.compliance;
  return (
    <Frame wide>
      <section className="ev-head">
        <span className="kicker">Your pack{b.name ? ` · ${b.name}` : ""}</span>
        <h1>{run.status === "done" ? <>Here's what the hub <em>built</em> for you.</> : <>Building your pack<em>…</em></>}</h1>
        <div className="ev-progress" role="status" aria-live="polite">
          <div className="ev-bar"><i style={{ width: `${Math.round((ready / Math.max(keys.length, 1)) * 100)}%` }} /></div>
          <span className="small muted">{ready} of {keys.length} ready{run.status !== "done" && run.eta?.all ? ` · about ${run.eta.all} seconds` : ""}</span>
        </div>
      </section>

      {run.needs_about && <AboutForm token={token} siteError={run.site?.error} onDone={(r) => { setRun(r); setKick((k) => k + 1); }} ai={run.ai} />}

      <div className="ev-grid">
        <Card area="Operations" title="Your compliance calendar" step={s.compliance}>
          {comp && <>
            <p className="ev-big"><b>{comp.count}</b> filings in the next 12 months{comp.this_month ? <> · <b>{comp.this_month}</b> this month</> : null}</p>
            <ul className="ev-list">{comp.next.slice(0, 5).map((i) => <li key={i.title + i.due_date}><span>{i.title}{i.period ? <span className="muted"> · {i.period}</span> : null}</span><b>{fmt(i.due_date)}</b></li>)}</ul>
            {comp.needs_date?.length > 0 && <p className="small muted">Also: {comp.needs_date.join(", ")} — add the expiry date from your certificate.</p>}
            <p className="small muted">{comp.review} Reminders reach you 30, 7 and 1 days before.</p>
          </>}
        </Card>

        <Card area="Data" title="Your website check" step={s.check}>
          {run.check ? <>
            <p className="ev-big"><b>{run.check.passed}</b> of {run.check.total} checks passed</p>
            <ul className="fr-checks">{run.check.items.map((c) => <li key={c.key}><span className={c.ok ? "ok" : "no"} aria-hidden="true">{c.ok ? "✓" : "✗"}</span><span>{c.label}<span className="fr-sr">{c.ok ? " — passed" : " — not yet"}</span></span></li>)}</ul>
            {run.check.fixes.length > 0 && <><span className="label">Fix first</span><ol className="d1-list small">{run.check.fixes.map((f) => <li key={f}>{f}</li>)}</ol></>}
          </> : <p className="small muted">{run.site?.error || s.check?.note}</p>}
        </Card>

        <Card area="Brand" title="Your brand starter" step={s.brand}>
          {colors && <>
            <div className="row ev-swatches">{["primary", "secondary", "accent"].map((k) => <span key={k} className="ev-swatch" style={{ background: colors[k] }} title={`${k} ${colors[k]}`}><span className="fr-sr">{k} {colors[k]}</span></span>)}
              <span className="small muted">{run.design.source === "logo" ? "From your logo" : run.design.source === "website" ? "From your website" : "A starting colour — change it in your Brand kit"}</span></div>
            <div className="ev-designs">
              <img src={img("status_poster")} alt="Your WhatsApp Status poster" width="270" height="480" loading="lazy" />
              <img src={img("social_square")} alt="Your social post" width="270" height="270" loading="lazy" />
            </div>
          </>}
        </Card>

        <Card area="Brand" title="Your brand message" step={s.brand_message}>
          {run.brand?.options?.length > 0 && <>
            <p className="small muted">Pick the one that sounds like you. Your poster changes with it.</p>
            <div className="stack ev-options">{run.brand.options.map((o, i) => (
              <button key={i} type="button" className={`ev-option${pick === i ? " on" : ""}`} aria-pressed={pick === i} disabled={pickBusy || run.saved} onClick={() => doPick(i)}>
                <b>{o.message}</b><span className="small muted">{o.pitch}</span>
              </button>))}</div>
          </>}
        </Card>

        <Card area="Foundation" title="Your Knowledge Hub, started" step={s.knowledge}>
          {b.name && <>
            {run.completeness && <p className="ev-big"><b>{run.completeness.percent}%</b> filled when you save</p>}
            <dl className="ev-kv">
              <dt>Business</dt><dd>{b.name}</dd>
              {b.industry && <><dt>What you do</dt><dd>{b.industry}</dd></>}
              {b.city && <><dt>City</dt><dd>{b.city}</dd></>}
              {b.offers?.length > 0 && <><dt>Offers</dt><dd>{b.offers.join(" · ")}</dd></>}
              {run.identity && <><dt>Business identity</dt><dd>{run.identity.state} · {run.identity.gst_registered ? `GST ${run.identity.gst_scheme === "qrmp" ? "quarterly" : "monthly"}` : "No GST"} · {run.identity.staff} staff</dd></>}
            </dl>
            <p className="small muted">Every tool in the hub writes from this. Check it once you save.</p>
          </>}
        </Card>

        <Card area="Lead" title="Your new website" step={s.website}>
          {run.website && (
            <div className="ev-site" style={{ "--brand": colors?.primary || "#1F4E79" }}>
              <div className="ev-site-hero"><span className="small">{b.name}</span><b>{run.website.headline}</b><span>{run.website.subheadline}</span><i>{run.website.cta}</i></div>
              {run.website.offers?.length > 0 && <ul className="ev-list">{run.website.offers.slice(0, 4).map((o) => <li key={o.name}><span>{o.name}</span>{o.price ? <b>{o.price}</b> : null}</li>)}</ul>}
              {run.competitors?.some((x) => x.read) && <p className="small">Written to stand out from {run.competitors.filter((x) => x.read).map((x) => x.host).join(", ")}.</p>}
              <p className="small muted">In your brand's colours, with your prices, an inquiry form and a WhatsApp button. It goes live with one tap after you save, at its own address to share.</p>
            </div>
          )}
        </Card>

        <Card area="Lead" title="Ways to get more leads" step={s.lead_ideas}>
          {run.ideas?.length > 0 && <ol className="ev-ideas">{run.ideas.map((x) => <li key={x.channel}><b>{x.title}</b><span className="small muted">{/^this week/i.test(x.first_step || "") ? x.first_step : `This week: ${x.first_step}`}</span></li>)}</ol>}
        </Card>
      </div>

      <section className="panel d1-sheet ev-save" ref={saveRef}>
        {run.saved ? <Saved applied={saved?.applied} pending={run.pending} /> : !save ? (
          <div className="row between">
            <div className="stack" style={{ gap: 4 }}><b className="ev-save-t">Keep all of this in your own hub.</b><span className="small muted">Free. Your WhatsApp number and email, and a 6-digit code on WhatsApp.</span></div>
            <button className="btn primary" onClick={() => setSave(true)}>Save it to my hub</button>
          </div>
        ) : <SaveForm token={token} building={!["done", "failed"].includes(run.status)} onSaved={(r) => { setSaved(r); setRun({ ...run, saved: true, pending: r.pending }); setKick((k) => k + 1); }} />}
      </section>
      {!run.saved && !save && <div className="ev-sticky"><button className="btn primary" onClick={() => setSave(true)}>Save it to my hub</button></div>}
    </Frame>
  );
}

function AboutForm({ token, siteError, onDone, ai }) {
  const [f, setF] = useState({ business_name: "", about: "", city: "" });
  const [err, setErr] = useState("");
  const [busy, setBusy] = useState(false);
  const go = async () => {
    if (f.business_name.trim().length < 2 || f.about.trim().length < 10) { setErr("Type your business name and what you sell."); return; }
    setBusy(true); setErr("");
    try { onDone(await api(`/event/runs/${token}/about`, { method: "POST", body: { ...f, business_name: f.business_name.trim(), about: f.about.trim(), city: f.city.trim() } })); }
    catch (e) { setErr(e.message); } finally { setBusy(false); }
  };
  return (
    <section className="panel active ev-about">
      <span className="label">We couldn't open your website</span>
      <p className="small muted">{siteError || "It didn't answer in time."} Tell us in a line what you sell{ai ? " and the hub writes your brand message, website and lead ideas from that" : ""}.</p>
      <div className="grid2">
        <Field2 label="Business name"><input className="input" maxLength={80} value={f.business_name} onChange={(e) => setF({ ...f, business_name: e.target.value })} /></Field2>
        <Field2 label="City"><input className="input" maxLength={80} value={f.city} onChange={(e) => setF({ ...f, city: e.target.value })} /></Field2>
      </div>
      <Field2 label="What you sell, in a line or two"><textarea className="textarea" rows={2} maxLength={1000} value={f.about} onChange={(e) => setF({ ...f, about: e.target.value })} /></Field2>
      <FormError msg={err} />
      <button className="btn primary" disabled={busy} onClick={go}>{busy ? "Sending…" : "Continue"}</button>
    </section>
  );
}

function SaveForm({ token, onSaved, building }) {
  const [f, setF] = useState({ name: "", email: "", phone: "" });
  const [sent, setSent] = useState(null);
  const [code, setCode] = useState("");
  const [err, setErr] = useState("");
  const [busy, setBusy] = useState(false);
  const [done, setDone] = useState(null);
  const digits = f.phone.replace(/\D/g, "").replace(/^(91|0)(?=\d{10}$)/, "");
  const okForm = f.name.trim().length > 0 && /^[^\s@]+@[^\s@]+\.[^\s@]{2,}$/.test(f.email.trim()) && /^[6-9]\d{9}$/.test(digits);
  const send = async () => {
    if (!okForm) { setErr("Add your name, your email and a 10-digit WhatsApp number."); return; }
    setBusy(true); setErr("");
    try { setSent(await api(`/event/runs/${token}/code`, { method: "POST", body: { phone: digits } })); } catch (e) { setErr(e.message); } finally { setBusy(false); }
  };
  const verify = async (e) => {
    e.preventDefault(); setBusy(true); setErr("");
    try {
      const r = await api(`/event/runs/${token}/verify`, { method: "POST", body: { phone: digits, code, email: f.email.trim(), name: f.name.trim() } });
      setDone(r);
      onSaved(r);
    } catch (e2) { setErr(e2.message); } finally { setBusy(false); }
  };
  if (done) return <Saved applied={done.applied} pending={done.pending} />;
  return (
    <div className="stack">
      <h2>Save it with your WhatsApp <em>code</em>.</h2>
      {building && <p className="small muted">Your pack is still being written. Save now if you like — the rest goes into your hub the moment it's ready.</p>}
      {!sent ? (
        <>
          <div className="grid2">
            <Field2 label="Your name"><input className="input" autoComplete="name" maxLength={80} value={f.name} onChange={(e) => setF({ ...f, name: e.target.value })} placeholder="Asha" /></Field2>
            <Field2 label="Email"><input className="input" type="email" inputMode="email" autoComplete="email" maxLength={120} value={f.email} onChange={(e) => setF({ ...f, email: e.target.value })} placeholder="you@yourbusiness.in" /></Field2>
          </div>
          <Field2 label="WhatsApp number" hint="The code comes here. Customers' replies and your reminders come here too.">
            <input className="input" inputMode="tel" autoComplete="tel" maxLength={18} value={f.phone} onChange={(e) => setF({ ...f, phone: e.target.value })} placeholder="98200 00000" />
          </Field2>
          <button className="btn primary" disabled={busy} onClick={send}>{busy ? "Sending…" : "Send my code"}</button>
        </>
      ) : (
        <form className="stack" onSubmit={verify}>
          <p className="small muted">Sent on {sent.via.includes("sms") ? "SMS" : "WhatsApp"} to {f.phone}. It works for 10 minutes.</p>
          {sent.dev_code && <p className="prompt mono">Development mode — code: {sent.dev_code}</p>}
          <input className="input mono" inputMode="numeric" autoComplete="one-time-code" maxLength={6} placeholder="6-digit code" aria-label="6-digit code"
            value={code} onChange={(e) => setCode(e.target.value.replace(/\D/g, ""))} autoFocus style={{ letterSpacing: ".3em", fontSize: 20 }} />
          <div className="row">
            <button className="btn primary" disabled={busy || code.length !== 6}>{busy ? "Saving…" : "Save to my hub"}</button>
            <button type="button" className="btn ghost" disabled={busy} onClick={send}>Send a new code</button>
          </div>
        </form>
      )}
      <FormError msg={err} />
    </div>
  );
}

const APPLIED = { knowledge: "Knowledge Hub", compliance: "Compliance calendar", brand: "Brand colours and logo", brand_message: "Brand message", website: "Website draft", lead_ideas: "Ways to get more leads" };

function Saved({ applied, pending }) {
  const a = applied || {};
  const kept = Object.entries(a).filter(([k, x]) => x === "kept" && APPLIED[k]).map(([k]) => APPLIED[k]);
  const trial = a.trial ? new Date(a.trial).toLocaleDateString("en-IN", { day: "numeric", month: "short" }) : null;
  return (
    <div className="stack">
      <h2>Saved. It's in your <em>hub</em>.</h2>
      {pending && <p className="sub">The last pieces are still being written. They go into your hub by themselves in a minute or two.</p>}
      {a.all === "team" ? <p className="sub">This number is a team member's login, so the pack wasn't added to the business owner's hub.</p> : <>
        {trial && <p className="sub">Membership features are open for you until {trial}, so you can try everything in the sessions.</p>}
        {kept.length > 0 && <p className="small muted">You already had these, so we kept yours: {kept.join(", ")}.</p>}
      </>}
      <button className="btn primary" onClick={() => { window.location.href = "/"; }}>Open my hub</button>
    </div>
  );
}

export default function Event() {
  return (
    <Routes>
      <Route path="/event" element={<Form />} />
      <Route path="/event/r/:token" element={<Pack />} />
      <Route path="*" element={<Form />} />
    </Routes>
  );
}
