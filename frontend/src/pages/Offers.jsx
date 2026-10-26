import React, { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { ago, api } from "../api.js";
import { Busy, Field, FormError, Sheet, Switch, useHub, useLoad } from "../ui.jsx";
import { Loading, Pill } from "../v2.jsx";
import "./program.css";

const LEVELS = [
  ["Easy first step", "Small, quick and low-risk — so a new customer can say yes today."],
  ["Main offer", "What most customers buy. Where most of your money comes from."],
  ["Premium", "Everything, done for them, for the customers who want the best."],
];

function BuildForm({ first, onDone, onCancel }) {
  const { refresh } = useHub();
  const [notes, setNotes] = useState("");
  const [entry, setEntry] = useState("");
  const [err, setErr] = useState("");
  return (
    <div className="panel active">
      <span className="label">{first ? "Build your offer ladder" : "Build it again"}</span>
      <Field label="Anything the AI should know (optional)" hint="What you sell today, your prices, who buys the most, what customers ask for.">
        <textarea className="textarea" maxLength={1000} value={notes} onChange={(e) => setNotes(e.target.value)} />
      </Field>
      <Field label="Your easy first step (optional)" hint="For example: a free site visit, a ₹499 trial, a 15-minute call.">
        <input className="input" maxLength={120} value={entry} onChange={(e) => setEntry(e.target.value)} />
      </Field>
      <FormError msg={err} />
      <div className="row">
        <Busy className="btn primary ai" onError={setErr} run={async () => {
          if (!first && !window.confirm("Build the ladder again? It replaces the three levels you have now.")) return;
          const d = await api("/offers/generate", { method: "POST", body: { notes: notes.trim(), entry: entry.trim() } });
          refresh();
          onDone(d);
        }}>Build my offer ladder</Busy>
        {onCancel && <button className="btn ghost" onClick={onCancel}>Cancel</button>}
      </div>
      <span className="small muted">Uses 1 AI token. Written from your Business Brain — you can change every word after.</span>
    </div>
  );
}

function LadderEditor({ content, onSaved }) {
  const { toast } = useHub();
  const saved = JSON.stringify(content);
  const [c, setC] = useState(content);
  const [err, setErr] = useState("");
  useEffect(() => setC(content), [saved]);
  const setLevel = (i, patch) => setC({ ...c, levels: c.levels.map((l, j) => (j === i ? { ...l, ...patch } : l)) });
  const clean = () => ({ ...c, levels: c.levels.map((l) => ({ ...l, includes: (l.includes || []).map((x) => x.trim()).filter(Boolean) })) });
  const dirty = JSON.stringify(clean()) !== saved;
  const ready = c.levels.length === 3 && c.levels.every((l) => l.name.trim());
  return (
    <div className="stack" style={{ gap: 16 }}>
      <div className="panel">
        <Field label="Section heading on your website"><input className="input" maxLength={80} value={c.heading} onChange={(e) => setC({ ...c, heading: e.target.value })} /></Field>
        <Field label="One line under it"><input className="input" maxLength={240} value={c.intro} onChange={(e) => setC({ ...c, intro: e.target.value })} /></Field>
      </div>
      <div className="pg-ladder">
        {c.levels.map((l, i) => (
          <div key={i} className={`panel ${i === 1 ? "active" : ""}`}>
            <div className="row between" style={{ alignItems: "flex-start" }}>
              <div className="stack" style={{ gap: 4 }}>
                <span className="label">{l.level || LEVELS[i][0]}</span>
                <span className="small muted">{LEVELS[i][1]}</span>
              </div>
              <span className="step-no" aria-hidden="true">0{i + 1}</span>
            </div>
            <Field label="Name"><input className="input" maxLength={80} value={l.name} onChange={(e) => setLevel(i, { name: e.target.value })} /></Field>
            <Field label="Who it's for"><textarea className="textarea" style={{ minHeight: 60 }} maxLength={200} value={l.for_whom} onChange={(e) => setLevel(i, { for_whom: e.target.value })} /></Field>
            <Field label="What's included" hint="One per line, up to 5.">
              <textarea className="textarea" style={{ minHeight: 0 }} rows={Math.max(3, (l.includes || []).length + 1)} value={(l.includes || []).join("\n")} onChange={(e) => setLevel(i, { includes: e.target.value.split("\n").slice(0, 5) })} />
            </Field>
            <Field label="Price"><input className="input money" maxLength={60} value={l.price} placeholder="₹" onChange={(e) => setLevel(i, { price: e.target.value })} /></Field>
            <Field label="Why this level"><textarea className="textarea" style={{ minHeight: 60 }} maxLength={200} value={l.why} onChange={(e) => setLevel(i, { why: e.target.value })} /></Field>
          </div>
        ))}
      </div>
      <FormError msg={err} />
      <div className="row">
        <Busy className="btn primary" disabled={!ready || !dirty} onError={setErr} run={async () => {
          const d = await api("/offers", { method: "PUT", body: { content: clean() } });
          toast("Offer ladder saved");
          onSaved(d);
        }}>Save ladder</Busy>
        {dirty && <><span className="small muted">Changes not saved yet</span><button className="btn ghost" onClick={() => setC(content)}>Undo changes</button></>}
      </div>
      {!ready && <span className="hint">Each of the three levels needs a name.</span>}
    </div>
  );
}

export default function Offers() {
  const { me, toast } = useHub();
  const [loaded] = useLoad("/offers");
  const [data, setData] = useState(null);
  const [again, setAgain] = useState(false);
  const [showErr, setShowErr] = useState("");
  const [switching, setSwitching] = useState(false);
  useEffect(() => { if (loaded) setData(loaded); }, [loaded]);

  const head = { kicker: "Structure · Offer ladder", id: "PR-02", pillar: "Structure" };
  const sub = "Three ways to buy from you: an easy first step, your main offer and a premium one. Customers climb when the next step is clear.";
  if (!data) return <Sheet {...head} title={<>Give every customer a next <em>step</em>.</>} sub={sub}><Loading /></Sheet>;
  const lad = data.ladder;
  if (!lad) {
    return (
      <Sheet {...head} title={<>Give every customer a next <em>step</em>.</>} sub={sub}>
        <BuildForm first onDone={setData} />
      </Sheet>
    );
  }

  const siteLive = me.site?.status === "live";
  const toggle = async (show) => {
    setSwitching(true);
    setShowErr("");
    try {
      setData(await api("/offers/show", { method: "POST", body: { show } }));
      toast(show ? "The ladder now shows on your website" : "The ladder is hidden from your website");
    } catch (e) { if (!e.handled) setShowErr(e.message); }
    finally { setSwitching(false); }
  };

  return (
    <Sheet {...head} sub={sub}
      title={lad.show ? <>Your ladder is on your <em>website</em>.</> : <>Three steps, from first yes to <em>premium</em>.</>}>
      <div className={`panel ${lad.show ? "active" : ""}`}>
        <div className="row between">
          <span className="label">On your website</span>
          {lad.show ? <Pill kind="on">Showing</Pill> : <Pill>Hidden</Pill>}
        </div>
        <Switch checked={lad.show} disabled={switching} label="Show on my website" onChange={toggle} />
        <FormError msg={showErr} />
        {lad.show && data.site_url && siteLive && (
          <p className="small">It shows as its own section. <a href={data.site_url} target="_blank" rel="noopener">See it on my website</a></p>
        )}
        {!siteLive && <p className="small muted">Your website isn't live yet, so nobody can see the ladder. <Link to="/website">Go to my website</Link></p>}
        {lad.updated_at && <span className="small muted">Last changed {ago(lad.updated_at)}</span>}
      </div>

      <LadderEditor content={lad.content} onSaved={setData} />

      {again
        ? <BuildForm onDone={(d) => { setData(d); setAgain(false); }} onCancel={() => setAgain(false)} />
        : <div><button className="btn ghost sm" onClick={() => setAgain(true)}>Build it again with AI</button></div>}
    </Sheet>
  );
}
