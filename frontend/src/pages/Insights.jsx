import React, { useState } from "react";
import { day } from "../api.js";
import { Gate, Locked, Sheet, Stat, useHub, useLoad } from "../ui.jsx";
import "./membership.css";

const fmt = (x) => new Intl.NumberFormat("en-IN", { maximumFractionDigits: 1 }).format(x || 0);
const pct = (x) => (x == null ? "—" : `${fmt(x)}%`);
const hours = (h) => (h == null ? "—" : h < 1 ? `${Math.max(1, Math.round(h * 60))} min` : h < 48 ? `${fmt(h)} h` : `${fmt(h / 24)} days`);
const short = (iso) => new Date(`${iso}T00:00:00`).toLocaleDateString("en-IN", { day: "numeric", month: "short" });

const CONV = {
  leads: "of website views became an inquiry",
  contacted: "of inquiries got a reply",
  won: "of those you replied to became customers",
};

function Funnel() {
  const [days, setDays] = useState(30);
  const [f] = useLoad(`/insights/funnel?days=${days}`, [days]);
  const max = f ? Math.max(1, ...f.steps.map((s) => s.n)) : 1;
  return (
    <div className="stack">
      <div className="row between">
        <span className="label">{f ? `${day(f.from)} – ${day(f.to)}` : ""}</span>
        <div className="tabs">{[7, 30, 90].map((d) => <button key={d} className={days === d ? "on" : ""} onClick={() => setDays(d)}>{d} days</button>)}</div>
      </div>
      {f && (
        <>
          <div className="panel">
            <div className="funnel">
              {f.steps.map((s, i) => (
                <React.Fragment key={s.key}>
                  {i > 0 && <div className="conv">{s.rate == null ? "—" : <><b style={{ color: "var(--paper)" }}>{pct(s.rate)}</b> {CONV[s.key]}</>}</div>}
                  <div className="step" title={`${s.label}: ${fmt(s.n)}`}>
                    <span>{s.label}</span>
                    <div className="track"><i style={{ width: `${(s.n / max) * 100}%` }} /></div>
                    <span className="n">{fmt(s.n)}</span>
                  </div>
                </React.Fragment>
              ))}
            </div>
            {f.steps[0].n === 0 && f.steps[1].n === 0 && (
              <p className="small muted">Nothing in these {f.days} days yet. Share your website link where your customers already are — WhatsApp Status, groups and your Google listing.</p>
            )}
          </div>
          <div className="statgrid">
            <Stat label="Inquiry to customer" value={pct(f.overall)} note="of all inquiries in this period" />
            <Stat label="Waiting for a reply" value={fmt(f.waiting)} note="still marked New" />
            <Stat label="In progress" value={fmt(f.open)} note="replied, not yet won or lost" />
            <Stat label="Lost" value={fmt(f.lost)} note="marked Lost" />
          </div>
          <p className="small muted">A view is one opening of your live website. Inquiries are counted on the day they arrive; their status is what it is today.</p>
        </>
      )}
    </div>
  );
}

/** One measure, twelve weeks: thin columns from a single baseline, the latest and the highest week labelled. */
function WeekBars({ title, weeks, field, format = fmt, unit }) {
  const W = 372, H = 150, left = 6, top = 22, bottom = 22;
  const vals = weeks.map((w) => w[field]);
  const max = Math.max(0, ...vals.filter((v) => v != null));
  const slot = (W - left * 2) / weeks.length;
  const bw = Math.min(20, slot - 6);
  const y = (v) => H - bottom - ((H - top - bottom) * v) / (max || 1);
  const last = weeks.length - 1;
  const peak = max > 0 ? vals.lastIndexOf(max) : -1;
  return (
    <div className="panel" style={{ gap: 8 }}>
      <div className="row between"><span className="label">{title}</span><span className="small muted">{unit}</span></div>
      <svg className="wk" viewBox={`0 0 ${W} ${H}`} role="img" aria-label={`${title}, last 12 weeks: ${weeks.map((w) => `${short(w.start)} ${w[field] == null ? "none" : format(w[field])}`).join(", ")}`}>
        <line className="grid" x1={left} x2={W - left} y1={top} y2={top} />
        {weeks.map((w, i) => {
          const v = w[field];
          const x = left + i * slot + (slot - bw) / 2;
          const h = v ? Math.max(H - bottom - y(v), 2) : 0;
          const yy = H - bottom - h;
          const r = Math.min(4, h);
          const label = i === last || i === peak;
          return (
            <g key={w.start} className="slot">
              <rect className="hit" x={left + i * slot} y={top - 18} width={slot} height={H - top + 18 - bottom} />
              {h > 0 && <path className="col" d={`M${x},${H - bottom} V${yy + r} Q${x},${yy} ${x + r},${yy} H${x + bw - r} Q${x + bw},${yy} ${x + bw},${yy + r} V${H - bottom} Z`} />}
              {label && v != null && max > 0 && <text className="val" x={x + bw / 2} y={yy - 5} textAnchor="middle">{format(v)}</text>}
              <title>{`Week of ${short(w.start)}${i === last ? " (so far)" : ""}: ${v == null ? "no data" : format(v)}`}</title>
            </g>
          );
        })}
        <line className="base" x1={left} x2={W - left} y1={H - bottom} y2={H - bottom} />
        <text x={left} y={H - 6}>{short(weeks[0].start)}</text>
        <text x={W - left} y={H - 6} textAnchor="end">This week</text>
        {max === 0 && <text x={W / 2} y={(H - bottom + top) / 2} textAnchor="middle">Nothing yet</text>}
      </svg>
    </div>
  );
}

