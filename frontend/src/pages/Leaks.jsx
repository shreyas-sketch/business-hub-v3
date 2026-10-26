import React, { useEffect, useState } from "react";
import { ago, api, rs } from "../api.js";
import { Busy, Field, FormError, Sheet, useHub, useLoad } from "../ui.jsx";
import { Empty, Loading, Num, Pill } from "../v2.jsx";
import "./program.css";

const n = (x) => Number(x) || 0;
const fmt = (x) => new Intl.NumberFormat("en-IN", { maximumFractionDigits: 1 }).format(x || 0);

function Step({ s, prev, worst }) {
  const scale = Math.max(s.rate, s.target) * 1.15 || 1;
  return (
    <div className={`pg-leak ${worst ? "worst" : ""}`}>
      <div className="stack" style={{ gap: 4 }}>
        <span className="small"><b>{prev.label}</b> → <b>{s.label}</b></span>
        <span className="small muted">{s.no_data ? `nothing came in from ${prev.label.toLowerCase()}` : `${fmt(s.n)} of ${fmt(prev.n)}`}{worst || s.no_data ? "" : s.rate >= s.target ? " · on target" : ""}</span>
        {worst && <span><Pill kind="on">Biggest leak</Pill></span>}
      </div>
      <div className="track" role="img" aria-label={`${fmt(s.rate)}% against a target of ${fmt(s.target)}%`}>
        <i style={{ width: `${Math.min(100, (s.rate / scale) * 100)}%` }} />
        <b style={{ left: `${Math.min(100, (s.target / scale) * 100)}%` }} title={`Target ${fmt(s.target)}%`} />
      </div>
      <div className="stack" style={{ gap: 4, justifyItems: "end" }}>
        <span className="rate">{fmt(s.rate)}%</span>
        <span className="small muted">target {fmt(s.target)}%</span>
      </div>
    </div>
  );
}

