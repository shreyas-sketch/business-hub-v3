import React, { useEffect, useState } from "react";
import { api } from "../api.js";
import { Busy, Field, FormError, useHub } from "../ui.jsx";
import "../pages/lead23.css";

/* v2.3: the shared inputs, asked once and reused by the website, the lead magnet and the idea suggestions:
   their website, one competitor's website, and — when there's no website — the 4 questions, pre-filled from the
   Knowledge Hub so nobody types the same thing twice. */

export const Q_KEYS = ["about", "offers", "audience", "pains"];
const FROM = { brain: "From your Knowledge Hub — check it", you: "", "": "" };

/** The 4 questions, pre-filled. `value` and `onChange` work on {about, offers, audience, pains}. */
export function FourQuestions({ data, value, onChange }) {
  const set = (k) => (e) => onChange({ ...value, [k]: e.target.value });
  return (
    <div className="stack">
      {Q_KEYS.map((k) => (
        <Field key={k} label={data.questions[k]} hint={[FROM[data.inputs.sources?.[k] || ""], data.hints?.[k]].filter(Boolean).join(" · ")}>
          {k === "audience"
            ? <input className="input" maxLength={300} value={value[k] || ""} onChange={set(k)} />
            : <textarea className="textarea" style={{ minHeight: k === "about" ? 56 : 72 }} maxLength={{ about: 400, offers: 600 }[k] || 500} value={value[k] || ""} onChange={set(k)} />}
        </Field>
      ))}
    </div>
  );
}

export const answersOf = (inputs) => Object.fromEntries(Q_KEYS.map((k) => [k, inputs?.[k] || ""]));

/** What the hub understood about the competitor (never their own words). */
export function CompetitorNote({ comp }) {
  if (!comp) return null;
  if (comp.status !== "ok" || !comp.summary) {
    return comp.error ? <p className="small muted">We couldn't read their website last time: {comp.error}</p> : null;
  }
  const s = comp.summary;
  return (
    <div className="panel flat si-comp">
      <span className="label">What we learned about {comp.host}</span>
      {s.positioning && <p className="small">{s.positioning}</p>}
      {s.buy_path_labels?.length > 0 && <p className="small muted">How their customers buy: {s.buy_path_labels.join(" · ")}</p>}
      {s.gaps?.length > 0 && (
        <>
          <span className="small muted">Where you can stand out</span>
          <ul className="small si-gaps">{s.gaps.map((g, i) => <li key={i}>{g}</li>)}</ul>
        </>
      )}
      <span className="small muted">Used only to make your website and ideas stand out. Their words are never copied.</span>
    </div>
  );
}

/** The whole set: your website, a competitor's website, and the 4 questions. `onSaved(view)` after a save. */
export default function SharedInputs({ title = "Your website, a competitor, and 4 quick questions", onSaved, showQuestions = "auto" }) {
  const { toast, refresh } = useHub();
  const [d, setD] = useState(null);
  const [site, setSite] = useState("");
  const [rival, setRival] = useState("");
  const [noSite, setNoSite] = useState(false);
  const [ans, setAns] = useState({});
  const [err, setErr] = useState("");
  const load = (v) => {
    setD(v);
    setSite(v.inputs.website_url || "");
    setRival(v.inputs.competitor_url || "");
    setNoSite(v.inputs.has_website === false);
    setAns(answersOf(v.inputs));
  };
  useEffect(() => { api("/inputs").then(load).catch((e) => setErr(e.message)); }, []);
  if (!d) return err ? <FormError msg={err} /> : null;
  const ask = showQuestions === "always" || (showQuestions === "auto" && (noSite || !site));
  const save = async () => {
    const body = { website_url: noSite ? "" : site.trim(), has_website: noSite ? false : site.trim() ? true : null, competitors: rival.trim() ? [rival.trim()] : [] };
    if (ask) Object.assign(body, ans);
    const v = await api("/inputs", { method: "PUT", body });
    load(v);
    toast("Saved");
    onSaved?.(v);
  };
  return (
    <div className="stack si">
      <span className="label">{title}</span>
      <p className="small muted">Asked once. Your website, lead magnet and idea suggestions all use these answers.</p>
      <Field label="Your website (optional)" hint={d.inputs.website_from === "website_read" ? "The website we read when you started. Change it if it's moved." : "If you have one, we read it and make a better version."}>
        <input className="input" inputMode="url" placeholder="www.yourbusiness.in" value={noSite ? "" : site} disabled={noSite} onChange={(e) => setSite(e.target.value)} />
      </Field>
      <label className="row small" style={{ cursor: "pointer" }}><input type="checkbox" checked={noSite} onChange={(e) => setNoSite(e.target.checked)} /> I don't have a website yet</label>
      <Field label="A competitor's website (optional, recommended)" hint="We read their public pages so yours can stand out. We never copy their words.">
        <input className="input" inputMode="url" placeholder="www.theirbusiness.in" value={rival} onChange={(e) => setRival(e.target.value)} />
      </Field>
      {d.competitor && d.competitor.matches && <CompetitorNote comp={d.competitor} />}
      {d.inputs.competitor_url && d.can_read_competitor && (!d.competitor || !d.competitor.matches) && (
        <div className="row">
          <Busy className="btn sm ghost ai" onError={setErr} run={async () => { load(await api("/inputs/competitor/read", { method: "POST" })); refresh(); toast("We read their website"); }}>
            Read their website now · 1 AI token
          </Busy>
          <span className="small muted">Or it's read the first time you build your website or lead magnet.</span>
        </div>
      )}
      {ask && (
        <>
          <p className="small muted">{noSite || !site ? "No website? Answer these 4 questions and we'll build one for you." : "Check these answers."}</p>
          <FourQuestions data={d} value={ans} onChange={setAns} />
        </>
      )}
      <FormError msg={err} />
      <div><Busy className="btn primary sm" onError={setErr} run={save}>Save</Busy></div>
    </div>
  );
}
