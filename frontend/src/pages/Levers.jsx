import React, { useEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { ago, api, day, rs } from "../api.js";
import { Busy, Field, FormError, LoadError, Sheet, Stat, useHub, useLoad } from "../ui.jsx";
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

/** What the server keeps: numbers as numbers, the funnel's empty stages left out. */
const bodyOf = (f) => ({
  customers: n(f.customers), avg_value: n(f.avg_value), frequency: n(f.frequency),
  grow_customers: n(f.grow_customers), grow_value: n(f.grow_value), grow_frequency: n(f.grow_frequency),
  funnel: Object.fromEntries(Object.entries(f.funnel || {}).map(([k, v]) => [k, String(v || "").trim()]).filter(([, v]) => v)),
  pledge: String(f.pledge || "").trim(),
});

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

const EFFORT = { low: "Low effort", medium: "Some effort", high: "Big effort" };
const IMPACT = { low: "Small impact", medium: "Good impact", high: "Big impact" };
const SRC = { knowledge: "Knowledge Hub", website: "Your website", lead_magnet: "Your lead magnet", competitor: "Competitor", business_type: "Your kind of business" };

/** 45 growth ideas: 15 per lever, written for this business from the Knowledge Hub, the website, the lead magnet and the
    competitor summary. Each idea says where it came from, its first step, effort and impact (never a rupee promise). */
function GrowthIdeas() {
  const { toast } = useHub();
  const [loaded, , loadErr] = useLoad("/multiplier");
  const [d, setD] = useState(null);
  const [lever, setLever] = useState("customers");
  const [err, setErr] = useState("");
  const [all, setAll] = useState("");
  const [more, setMore] = useState(false);
  useEffect(() => { if (loaded) setD(loaded); }, [loaded]);
  useEffect(() => setMore(false), [lever]);
  if (!d) return <div className="panel">{loadErr ? <LoadError err={loadErr} /> : <Loading />}</div>;
  const lv = d.levers.find((x) => x.key === lever) || d.levers[0];
  const write = async (key) => { setD(await api(`/multiplier/${key}`, { method: "POST" })); };
  const writeAll = async () => {
    for (const x of d.levers) {
      setAll(`Writing ideas for ${x.label.toLowerCase()}…`);
      try { setD(await api(`/multiplier/${x.key}`, { method: "POST" })); } catch (e) { setAll(""); throw e; }
    }
    setAll("");
    toast("45 ideas ready");
  };
  const status = async (i, st) => setD(await api(`/multiplier/${lever}/${i.key}/status`, { method: "POST", body: { status: st } }));
  const used = Object.entries(d.sources).filter(([, v]) => v).map(([k]) => SRC[k]);
  return (
    <div className="panel active">
      <div className="row between">
        <div className="stack" style={{ gap: 4 }}>
          <span className="label">45 ways to grow — 15 for each lever</span>
          <p className="small muted">Written for your business from {used.length ? used.join(", ") : "your Knowledge Hub"}{d.sources.competitor ? "" : ". Add a competitor's website in the Knowledge Hub for sharper ideas"}.</p>
        </div>
        {d.total > 0 && <Pill kind="on">{d.doing} doing · {d.done} done</Pill>}
      </div>
      <div className="mp-levers" role="tablist">
        {d.levers.map((x) => (
          <button key={x.key} role="tab" aria-selected={lever === x.key} className={`mp-lever ${lever === x.key ? "on" : ""}`} onClick={() => setLever(x.key)}>
            <b>{x.label}</b><span className="small muted">{x.ideas.length ? `${x.ideas.length} ideas · ${x.doing + x.done} on the go` : "Not written yet"}</span>
          </button>
        ))}
      </div>
      <p className="small muted">{lv.meaning.charAt(0).toUpperCase() + lv.meaning.slice(1)}.</p>
      {lv.ideas.length > 0 && (
        <div className="lv-ideas">
          {(more ? lv.ideas : lv.ideas.slice(0, 5)).map((i, n) => (
            <div key={i.key} className={`lv-idea mp-idea ${i.status === "done" ? "done" : ""}`}>
              <span className="mp-n" aria-hidden="true">{n + 1}</span>
              <div className="stack" style={{ gap: 4 }}>
                <b className="lv-title">{i.title}</b>
                <span className="small muted">{i.why}</span>
                {i.first_step && <span className="small">First step: {i.first_step}</span>}
                <div className="row" style={{ gap: 6 }}>
                  <Pill>{SRC[i.source] || "Knowledge Hub"}</Pill><Pill>{EFFORT[i.effort]}</Pill><Pill kind="ai">{IMPACT[i.impact]}</Pill>
                  {i.feature && i.path && (i.locked ? <span className="small muted">{i.feature_label} (locked)</span>
                    : <Link className="small" to={i.path}>Open {i.feature_label} →</Link>)}
                </div>
                <div className="row" style={{ gap: 6 }}>
                  <button className={`btn sm ${i.status === "doing" ? "primary" : "ghost"}`} aria-pressed={i.status === "doing"}
                    onClick={() => status(i, i.status === "doing" ? "" : "doing").catch((x) => toast(x.message, "err"))}>Doing</button>
                  <button className={`btn sm ${i.status === "done" ? "primary" : "ghost"}`} aria-pressed={i.status === "done"}
                    onClick={() => status(i, i.status === "done" ? "" : "done").catch((x) => toast(x.message, "err"))}>Done</button>
                </div>
              </div>
            </div>
          ))}
        </div>
      )}
      {lv.ideas.length > 5 && <button className="btn sm ghost" aria-expanded={more} onClick={() => setMore(!more)}>{more ? "Show the first 5" : `Show all ${lv.ideas.length} ideas`}</button>}
      <FormError msg={err} />
      {all && <p className="small muted" aria-live="polite">{all}</p>}
      <div className="row">
        <Busy className={`btn ai ${lv.ideas.length ? "ghost" : "primary"}`} onError={setErr} run={() => write(lv.key)}>
          {lv.ideas.length ? `Write ${lv.label.toLowerCase()} ideas again` : `Write 15 ideas: ${lv.label.toLowerCase()}`}
        </Busy>
        {d.total === 0 && <Busy className="btn ai" onError={setErr} run={writeAll}>Write all 45</Busy>}
        <span className="small muted">
          {lv.ideas.length ? "Uses 1 AI token. Ideas you're doing or have done are kept." : `1 AI token for 15 ideas, or ${d.tokens_all} tokens for all 45.`}
        </span>
      </div>
      <span className="small muted">Ideas, not promises: impact is a rough guide, never a rupee figure. New ideas each month.</span>
      {d.past?.length > 0 && <span className="small muted">Earlier: {d.past.map((p) => `${p.month} — ${p.done} of ${p.ideas} done`).join(" · ")}</span>}
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
  const { me, toast } = useHub();
  const [loaded] = useLoad("/levers");
  const [f, setF] = useState(BLANK);
  const [saved, setSaved] = useState(null);
  const [err, setErr] = useState("");
  const [saving, setSaving] = useState(false);
  const sent = useRef(null);      // the last body the server has, so an unchanged form isn't saved again
  useEffect(() => {
    if (!loaded) return;
    setSaved(loaded.levers);
    if (loaded.levers) {
      const form = Object.fromEntries(KEYS.map((k) => [k, loaded.levers[k] ?? BLANK[k]]));
      setF(form);
      sent.current = JSON.stringify(bodyOf(form));
    } else {
      sent.current = JSON.stringify(bodyOf(BLANK));
    }
  }, [loaded]);
  // Saved as you type (a second after the last change), so numbers are never lost by leaving the page
  useEffect(() => {
    if (!loaded || sent.current === null) return undefined;
    const body = bodyOf(f);
    if (JSON.stringify(body) === sent.current) return undefined;
    const t = setTimeout(async () => {
      setSaving(true);
      try {
        const d = await api("/levers", { method: "PUT", body });
        sent.current = JSON.stringify(body); setSaved(d.levers); setErr("");
      } catch (e) { if (!e.handled) setErr(e.message); } finally { setSaving(false); }
    }, 1000);
    return () => clearTimeout(t);
  }, [f, loaded]);

  const head = { kicker: "Sales · Revenue Multiplier", id: "SA-01", pillar: "Sales", money: true };
  const sub = "Revenue = customers × average value × how often they buy. Grow each a little and the total grows a lot.";
  if (!loaded) return <Sheet {...head} title={<>Three small levers, one big <em>jump</em>.</>} sub={sub}><Loading /></Sheet>;

  const r = calc(f);
  const set = (k) => (v) => setF({ ...f, [k]: v });
  const setStage = (k, v) => setF({ ...f, funnel: { ...f.funnel, [k]: v } });
  const hasNumbers = r.revenue > 0;
  const pledgeText = () => `I pledge to grow my customers by ${pct(n(f.grow_customers))}%, my average sale by ${pct(n(f.grow_value))}% and how often customers buy by ${pct(n(f.grow_frequency))}%` +
    (hasNumbers ? ` — from ${rs(r.revenue)} to ${rs(r.new_revenue)} a year.` : ` — ${pct(r.growth_pct)}% more revenue.`);
  const save = async () => {
    const body = bodyOf(f);
    const d = await api("/levers", { method: "PUT", body });
    sent.current = JSON.stringify(body);
    setSaved(d.levers);
    toast("Saved");
  };
  const history = saved?.history || [];
  const restoreOld = (h) => setF({ ...f, ...Object.fromEntries(["customers", "avg_value", "frequency", "grow_customers", "grow_value", "grow_frequency"].map((k) => [k, h[k]])) });

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
      <p className="small muted" aria-live="polite">{saving ? "Saving…" : saved?.updated_at ? `Your numbers are saved as you type · last saved ${ago(saved.updated_at)}` : "Your numbers are saved as you type."}</p>
      {!hasNumbers && <p className="small muted">Fill in the three numbers for last year to see your revenue in rupees.</p>}
      {history.length > 0 && (
        <details className="panel lv-history">
          <summary><span className="label">Your earlier numbers</span><span className="small muted"> · {history.length} saved</span></summary>
          <ul className="lv-hist">
            {history.map((h, i) => (
              <li key={i}>
                <span className="small">{day(h.at)}</span>
                <span className="small">{Number(h.customers).toLocaleString("en-IN")} customers × {rs(h.avg_value)} × {h.frequency}/yr</span>
                <span className="small money">{rs(h.revenue)} → {rs(h.new_revenue)}</span>
                <button type="button" className="btn sm ghost" onClick={() => restoreOld(h)}>Use these</button>
              </li>
            ))}
          </ul>
        </details>
      )}
      {hasNumbers && <Compounding f={f} r={r} />}

      <GrowthIdeas />

      {me?.features?.lead_ideas && (
        <div className="panel">
          <div className="row between">
            <div className="stack" style={{ gap: 4 }}>
              <span className="label">Ways to get more leads</span>
              <span className="small muted">This month's short list of lead ideas for your kind of business, each with a first step.</span>
            </div>
            <Link className="btn sm" to="/lead-ideas">Open →</Link>
          </div>
        </div>
      )}

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