export default function Leaks() {
  const { refresh, toast } = useHub();
  const [loaded] = useLoad("/leaks");
  const [data, setData] = useState(null);
  const [nums, setNums] = useState({});
  const [targets, setTargets] = useState({});
  const [avg, setAvg] = useState("");
  const [err, setErr] = useState("");
  const fill = (d) => {
    setData(d);
    const s = d.saved;
    const hub = d.from_hub || {};
    const useHubNums = !s && Object.values(hub).some((x) => x > 0);
    setNums(Object.fromEntries(d.stages.map(({ key }) => [key, s ? s.numbers[key] ?? "" : useHubNums ? hub[key] ?? 0 : ""])));
    setTargets({ ...d.default_targets, ...(s?.targets || {}) });
    setAvg(s?.avg_sale || "");
  };
  useEffect(() => { if (loaded) fill(loaded); }, [loaded]);

  const head = { kicker: "Systems · Funnel leak finder", id: "PR-06", pillar: "Systems" };
  const sub = "Put in your funnel numbers. See the step where most customers slip away, and what fixing it is worth.";
  if (!data) return <Sheet {...head} title={<>Find where customers slip <em>away</em>.</>} sub={sub}><Loading /></Sheet>;

  const { stages, saved, from_hub: hub } = data;
  const body = {
    numbers: Object.fromEntries(stages.map(({ key }) => [key, n(nums[key])])),
    targets: Object.fromEntries(Object.keys(data.default_targets).map((k) => [k, n(targets[k]) || data.default_targets[k]])),
    avg_sale: n(avg),
  };
  const dirty = !saved || JSON.stringify(body) !== JSON.stringify({ numbers: saved.numbers, targets: saved.targets, avg_sale: saved.avg_sale });
  const result = saved?.result;
  const leak = result?.biggest_leak;
  const fixed = result?.if_fixed;
  const fixes = saved?.fixes;
  const ready = n(nums[stages[0].key]) > 0 || n(nums[stages[1].key]) > 0;

  return (
    <Sheet {...head} sub={sub}
      title={leak ? <>Your biggest <em>leak</em> is {leak.label.toLowerCase()}.</> : <>Find where customers slip <em>away</em>.</>}>
      <div className="panel">
        <div className="row between">
          <span className="label">Your numbers for one period</span>
          <button className="btn sm" onClick={() => setNums(Object.fromEntries(stages.map(({ key }) => [key, hub[key] ?? 0])))}>Use my hub numbers</button>
        </div>
        <p className="small muted">Last 30 days in the hub — {stages.map(({ key, label }) => `${label}: ${fmt(hub[key])}`).join(" · ")}. Meetings and proposals count the leads you moved to those stages on the sales pipeline.</p>
        <div className="pg-fields" style={{ gridTemplateColumns: "repeat(auto-fit, minmax(130px, 1fr))" }}>
          {stages.map(({ key, label }) => (
            <Field key={key} label={label}><Num min="0" step="1" value={nums[key]} onChange={(v) => setNums({ ...nums, [key]: v })} /></Field>
          ))}
        </div>
        <span className="label">Targets</span>
        <div className="pg-fields">
          {stages.slice(1).map(({ key, label }, i) => (
            <Field key={key} label={`${stages[i].label} → ${label}`} hint="% that should make it">
              <Num min="0.1" max="100" step="any" value={targets[key]} onChange={(v) => setTargets({ ...targets, [key]: v })} />
            </Field>
          ))}
        </div>
        <div className="grid2">
          <Field label="Average sale (₹)" hint={n(avg) ? <span className="money">{rs(n(avg))}</span> : "So the hub can say what a fix is worth."}>
            <Num min="0" value={avg} onChange={setAvg} />
          </Field>
        </div>
        <FormError msg={err} />
        <div className="row">
          <Busy className="btn primary" disabled={!ready || !dirty} onError={setErr} run={async () => {
            fill(await api("/leaks", { method: "PUT", body }));
            toast("Funnel checked");
          }}>Find my biggest leak</Busy>
          {saved?.updated_at && !dirty && <span className="small muted">Saved {ago(saved.updated_at)}</span>}
          {saved && dirty && <span className="pg-stale">Changes not saved — the result below is from your last check.</span>}
        </div>
        {!ready && <span className="hint">Start with your inquiries. Don't know your website visitors? Leave it at 0 — the hub starts from inquiries.</span>}
      </div>

      {!result ? (
        <Empty>Add your numbers and tap Find my biggest leak. Use one period for all of them — the last 30 days is a good start.</Empty>
      ) : (
        <div className="stack">
          {result.steps.slice(1).map((s, i) => (s.skipped
            ? <p key={s.key} className="small muted">No website visitors given, so your funnel starts at inquiries ({fmt(s.n)}).</p>
            : <Step key={s.key} s={s} prev={result.steps[i]} worst={leak?.key === s.key} />))}
          {!leak && (result.steps.every((s) => !n(s.n))
            ? <div className="panel flat"><p className="small">All the numbers are 0, so there's nothing to compare yet. Add last month's numbers.</p></div>
            : <div className="panel flat"><p className="small">Every step is at or above its target. Raise a target to find the next leak.</p></div>)}
          {leak && fixed && fixed.extra_customers > 0 && (
            <div className="pg-fixed">
              <span className="label">If you fix this one step</span>
              <span className="money" style={{ font: "400 26px/1.2 var(--head)" }}>
                <span className="pg-nowrap">+{fmt(fixed.extra_customers)} {fixed.extra_customers === 1 ? "customer" : "customers"}</span>{fixed.extra_sales ? <>, <span className="pg-nowrap">+{rs(fixed.extra_sales)}</span></> : ""}
              </span>
              <span className="small muted">From the same {result.starts_at === "inquiries" ? "inquiries" : "visitors"}, if {leak.label.toLowerCase()} reached {fmt(leak.target)}% instead of {fmt(leak.rate)}%.{!fixed.extra_sales ? " Add your average sale to see it in rupees." : ""}</span>
            </div>
          )}
        </div>
      )}

      {result && (
        <div className="panel">
          <div className="row between">
            <span className="label">Fixes for your leak</span>
            {saved?.fixes_at && <span className="small muted">{ago(saved.fixes_at)}</span>}
          </div>
          {fixes?.summary && <p>{fixes.summary}</p>}
          {fixes?.fixes?.length > 0 && (
            <ol className="read-list">
              {fixes.fixes.map((x, i) => (
                <li key={i}>
                  <b>{x.action}</b>
                  {(x.stage || x.why) && <div className="small muted">{[x.stage, x.why].filter(Boolean).join(" · ")}</div>}
                </li>
              ))}
            </ol>
          )}
          <div className="row">
            <Busy className={`btn ai ${fixes ? "" : "primary"}`} disabled={dirty} run={async () => { fill(await api("/leaks/fixes", { method: "POST" })); refresh(); }}>
              {fixes ? "Get fresh fixes" : "Get fixes"}
            </Busy>
            <span className="small muted">{dirty ? "Save your numbers first." : "Uses 1 AI token, written for your business and this leak."}</span>
          </div>
        </div>
      )}
    </Sheet>
  );
}