function Dashboard() {
  const [d] = useLoad("/insights/dashboard");
  const [table, setTable] = useState(false);
  if (!d) return null;
  const { this: a, last: b } = d.months;
  const vs = (y, f = fmt) => `${b.label}: ${y == null ? "—" : f(y)}`;
  return (
    <div className="stack">
      <span className="label">{a.label} so far, against all of {b.label}</span>
      <div className="statgrid">
        <Stat label="Inquiries" value={fmt(a.leads)} note={vs(b.leads)} />
        <Stat label="Customers won" value={fmt(a.won)} note={vs(b.won)} />
        <Stat label="Inquiry to customer" value={pct(a.conversion)} note={vs(b.conversion, pct)} />
        <Stat label="First reply, median" value={hours(a.response_hours)} note={vs(b.response_hours, hours)} />
        <Stat label="Website views" value={fmt(a.views)} note={vs(b.views)} />
        <Stat label="AI tokens used" value={fmt(a.runs)} note={vs(b.runs)} />
      </div>
      <div className="row between">
        <span className="label">Last 12 weeks · Monday to Sunday</span>
        <button className="btn sm ghost" onClick={() => setTable(!table)}>{table ? "Show charts" : "Show as table"}</button>
      </div>
      {table ? (
        <table className="t cards">
          <thead><tr><th>Week of</th><th>Inquiries</th><th>Won</th><th>First reply</th><th>Views</th><th>AI tokens</th></tr></thead>
          <tbody>{[...d.weeks].reverse().map((w, i) => (
            <tr key={w.start}><td><b>{short(w.start)}</b>{i === 0 && <span className="small muted"> · so far</span>}</td>
              <td data-l="Inquiries">{fmt(w.leads)}</td><td data-l="Won">{fmt(w.won)}</td><td data-l="First reply">{hours(w.response_hours)}</td>
              <td data-l="Views">{fmt(w.views)}</td><td data-l="AI tokens">{fmt(w.runs)}</td></tr>
          ))}</tbody>
        </table>
      ) : (
        <div className="grid2">
          <WeekBars title="Inquiries" weeks={d.weeks} field="leads" unit="per week" />
          <WeekBars title="Customers won" weeks={d.weeks} field="won" unit="by week the inquiry came in" />
          <WeekBars title="First reply, median" weeks={d.weeks} field="response_hours" format={hours} unit="lower is better" />
          <WeekBars title="Website views" weeks={d.weeks} field="views" unit="per week" />
          <WeekBars title="AI tokens used" weeks={d.weeks} field="runs" unit="per week" />
        </div>
      )}
      <p className="small muted">First reply is the time from an inquiry arriving to the first time you change its status in Leads.</p>
    </div>
  );
}

function InsightsInner() {
  const { me } = useHub();
  const [tab, setTab] = useState("funnel");
  return (
    <Sheet kicker="Systems · Funnel & dashboard" id="SY-03" pillar="Systems" title={<>Visitors, inquiries, <em>customers</em>.</>}
      sub="How many website visitors become inquiries and customers, and how this month compares with the last.">
      <div className="tabs" role="tablist">
        {[["funnel", "Funnel"], ["dashboard", "Dashboard"]].map(([k, l]) => (
          <button key={k} role="tab" aria-selected={tab === k} className={tab === k ? "on" : ""} onClick={() => setTab(k)}>{l}</button>
        ))}
      </div>
      {tab === "funnel" && <Funnel />}
      {tab === "dashboard" && (me.features.funnel ? <Dashboard /> : <Locked feature="funnel" what="Weekly performance dashboard" />)}
    </Sheet>
  );
}

export default function Insights() {
  return (
    <Gate feature="funnel" role="manager" kicker="Systems · Funnel & dashboard" id="SY-03"
      what="See how many website visitors become inquiries and customers, how fast you reply, and how each week compares — without a spreadsheet.">
      <InsightsInner />
    </Gate>
  );
}
