import React, { useEffect, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { api, attribution, rememberAttribution } from "../api.js";
import { Logo } from "../ui.jsx";

const PILLARS = [
  ["01", "Structure", "Your brand message", "AI drafts three one-line messages and a 30-second pitch from what you tell it. You pick and edit."],
  ["02", "Systems", "A website that brings leads", "Your page goes live with an inquiry form. Every inquiry reaches your WhatsApp the moment it comes in."],
  ["03", "Scale", "Posts and replies, written", "A week of social posts, replies to customers and a Business GPT that knows your business."],
];

export default function Landing() {
  const [params] = useSearchParams();
  const [invite, setInvite] = useState(null);
  const [cohort, setCohort] = useState(null);
  const [showcase, setShowcase] = useState([]);
  const [plans, setPlans] = useState([]);

  useEffect(() => {
    rememberAttribution(params);
    const a = attribution();
    if (a.ref) api(`/public/invite/${encodeURIComponent(a.ref)}?src=${encodeURIComponent(a.src || "invite")}`).then((d) => d.valid && setInvite(d)).catch(() => {});
    if (a.c) api(`/public/cohort/${encodeURIComponent(a.c)}`).then((d) => d.valid && setCohort(d)).catch(() => {});
    api("/public/showcase").then(setShowcase).catch(() => {});
    api("/public/plans").then(setPlans).catch(() => {});
  }, []);

  return (
    <div className="public">
      <header>
        <div className="mark"><Logo h={44} /><span>Akshat Dani · Chirag J.</span></div>
        <Link className="btn sm ghost" to="/login">Log in</Link>
      </header>

      {invite && (
        <div className="invite-banner">
          <span><b>{invite.business || "A business owner"}</b> invited you. Your first {invite.trial_days} days include Membership features, free.</span>
          <Link className="btn sm primary" to="/login">Claim it</Link>
        </div>
      )}
      {cohort && !invite && (
        <div className="invite-banner">
          <span>Welcome from <b>{cohort.name || "the Business AI workshop"}</b>. Your hub is free — set it up during the session.</span>
          <Link className="btn sm primary" to="/login">Start now</Link>
        </div>
      )}

      <section className="title-sheet hero" style={{ marginTop: 18 }}>
        <div className="hero-copy">
          <span className="kicker">Free for business owners</span>
          <h1>Your business website, <em>live</em> in ten minutes.</h1>
          <p className="sub" style={{ fontSize: 17 }}>Tell the AI about your business. It writes your brand message, builds a website with an inquiry form, and sends every new lead to your WhatsApp. Free — no card, no setup fee.</p>
          <div className="row">
            <Link className="btn primary" to="/login">Start free with your mobile number</Link>
            <a className="btn ghost" href="#showcase">See what owners built</a>
          </div>
          <ul className="hero-points">
            <li>Website at <b>yourname.employz.ai</b></li>
            <li>Every inquiry on your WhatsApp</li>
            <li>Business AI Score out of 100</li>
          </ul>
        </div>
        <HubPreview />
      </section>
      <div className="pillars">
        {PILLARS.map(([n, pillar, title, text]) => (
          <div key={n}>
            <span className="ghost-num">{n}</span>
            <span className="kicker">{pillar}</span>
            <h3>{title}</h3>
            <p className="muted">{text}</p>
          </div>
        ))}
      </div>

      <section style={{ padding: "64px 0 0", display: "grid", gap: 22 }} id="showcase">
        <span className="kicker">Showcase</span>
        <h2>Websites owners put <em>live</em> here.</h2>
        {showcase.length ? (
          <div className="showcase">
            {showcase.map((s) => (
              <a key={s.slug} href={`/s/${s.slug}`} target="_blank" rel="noopener">
                <span className="label">{s.city || "India"}</span>
                <b style={{ fontFamily: "var(--head)", fontWeight: 700, fontSize: 18 }}>{s.name}</b>
                <span className="small muted">{s.headline}</span>
              </a>
            ))}
          </div>
        ) : <p className="muted">The first websites from this week's workshop will appear here.</p>}
      </section>

      {plans.length > 0 && (
        <section style={{ padding: "72px 0 0", display: "grid", gap: 22 }} id="plans">
          <div className="row between" style={{ alignItems: "flex-end" }}>
            <div style={{ display: "grid", gap: 14 }}>
              <span className="kicker">Six plans · one hub</span>
              <h2>Start free. The same hub <em>grows</em> with you.</h2>
            </div>
            <p className="sub small">Each plan includes everything before it. Nothing to move or set up again.</p>
          </div>
          <div className="ladder">
            {plans.map((p, i) => (
              <div key={p.tier} className={`ladder-step${p.tier === "free" ? " free" : ""}`}>
                <span className="ladder-n">{String(i + 1).padStart(2, "0")}</span>
                <span className="label">{p.name}</span>
                <b className={p.price_minor ? "ladder-price money" : "ladder-price"}>{price(p)}<small>{term(p)}</small></b>
                <span className="small muted">{p.promise || p.pitch}</span>
                <span className="ladder-meta">{p.runs.toLocaleString("en-IN")} AI runs a month · {p.features} features</span>
              </div>
            ))}
          </div>
        </section>
      )}

      <section style={{ padding: "64px 0 0", display: "grid", gap: 16 }}>
        <span className="kicker">Free today</span>
        <h2>Start free. Unlock more when it <em>works</em> for you.</h2>
        <p className="sub">Free includes your brand message, a live website with inquiries to WhatsApp, and 10 AI runs a month. Membership removes the badge, replies to every new lead instantly on WhatsApp and gives you 150 runs a month, with two coaching calls.</p>
        <div><Link className="btn" to="/login">Create my free hub</Link></div>
      </section>
      <footer className="public-foot">
        <span>OwnerDesk, with Akshat Dani and Chirag J.</span>
        <Link to="/login">Log in</Link>
      </footer>
    </div>
  );
}

function price(p) {
  return "₹" + ((p.price_minor || 0) / 100).toLocaleString("en-IN");
}

function term(p) {
  if (!p.price_minor) return "free forever";
  if (p.billing === "subscription") return "per month";
  const d = p.access_days;
  if (!d) return "once";
  return `once · ${d % 365 === 0 ? `${d / 365} year${d > 365 ? "s" : ""}` : d % 30 === 0 ? `${d / 30} months` : `${d} days`}`;
}

/** A drawing of the hub on the hero, so first-time visitors see what they get before they sign up. Illustrative, not live data. */
function HubPreview() {
  return (
    <div className="hub-preview" aria-hidden="true">
      <div className="hp-bar"><span>Your hub · today</span><i /><i /><i /></div>
      <div className="hp-alert">
        <span className="label" style={{ color: "var(--aqua)" }}>WhatsApp · new inquiry</span>
        <b>Kavita R. wants an L-shape kitchen</b>
        <span className="small muted">2BHK in Baner · asked for a site visit on Saturday</span>
        <span className="hp-reply">Reply drafted — tap to send</span>
      </div>
      <div className="hp-stats">
        <div><span className="label">Website</span><b>Live</b><span className="small muted">yourname.employz.ai</span></div>
        <div><span className="label">Leads this week</span><b>6</b><span className="small muted">2 waiting for you</span></div>
      </div>
      <div className="hp-score">
        <b>82</b><span className="muted">/100</span>
        <div><span className="label">Business AI Score</span><div className="hp-meter"><i style={{ width: "82%" }} /></div></div>
      </div>
      <div className="hp-posts">
        <span className="label">This week's posts</span>
        <div>{["Mon", "Wed", "Fri"].map((d, i) => <span key={d} className={i < 2 ? "done" : ""}>{d}</span>)}</div>
      </div>
    </div>
  );
}
