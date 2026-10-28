import React, { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../api.js";
import { Busy, FormError, LoadError, Sheet, useHub, useLoad } from "../ui.jsx";
import { Loading, Pill } from "../v2.jsx";
import "./levers.css";

/* Ways to get more leads (v2.3, Sales engine): 5–7 ideas a month that fit this kind of business, each with one first step and
   the hub feature that does it. Its own page now; the Revenue Multiplier links here. Same API as before (/levers/ideas). */

const TYPE = { trading: "a trading business", clinic: "a clinic", food: "a food business", services: "a services business", manufacturing: "a manufacturer" };
const SOURCE = { website: "website", guide: "lead magnet", lead_magnet: "lead magnet", whatsapp: "WhatsApp", email: "email", card: "card / QR", qr: "card / QR" };

export function LeadIdeasPanel() {
  const { me, toast } = useHub();
  const [d, setD] = useState(null);
  const [loaded, , loadErr] = useLoad("/levers/ideas");
  const [err, setErr] = useState("");
  useEffect(() => { if (loaded) setD(loaded); }, [loaded]);
  if (!d) return <div className="panel">{loadErr ? <LoadError err={loadErr} /> : <Loading />}</div>;
  const cur = d.current;
  const sources = Object.entries(d.sources || {});
  const tierName = (feature) => me?.tiers?.[me?.feature_tiers?.[feature] || "lite"]?.name || "Membership";
  const tick = async (i, tried) => setD(await api(`/levers/ideas/${i.n}/tried`, { method: "POST", body: { tried } }));
  const suggest = async () => { setD(await api("/levers/ideas", { method: "POST" })); toast("This month's ideas are ready"); };
  return (
    <div className="panel active">
      <div className="row between">
        <div className="stack" style={{ gap: 4 }}>
          <span className="label">This month's ideas</span>
          <p className="small muted">Ideas that fit {TYPE[d.business_type] || "your business"}, each with one first step. Tick what you tried.</p>
        </div>
        {cur && <Pill kind="on">{cur.tried} of {cur.ideas.length} tried</Pill>}
      </div>
      <span className="small muted">
        Your leads in the last 90 days: {sources.length ? sources.map(([k, v]) => `${SOURCE[k] || k} ${v}`).join(" · ") : "none recorded yet"}.
      </span>
      {cur?.ideas?.length > 0 && (
        <div className="lv-ideas">
          {cur.ideas.map((i) => (
            <div key={i.n} className={`lv-idea ${i.tried ? "done" : ""}`}>
              <label className="lv-tick">
                <input type="checkbox" checked={!!i.tried} aria-label={`Tried: ${i.title}`} onChange={(e) => tick(i, e.target.checked).catch((x) => toast(x.message, "err"))} />
              </label>
              <div className="stack" style={{ gap: 4 }}>
                <b className="lv-title">{i.title}</b>
                {i.why && <span className="small muted">{i.why}</span>}
                {i.first_step && <span className="small">First step: {i.first_step}</span>}
                {i.feature && i.path && (
                  i.locked
                    ? <span className="small muted">{i.feature_label} opens with {tierName(i.feature)}. <Link to={i.path}>See it</Link></span>
                    : <span className="small"><Link to={i.path}>Open {i.feature_label} →</Link></span>
                )}
              </div>
            </div>
          ))}
        </div>
      )}
      <FormError msg={err} />
      <div className="row">
        <Busy className={`btn ai ${cur ? "ghost" : "primary"}`} onError={setErr} run={suggest}>{cur ? "Suggest again" : "Suggest ideas"}</Busy>
        <span className="small muted">Uses 1 AI token. Written from your Knowledge Hub{cur ? "; ideas you ticked are kept" : ""}.</span>
      </div>
      {d.past?.length > 0 && (
        <span className="small muted">Earlier: {d.past.map((p) => `${p.month} — ${p.tried} of ${p.ideas} tried`).join(" · ")}</span>
      )}
    </div>
  );
}

export default function LeadIdeas() {
  const { me } = useHub();
  return (
    <Sheet kicker="Sales · Ways to get more leads" id="SA-02" pillar="Sales"
      title={<>Ways to get more <em>leads</em>.</>}
      sub="A short list each month: the ways that fit your kind of business, each with one first step you can do this week.">
      <LeadIdeasPanel />
      {me?.features?.revenue_levers && (
        <p className="small muted">For 45 ideas across all three levers — more customers, a bigger sale, buying more often — open the <Link to="/levers">Revenue Multiplier</Link>.</p>
      )}
    </Sheet>
  );
}
