import React, { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { ago, api, day, rs } from "../api.js";
import { Busy, Field, FormError, Sheet, Stat, useHub, useLoad } from "../ui.jsx";
import { Loading, Num, Pill } from "../v2.jsx";
import "./program.css";
import "./levers.css";

const n = (x) => Number(x) || 0;
const pct = (x) => new Intl.NumberFormat("en-IN", { maximumFractionDigits: 1 }).format(x);
const BLANK = { customers: "", avg_value: "", frequency: "", grow_customers: 10, grow_value: 10, grow_frequency: 10, funnel: {}, pledge: "" };
const KEYS = Object.keys(BLANK);
const HINTS = {
  leads: "e.g. Publish the lead magnet and post 3 times a week",
  conversion: "e.g. Reply within 10 minutes; follow up on day 1, 3 and 7",
  value: "e.g. Offer a premium package with installation",
  retention: "e.g. A service reminder every 6 months",
  referral: "e.g. Ask every happy customer for one name",
};

/** The same maths as the server, so the numbers move while you type. */
function calc(f) {
  const today = n(f.customers) * n(f.avg_value) * n(f.frequency);
  const factor = (1 + n(f.grow_customers) / 100) * (1 + n(f.grow_value) / 100) * (1 + n(f.grow_frequency) / 100);
  return { revenue: Math.round(today), new_revenue: Math.round(today * factor), growth_pct: Math.round((factor - 1) * 1000) / 10, extra: Math.round(today * factor - today) };
}

/** Lever by lever, in rupees: each one multiplies what the one before it made. Same maths as the server. */
function steps(f) {
  let running = n(f.customers) * n(f.avg_value) * n(f.frequency);
  return [["more customers", "grow_customers"], ["a bigger average sale", "grow_value"], ["buying more often", "grow_frequency"]].map(([lever, k]) => {
    const before = running;
    running = before * (1 + n(f[k]) / 100);
    return { lever, pct: n(f[k]), before: Math.round(before), after: Math.round(running), adds: Math.round(running - before) };
  });
}

function Compounding({ f, r }) {
  const rows = steps(f);
  const mult = rows.length ? (rows[2].after / Math.max(rows[0].before, 1)) : 1;
  const simple = n(f.grow_customers) + n(f.grow_value) + n(f.grow_frequency);
  return (
    <div className="panel">
      <div className="stack" style={{ gap: 4 }}>
        <span className="label">Why small levers compound</span>
        <p className="small muted">Added up, these sound like {pct(simple)}%. Multiplied — which is what really happens — they're {pct(r.growth_pct)}% (×{mult.toFixed(3)}).</p>
      </div>
      <ol className="lv-steps">
        {rows.map((x) => (
          <li key={x.lever}>
            <span>+{pct(x.pct)}% from {x.lever}</span>
            <span className="money">{rs(x.before)} → {rs(x.after)}</span>
            <span className="small muted">adds <span className="money">{rs(x.adds)}</span></span>
          </li>
        ))}
      </ol>
      <span className="small muted">From your own numbers, a year. The last lever adds the most because it multiplies the first two.</span>
    </div>
  );
}

const TYPE = { trading: "a trading business", clinic: "a clinic", food: "a food business", services: "a services business", manufacturing: "a manufacturer" };
const SOURCE = { website: "website", guide: "lead magnet", whatsapp: "WhatsApp", email: "email", card: "card / QR", qr: "card / QR" };

/** Ways to get more leads: 5–7 ideas a month that fit this kind of business, each with a first step and the hub feature that does it. */
function LeadIdeas() {
  const { me, toast } = useHub();
  const [d, setD] = useState(null);
  const [loaded, , loadErr] = useLoad("/levers/ideas");
  const [err, setErr] = useState("");
  useEffect(() => { if (loaded) setD(loaded); }, [loaded]);
  if (!d) return <div className="panel">{loadErr ? <FormError msg={loadErr.message} /> : <Loading />}</div>;
  const cur = d.current;
  const sources = Object.entries(d.sources || {});
  const tierName = (feature) => me?.tiers?.[me?.feature_tiers?.[feature] || "lite"]?.name || "a higher plan";
  const tick = async (i, tried) => setD(await api(`/levers/ideas/${i.n}/tried`, { method: "POST", body: { tried } }));
  const suggest = async () => { setD(await api("/levers/ideas", { method: "POST" })); toast("This month's ideas are ready"); };
  return (
    <div className="panel active">
      <div className="row between">
        <div className="stack" style={{ gap: 4 }}>
          <span className="label">Ways to get more leads</span>
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
                    ? <span className="small muted">{i.feature_label} unlocks with {tierName(i.feature)}. <Link to={i.path}>See it</Link></span>
                    : <span className="small"><Link to={i.path}>Open {i.feature_label} →</Link></span>
                )}
              </div>
            </div>
          ))}
        </div>
      )}
      <FormError msg={err} />
      <div className="row">
        <Busy className={`btn ${cur ? "ghost" : "primary"}`} onError={setErr} run={suggest}>{cur ? "Suggest again" : "Suggest ideas"}</Busy>
        <span className="small muted">Uses 1 AI run. Written from your Knowledge Hub{cur ? "; ideas you ticked are kept" : ""}.</span>
      </div>
      {d.past?.length > 0 && (
        <span className="small muted">Earlier: {d.past.map((p) => `${p.month} — ${p.tried} of ${p.ideas} tried`).join(" · ")}</span>
      )}
    </div>
  );
}

