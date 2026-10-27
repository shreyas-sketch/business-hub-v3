import React, { useEffect, useMemo, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { api, day } from "../api.js";
import { Busy, FormError, Sheet, Stat } from "../ui.jsx";
import { Loading } from "../v2.jsx";
import "./audit.css";

/* Engine Audit (v2.3): the Day 1 activity. Have it or Missing for each of the 14 processes; for a gap, roughly
   what it costs a month and the hours it takes a week. Totals add up live, here on the phone (the server does the same sums
   when the audit is saved). 0 tokens. */

const fmt = (n) => "₹" + new Intl.NumberFormat("en-IN", { maximumFractionDigits: 0 }).format(Math.round(n || 0));
const hrs = (n) => { const v = Math.round((n || 0) * 10) / 10; return `${v}`; };
const CHOICES = [["have", "Have it"], ["missing", "Missing"]];
// an audit or draft saved when there was a "Not sure" answer: read as Missing
const statusOf = (s) => (s === "unsure" ? "missing" : s);
const NOTE = "Estimated from your own numbers.";

function num(v) { const x = Number(v); return Number.isFinite(x) && x > 0 ? x : 0; }

/** The same sums as engine_audit.compute on the server. */
export function compute(items, answers, revenueMonth, capPct) {
  const rows = items.map((it) => {
    const a = answers[it.key] || {};
    const gap = a.status === "missing";
    const cost = gap ? Math.min(num(a.monthly_cost), 1e9) : 0;
    const hours = gap ? Math.min(num(a.hours_week), 1000) : 0;
    return { key: it.key, engine: it.engine, status: a.status || null, cost, hours, growth: cost * it.rate, back: hours * it.hours_rate };
  });
  const growthRaw = rows.reduce((s, r) => s + r.growth, 0);
  const cap = revenueMonth > 0 ? Math.round(revenueMonth * capPct / 100) : null;
  const capped = cap != null && growthRaw > cap;
  const factor = capped && growthRaw ? cap / growthRaw : 1;
  rows.forEach((r) => { r.growth = Math.round(r.growth * factor); });
  const sums = (rs) => ({
    inPlace: rs.filter((r) => r.status === "have").length, total: rs.length, answered: rs.filter((r) => r.status).length,
    cost: rs.reduce((s, r) => s + r.cost, 0), growth: rs.reduce((s, r) => s + r.growth, 0),
    hours: rs.reduce((s, r) => s + r.hours, 0), back: rs.reduce((s, r) => s + r.back, 0),
  });
  const byEngine = {};
  rows.forEach((r) => { (byEngine[r.engine] = byEngine[r.engine] || []).push(r); });
  return { rows, total: { ...sums(rows), capped, cap }, engines: Object.fromEntries(Object.entries(byEngine).map(([k, rs]) => [k, sums(rs)])) };
}

/** Where the fix is: open now, opening on a day of the event, or in the plan. */
function Fix({ fix, small = true }) {
  if (!fix) return null;
  const cls = small ? "btn sm" : "btn";
  if (fix.state === "open") return <Link className={cls} to={fix.path}>Open {fix.label}</Link>;
  if (fix.state === "day") return <span className="pill on">{fix.label} · opens on Day {fix.opens_day}</span>;
  return <Link className={`${cls} money`} to="/plans">Unlock {fix.label}</Link>;
}

function Totals({ t, year, setYear, capPct }) {
  const m = year ? 12 : 1;
  const w = year ? 52 : 1;
  return (
    <div className="au-totals" aria-live="polite">
      <div className="au-tot-grid">
        <div><span className="label">Cost of gaps / {year ? "year" : "month"}</span><b className="money">{fmt(t.cost * m)}</b></div>
        <div><span className="label">Growth unlocked / {year ? "year" : "month"}</span><b className="money">{fmt(t.growth * m)}</b></div>
        <div><span className="label">Hours lost / {year ? "year" : "week"}</span><b>{hrs(t.hours * w)}</b></div>
        <div><span className="label">Hours back / {year ? "year" : "week"}</span><b>{hrs(t.back * w)}</b></div>
      </div>
      <div className="au-tot-foot">
        <span className="small muted">{NOTE}{t.capped ? ` Growth capped at a conservative level (${capPct}% of a month's sales).` : ""}</span>
        <div className="au-toggle" role="group" aria-label="Show totals for">
          <button type="button" aria-pressed={!year} onClick={() => setYear(false)}>Month</button>
          <button type="button" aria-pressed={year} onClick={() => setYear(true)}>Year</button>
        </div>
      </div>
    </div>
  );
}

function Item({ it, a, onChange }) {
  const set = (patch) => onChange({ ...a, ...patch });
  const gap = a.status === "missing";
  return (
    <div className={`au-item ${a.status ? `is-${a.status}` : ""}`}>
      <p className="au-st" id={`au-${it.key}`}>{it.statement}</p>
      <div className="au-choice" role="group" aria-labelledby={`au-${it.key}`}>
        {CHOICES.map(([v, w]) => (
          <button key={v} type="button" aria-pressed={a.status === v} onClick={() => set({ status: v })}>{w}</button>
        ))}
      </div>
      {gap && (
        <div className="au-gap">
          <label className="au-q">
            <span>Roughly how much is this costing you a month?</span>
            <div className="au-in">
              <span className="au-pre">₹</span>
              <input className="input" inputMode="numeric" placeholder="e.g. 25000" value={a.monthly_cost ?? ""}
                onChange={(e) => set({ monthly_cost: e.target.value.replace(/[^\d.]/g, "") })} aria-label="Rupees a month" />
            </div>
            <small className="muted">{it.hint_cost}</small>
          </label>
          <label className="au-q">
            <span>Roughly how many hours a week does this take from you or your team?</span>
            <div className="au-in">
              <input className="input" inputMode="decimal" placeholder="e.g. 5" value={a.hours_week ?? ""}
                onChange={(e) => set({ hours_week: e.target.value.replace(/[^\d.]/g, "") })} aria-label="Hours a week" />
              <span className="au-post">hours</span>
            </div>
            <small className="muted">{it.hint_hours}</small>
          </label>
          <div className="au-fix"><span className="small muted">Fixed by</span><Fix fix={it.fix} /></div>
        </div>
      )}
    </div>
  );
}

function cleanAnswers(answers) {
  const out = {};
  Object.entries(answers).forEach(([k, a]) => {
    if (!a || !a.status) return;
    out[k] = { status: statusOf(a.status), monthly_cost: num(a.monthly_cost) || null, hours_week: num(a.hours_week) || null };
  });
  return out;
}

function Take({ data, initial, onDone, onCancel }) {
  const [answers, setAnswers] = useState(initial.answers || {});
  const [revenue, setRevenue] = useState(initial.revenue_month ?? data.revenue?.month ?? "");
  const [year, setYear] = useState(false);
  const [err, setErr] = useState("");
  const items = data.items;
  const live = useMemo(() => compute(items, answers, num(revenue), data.cap_pct), [items, answers, revenue, data.cap_pct]);
  const n = live.total.answered;

  // Save the answers so far a moment after the last change (one small write), so a refresh or another phone picks up here.
  const first = useRef(true);
  useEffect(() => {
    if (first.current) { first.current = false; return undefined; }
    const t = setTimeout(() => {
      api("/audit/draft", { method: "PUT", body: { answers: cleanAnswers(answers), revenue_month: num(revenue) || null } }).catch(() => {});
    }, 1500);
    return () => clearTimeout(t);
  }, [answers, revenue]);

  const submit = async () => {
    onDone(await api("/audit", { method: "POST", body: { answers: cleanAnswers(answers), revenue_month: num(revenue) || null } }));
  };

  return (
    <div className="au-take">
      <div className="panel">
        <span className="label">How it works</span>
        <p>Tap <b>Have it</b> or <b>Missing</b> for each line. For a gap, say roughly what it costs you a month and the hours
          it takes a week — a rough guess is fine. The totals add up as you go. It takes about 5 minutes and uses 0 tokens.</p>
        <label className="au-q">
          <span>Your sales in a typical month (optional)</span>
          <div className="au-in">
            <span className="au-pre">₹</span>
            <input className="input" inputMode="numeric" placeholder="e.g. 500000" value={revenue}
              onChange={(e) => setRevenue(e.target.value.replace(/[^\d.]/g, ""))} aria-label="Sales in a typical month" />
          </div>
          <small className="muted">
            {data.revenue?.month && Number(revenue) === data.revenue.month ? `Filled in from ${data.revenue.source}. ` : ""}
            It keeps the growth estimate conservative: never more than {data.cap_pct}% of a month's sales.
          </small>
        </label>
      </div>

      {data.engines.map((e) => {
        const its = items.filter((i) => i.engine === e.key);
        const s = live.engines[e.key];
        return (
          <section key={e.key} className="panel au-engine">
            <div className="au-eh">
              <div><h2>{e.label} engine</h2><p className="small muted">{e.blurb}</p></div>
              <div className="au-es">
                <b>{s.inPlace} of {s.total}</b><span className="small muted">in place</span>
                {(s.cost > 0 || s.hours > 0) && <span className="small"><span className="money">{fmt(s.cost)}</span>/mo · {hrs(s.hours)} h/wk</span>}
              </div>
            </div>
            {its.map((it) => (
              <Item key={it.key} it={it} a={answers[it.key] || {}} onChange={(a) => setAnswers({ ...answers, [it.key]: a })} />
            ))}
          </section>
        );
      })}

      <Totals t={live.total} year={year} setYear={setYear} capPct={data.cap_pct} />
      <FormError msg={err} />
      <div className="row">
        <Busy className="btn primary" disabled={n < items.length} run={submit} onError={setErr}>See my result</Busy>
        {onCancel && <button className="btn ghost" onClick={onCancel}>Cancel</button>}
        <span className="small muted">{n} of {items.length} answered</span>
      </div>
    </div>
  );
}

async function shareCard(id) {
  const url = `/api/audit/${id}/card.png`;
  try {
    const blob = await (await fetch(url, { credentials: "same-origin" })).blob();
    const file = new File([blob], "engine-audit.png", { type: "image/png" });
    if (navigator.canShare && navigator.canShare({ files: [file] })) {
      await navigator.share({ files: [file], title: "My Engine Audit" });
      return;
    }
  } catch (e) {
    if (e && e.name === "AbortError") return;
  }
  window.open(url, "_blank", "noopener");
}

function Result({ data, audit, onRetake }) {
  const [year, setYear] = useState(false);
  const t = audit.totals;
  const fixes = Object.fromEntries(data.items.map((i) => [i.key, i.fix]));
  const statements = Object.fromEntries(audit.answers.map((r) => [r.item_key, r]));
  const gaps = audit.answers.filter((r) => statusOf(r.status) === "missing");
  const c = audit.change;
  const m = year ? 12 : 1;
  const w = year ? 52 : 1;
  const per = year ? "year" : "month";
  const perw = year ? "year" : "week";
  return (
    <div className="stack au-result">
      <div className="statgrid">
        <Stat label={`Cost of gaps / ${per}`} value={fmt(t.cost_month * m)} money />
        <Stat label={`Growth unlocked / ${per}`} value={fmt(t.growth_month * m)} money />
        <Stat label={`Hours lost / ${perw}`} value={hrs(t.hours_week * w)} />
        <Stat label={`Hours you get back / ${perw}`} value={hrs(t.hours_back_week * w)} />
      </div>
      <div className="row between au-note">
        <span className="small muted">
          {NOTE} Not a promise or a guarantee.{t.capped ? ` Growth is capped at a conservative level: ${t.cap_pct}% of a month's sales (${fmt(t.cap_amount)}).` : ""}
        </span>
        <div className="au-toggle" role="group" aria-label="Show totals for">
          <button type="button" aria-pressed={!year} onClick={() => setYear(false)}>Month</button>
          <button type="button" aria-pressed={year} onClick={() => setYear(true)}>Year</button>
        </div>
      </div>
      {c && (
        <p className="small">
          Since your last audit ({day(c.since)}): {c.in_place > 0 ? `${c.in_place} more in place` : c.in_place < 0 ? `${-c.in_place} fewer in place` : "the same number in place"}
          {c.cost_month !== 0 ? `, gaps cost ${fmt(Math.abs(c.cost_month))} a month ${c.cost_month < 0 ? "less" : "more"}` : ""}
          {c.hours_week !== 0 ? `, ${hrs(Math.abs(c.hours_week))} hours a week ${c.hours_week < 0 ? "fewer" : "more"} lost` : ""}.
        </p>
      )}

      <div className="panel">
        <span className="label">Your engines</span>
        <div className="au-score">
          {audit.engines.map((e) => (
            <div key={e.key} className="au-srow">
              <div className="row between"><b>{e.label}</b><span className="small muted">{e.in_place} of {e.total} in place</span></div>
              <div className="meter"><i style={{ width: `${e.total ? (e.in_place / e.total) * 100 : 0}%` }} /></div>
              {(e.cost_month > 0 || e.hours_week > 0) && (
                <span className="small muted">Gaps: <span className="money">{fmt(e.cost_month)}</span> a month · {hrs(e.hours_week)} hours a week</span>
              )}
            </div>
          ))}
        </div>
      </div>

      {audit.top_gaps.length > 0 && (
        <div className="panel active">
          <span className="label">Your top {audit.top_gaps.length} gap{audit.top_gaps.length > 1 ? "s" : ""}</span>
          {audit.top_gaps.map((k, i) => {
            const r = statements[k];
            if (!r) return null;
            return (
              <div key={k} className="au-top">
                <span className="au-n">{i + 1}</span>
                <div className="stack" style={{ gap: 6 }}>
                  <b>{r.statement}</b>
                  <span className="small muted">
                    <span className="money">{fmt(r.monthly_cost)}</span> a month · {hrs(r.hours_week)} hours a week
                  </span>
                  <div><Fix fix={fixes[k]} /></div>
                </div>
              </div>
            );
          })}
        </div>
      )}

      {gaps.length > audit.top_gaps.length && (
        <details className="panel">
          <summary>All {gaps.length} gaps and what fixes them</summary>
          {gaps.map((r) => (
            <div key={r.item_key} className="au-gaprow">
              <span>{r.statement}</span>
              <Fix fix={fixes[r.item_key]} />
            </div>
          ))}
        </details>
      )}

      <div className="row">
        <a className="btn primary" href={`/api/audit/${audit.id}/pdf`}>Download the PDF</a>
        <button className="btn" onClick={() => shareCard(audit.id)}>Share the image</button>
        <button className="btn ghost" onClick={onRetake}>Take the audit again</button>
      </div>

      {data.history.length > 1 && (
        <div className="panel">
          <span className="label">Your audits</span>
          {data.history.map((h, i) => (
            <div key={h.id} className="au-hist">
              <span>{i === 0 ? "Latest · " : ""}{day(h.completed_at)}</span>
              <span className="small muted">{h.in_place} of {h.total} in place · <span className="money">{fmt(h.cost_month)}</span>/mo · {hrs(h.hours_week)} h/wk</span>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

export default function Audit() {
  const [data, setData] = useState(null);
  const [err, setErr] = useState("");
  const [taking, setTaking] = useState(false);
  const [shown, setShown] = useState(null);
  const load = () => api("/audit").then((d) => { setData(d); return d; }).catch((e) => setErr(e.message));
  useEffect(() => { load(); }, []);

  const head = { kicker: "Start · Engine Audit", id: "EA-01", pillar: "Start" };
  if (err) return <Sheet {...head} title={<>Your <em>Engine Audit</em>.</>}><FormError msg={err} /></Sheet>;
  if (!data) return <Sheet {...head} title={<>Your <em>Engine Audit</em>.</>}><Loading /></Sheet>;
  const audit = shown || data.latest;
  if (audit && !taking) {
    return (
      <Sheet {...head} title={<><em>{audit.totals.in_place} of {audit.totals.total}</em> in place across your engines.</>}
        sub={`Your Engine Audit from ${day(audit.completed_at)}. Fix the top gaps first — each one opens the part of the hub that does it.`}>
        <Result data={data} audit={audit} onRetake={() => { setShown(null); setTaking(true); }} />
      </Sheet>
    );
  }
  const initial = data.draft && Object.keys(data.draft.answers || {}).length
    ? { ...data.draft, answers: Object.fromEntries(Object.entries(data.draft.answers).map(([k, a]) => [k, { ...a, status: statusOf(a?.status) }])) }
    : data.latest ? { answers: Object.fromEntries(data.latest.answers.map((r) => [r.item_key, {
        status: statusOf(r.status), monthly_cost: r.monthly_cost || "", hours_week: r.hours_week || "" }])),
        revenue_month: null }
      : { answers: {} };
  return (
    <Sheet {...head} title={<>Check your business against the <em>6 engines</em>.</>}
      sub="Every business runs on six engines. See which ones run without you, what each gap costs you in hours and rupees, and what fixing it unlocks.">
      <Take data={data} initial={initial} onCancel={data.latest ? () => setTaking(false) : null}
        onDone={async (res) => { await load(); setShown(res); setTaking(false); window.scrollTo(0, 0); }} />
    </Sheet>
  );
}
