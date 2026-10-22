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

  useEffect(() => {
    rememberAttribution(params);
    const a = attribution();
    if (a.ref) api(`/public/invite/${encodeURIComponent(a.ref)}?src=${encodeURIComponent(a.src || "invite")}`).then((d) => d.valid && setInvite(d)).catch(() => {});
    if (a.c) api(`/public/cohort/${encodeURIComponent(a.c)}`).then((d) => d.valid && setCohort(d)).catch(() => {});
    api("/public/showcase").then(setShowcase).catch(() => {});
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

      <section className="title-sheet" style={{ marginTop: 18 }}>
        <span className="kicker">Free for business owners</span>
        <h1>Your business website, <em>live</em> in ten minutes.</h1>
        <p className="sub" style={{ fontSize: 17 }}>Tell the AI about your business. It writes your brand message, builds a website with an inquiry form, and sends every new lead to your WhatsApp. Free — no card, no setup fee.</p>
        <div className="row">
          <Link className="btn primary" to="/login">Start free with your mobile number</Link>
          <a className="btn ghost" href="#showcase">See what owners built</a>
        </div>
        <div className="titleblock">Project: a business that runs without you<br />Sheet 01 · Scale 1:1<br />Drawn. OwnerDesk</div>
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

      <section style={{ padding: "64px 0 0", display: "grid", gap: 16 }}>
        <span className="kicker">Free today</span>
        <h2>Start free. Unlock more when it <em>works</em> for you.</h2>
        <p className="sub">Free includes your brand message, a live website with inquiries to WhatsApp, and 10 AI runs a month. Membership removes the badge, replies to every new lead instantly on WhatsApp and gives you 150 runs a month, with two coaching calls.</p>
        <div><Link className="btn" to="/login">Create my free hub</Link></div>
      </section>
      <div className="rail-foot" style={{ marginTop: 72 }}>
        <span>Akshat Dani · Chirag J. · OwnerDesk</span>
        <span>AH-00</span>
      </div>
    </div>
  );
}