function Lever({ title, label, value, onValue, grow, onGrow, growLabel, step = "any" }) {
  return (
    <div className="panel pg-lever">
      <span className="label">{title}</span>
      <Field label={label}><Num min="0" step={step} value={value} onChange={onValue} /></Field>
      <div className="grow">
        <span className="small muted">{growLabel}</span>
        <div className="row" style={{ gap: 6, flexWrap: "nowrap" }}>
          <span className="small muted">+</span>
          <Num min="0" max="500" step="1" style={{ width: 76 }} aria-label={`${growLabel} (%)`} value={grow} onChange={onGrow} />
          <span className="small muted">%</span>
        </div>
      </div>
    </div>
  );
}

export default function Levers() {
  const { toast } = useHub();
  const [loaded] = useLoad("/levers");
  const [f, setF] = useState(BLANK);
  const [saved, setSaved] = useState(null);
  const [err, setErr] = useState("");
  useEffect(() => {
    if (!loaded) return;
    setSaved(loaded.levers);
    if (loaded.levers) setF(Object.fromEntries(KEYS.map((k) => [k, loaded.levers[k] ?? BLANK[k]])));
  }, [loaded]);

  const head = { kicker: "Structure · Revenue levers", id: "PR-03", pillar: "Structure", money: true };
  const sub = "Revenue = customers × average value × how often they buy. Grow each a little and the total grows a lot.";
  if (!loaded) return <Sheet {...head} title={<>Three small levers, one big <em>jump</em>.</>} sub={sub}><Loading /></Sheet>;

  const r = calc(f);
  const set = (k) => (v) => setF({ ...f, [k]: v });
  const setStage = (k, v) => setF({ ...f, funnel: { ...f.funnel, [k]: v } });
  const hasNumbers = r.revenue > 0;
  const pledgeText = () => `I pledge to grow my customers by ${pct(n(f.grow_customers))}%, my average sale by ${pct(n(f.grow_value))}% and how often customers buy by ${pct(n(f.grow_frequency))}%` +
    (hasNumbers ? ` — from ${rs(r.revenue)} to ${rs(r.new_revenue)} a year.` : ` — ${pct(r.growth_pct)}% more revenue.`);
  const save = async () => {
    const body = {
      customers: n(f.customers), avg_value: n(f.avg_value), frequency: n(f.frequency),
      grow_customers: n(f.grow_customers), grow_value: n(f.grow_value), grow_frequency: n(f.grow_frequency),
      funnel: Object.fromEntries(Object.entries(f.funnel || {}).map(([k, v]) => [k, String(v || "").trim()]).filter(([, v]) => v)),
      pledge: f.pledge.trim(),
    };
    const d = await api("/levers", { method: "PUT", body });
    setSaved(d.levers);
    toast("Saved");
  };

  return (
    <Sheet {...head} sub={sub}
      title={<>{pct(r.growth_pct)}% more revenue from three small <em>levers</em>.</>}>
      <div className="pg-formula">
        <Lever title="Customers" label="Customers a year" value={f.customers} onValue={set("customers")}
          grow={f.grow_customers} onGrow={set("grow_customers")} growLabel="More customers" step="1" />
        <span className="op" aria-hidden="true">×</span>
        <Lever title="Average value" label="Average sale (₹)" value={f.avg_value} onValue={set("avg_value")}
          grow={f.grow_value} onGrow={set("grow_value")} growLabel="Bigger sale" />
        <span className="op" aria-hidden="true">×</span>
        <Lever title="Frequency" label="Purchases a year, each" value={f.frequency} onValue={set("frequency")}
          grow={f.grow_frequency} onGrow={set("grow_frequency")} growLabel="More often" step="0.1" />
      </div>

      <div className="pg-stats" aria-live="polite">
        <Stat label="Revenue today" value={hasNumbers ? rs(r.revenue) : "—"} money note="a year" />
        <Stat label="New revenue" value={hasNumbers ? rs(r.new_revenue) : "—"} money note="a year, with the growth above" />
        <div className="stat"><span className="label">Growth</span><span className="num aqua">{pct(r.growth_pct)}%</span><span className="small muted">10% on each lever ≈ 33.1%</span></div>
        <Stat label="Extra a year" value={hasNumbers ? `+${rs(r.extra)}` : "—"} money />
      </div>
      {!hasNumbers && <p className="small muted">Fill in the three numbers for last year to see your revenue in rupees.</p>}
      {hasNumbers && <Compounding f={f} r={r} />}

      <LeadIdeas />

      <div className="panel">
        <div className="stack" style={{ gap: 4 }}>
          <span className="label">Your 5-stage funnel</span>
          <p className="small muted">For each stage, one thing you'll do this quarter to pull its lever — and which AI tool helps.</p>
        </div>
        <div>
          {loaded.stages.map((s, i) => (
            <div key={s.key} className="pg-stage">
              <span className="n">{i + 1}</span>
              <Field label={s.label}>
                <textarea className="textarea" maxLength={400} placeholder={HINTS[s.key] || "What we'll do"} value={f.funnel?.[s.key] || ""} onChange={(e) => setStage(s.key, e.target.value)} />
              </Field>
            </div>
          ))}
        </div>
      </div>

      <div className="panel active">
        <div className="row between">
          <span className="label">The 10×10×10 pledge</span>
          {saved?.pledged_at && <span className="small muted">Pledged on {day(saved.pledged_at)}</span>}
        </div>
        <p className="small muted">Write it in your own words. Saying it out loud, with a date, is what makes it real.</p>
        <textarea className="textarea" maxLength={600} value={f.pledge} placeholder="I pledge to grow my customers, average sale and frequency by 10% each by 31 March."
          aria-label="Your pledge" onChange={(e) => setF({ ...f, pledge: e.target.value })} />
        {!f.pledge.trim() && <div><button className="btn sm ghost" onClick={() => setF({ ...f, pledge: pledgeText() })}>Start from my numbers</button></div>}
      </div>

      <FormError msg={err} />
      <div className="row">
        <Busy className="btn primary" onError={setErr} run={save}>Save my levers</Busy>
        {saved?.updated_at && <span className="small muted">Saved {ago(saved.updated_at)}</span>}
      </div>
    </Sheet>
  );
}
