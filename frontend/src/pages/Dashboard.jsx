import React, { useCallback, useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { ago, api, rs } from "../api.js";
import { Busy, FormError, LoadError, Sheet, Switch, useHub, useLoad } from "../ui.jsx";
import { Loading, Pill } from "../v2.jsx";
import "./dashboard.css";

/* The Smart Dashboard (v2.1, 3.2): one tile per widget from GET /api/dashboard. Every number is counted on the server
   from the hub's own records; tiles mark the two that are forecasts ("estimated"). */

const num = (x) => new Intl.NumberFormat("en-IN", { maximumFractionDigits: 1 }).format(Number(x) || 0);
/** ₹3.2 L · ₹45,000 · ₹1.2 Cr — the full figure is in the title. */
const short = (x) => {
  const v = Math.abs(Number(x) || 0);
  const sign = Number(x) < 0 ? "−" : "";
  if (v >= 1e7) return `${sign}₹${(v / 1e7).toFixed(1).replace(/\.0$/, "")} Cr`;
  if (v >= 1e5) return `${sign}₹${(v / 1e5).toFixed(1).replace(/\.0$/, "")} L`;
  return `${sign}${rs(Math.round(v))}`;
};
const Money = ({ v, className = "" }) => <span className={`money ${className}`} title={rs(Math.round(Number(v) || 0))}>{short(v)}</span>;
const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
/** "2026-12-01" → "1 Dec" (no time-zone shifts for plain dates). */
const dshort = (iso) => {
  const [, m, d] = String(iso || "").split("-").map(Number);
  return m && d ? `${d} ${MONTHS[m - 1]}` : "";
};
const Est = () => <Pill>estimated</Pill>;
const KV = ({ rows }) => (
  <dl className="db-kv">{rows.filter(Boolean).map(([k, v]) => <React.Fragment key={k}><dt>{k}</dt><dd>{v}</dd></React.Fragment>)}</dl>
);
const STATUS_KIND = { on_track: "on", slightly_behind: "warn", well_behind: "warn" };

/* ───────── one renderer per tile ───────── */
const RENDER = {
  today: (d) => (
    <ul className="db-today">
      {d.items.map((i) => (
        <li key={i.key} className={i.n ? "" : "zero"}>
          <Link to={i.path}><span>{i.label}</span><b>{i.n}</b></Link>
        </li>
      ))}
    </ul>
  ),
  leads: (d) => {
    const top = Math.max(1, ...d.by_source.map((s) => s.n));
    return (
      <>
        <div className="db-trio">
          <div><span className="db-num">{d.today}</span><span className="small muted">today</span></div>
          <div><span className="db-num">{d.week}</span><span className="small muted">7 days</span></div>
          <div><span className="db-num">{d.month}</span><span className="small muted">30 days</span></div>
        </div>
        {d.by_source.length > 0 && (
          <div className="db-bars" aria-label="Inquiries in the last 30 days by source">
            {d.by_source.map((s) => (
              <div key={s.source} className="db-barrow"><span>{s.source}</span><span className="bar"><i style={{ width: `${(s.n / top) * 100}%` }} /></span><b>{s.n}</b></div>
            ))}
          </div>
        )}
        <span className="small muted">First reply: {d.median_reply_hours == null ? "no replies recorded yet" : `${num(d.median_reply_hours)} h (median, 30 days)`}</span>
      </>
    );
  },
  pipeline: (d) => {
    const top = Math.max(1, ...d.stages.map((s) => s.n));
    return (
      <>
        <div className="db-pair"><span className="db-big">{d.open}</span><span className="small muted">open {d.open === 1 ? "deal" : "deals"} · <Money v={d.open_value} /></span></div>
        <div className="db-bars">
          {d.stages.map((s) => (
            <div key={s.key} className="db-barrow"><span>{s.label}</span><span className="bar"><i style={{ width: `${(s.n / top) * 100}%` }} /></span><b>{s.n}</b></div>
          ))}
        </div>
        {d.stuck && <span className="small muted">Most deals sit at <b className="paper">{d.stuck.label}</b> ({d.stuck.n}).</span>}
      </>
    );
  },
  target: (d) => (
    <>
      <div className="row between"><Pill kind={STATUS_KIND[d.status]}>{d.status_label}</Pill><span className="small muted">{d.month_gone_pct}% of the month gone</span></div>
      <div className="db-pair"><Money v={d.month_to_date} className="db-big" /><span className="small muted">of <Money v={d.target} /> · {d.pct_of_target}%</span></div>
      <div className="db-pace" role="img" aria-label={`${d.pct_of_target}% of target; ${d.month_gone_pct}% of the month gone`}>
        <i style={{ width: `${Math.min(d.pct_of_target, 100)}%` }} /><b style={{ left: `${Math.min(d.month_gone_pct, 100)}%` }} />
      </div>
      <KV rows={[
        ["Heading for", <><Money v={d.projected} /> <Est /></>],
        d.gap > 0 && ["Short by", <Money v={d.gap} />],
        d.days_left > 0 && ["Needed a day", <Money v={d.required_daily} />],
      ]} />
    </>
  ),
  money: (d) => (
    <>
      <div className="db-pair"><Money v={d.overdue} className="db-big" /><span className="small muted">overdue · {d.overdue_count} {d.overdue_count === 1 ? "payment" : "payments"}</span></div>
      <KV rows={[
        ["Due this week", <Money v={d.due_this_week} />],
        ["Collected this month", <Money v={d.collected_month} />],
        d.oldest_overdue_days != null && ["Oldest overdue", `${d.oldest_overdue_days} ${d.oldest_overdue_days === 1 ? "day" : "days"}`],
      ]} />
    </>
  ),
  cash_flow: (d) => (
    <>
      <div className="row between"><span className="label">Lowest in 30 days</span><Est /></div>
      <div className="db-pair"><Money v={d.lowest} className={`db-big ${d.lowest < 0 ? "neg" : ""}`} /><span className="small muted">on {dshort(d.lowest_date)}</span></div>
      {d.shortfall_date
        ? <p className="db-alert">Short from {dshort(d.shortfall_date)}{d.short_by ? <> by <Money v={d.short_by} /></> : null}.</p>
        : <span className="small muted">No shortfall in the next 30 days.</span>}
      {d.label && <span className="small muted">{d.label}</span>}
    </>
  ),
  invoices: (d) => (
    <>
      <div className="db-pair"><Money v={d.invoiced_month} className="db-big" /><span className="small muted">invoiced this month · {d.invoices_month}</span></div>
      <KV rows={[
        ["Paid", <Money v={d.paid} />],
        ["Not paid yet", <Money v={d.unpaid} />],
        ["Open quotations", <>{d.open_quotes} · <Money v={d.open_quotes_value} /></>],
      ]} />
    </>
  ),
  social: (d) => (
    <>
      <div className="db-trio">
        <div><span className="db-num">{d.week}</span><span className="small muted">posted this week</span></div>
        <div><span className="db-num">{d.month}</span><span className="small muted">this month</span></div>
        <div><span className="db-num">{d.streak_weeks}</span><span className="small muted">{d.streak_weeks === 1 ? "week" : "weeks"} in a row</span></div>
      </div>
      {Object.keys(d.by_platform || {}).length > 0 && (
        <span className="small muted">{Object.entries(d.by_platform).map(([k, v]) => `${k} ${v}`).join(" · ")}</span>
      )}
      <KV rows={[
        ["Posts written this month", d.created_month],
        d.next && ["Next post", `${dshort(d.next.date)}${d.next.occasion ? ` · ${d.next.occasion}` : ""}`],
      ]} />
    </>
  ),
  compliance: (d) => (
    <>
      {d.overdue > 0 && <p className="db-alert">{d.overdue} overdue</p>}
      {d.next.length ? (
        <ul className="db-list">{d.next.map((i) => <li key={`${i.title}${i.due_date}`}><span>{i.title}</span><b>{dshort(i.due_date)}</b></li>)}</ul>
      ) : <span className="small muted">Nothing due ahead.</span>}
      {d.expiring_docs > 0 && <span className="small muted">{d.expiring_docs} {d.expiring_docs === 1 ? "document expires" : "documents expire"} within 30 days.</span>}
    </>
  ),
  website: (d) => (
    <>
      <div className="row between"><Pill kind={d.live ? "on" : ""}>{d.live ? "Live" : "Not live"}</Pill></div>
      <div className="db-trio">
        <div><span className="db-num">{d.views_7}</span><span className="small muted">views, 7 days</span></div>
        <div><span className="db-num">{d.views_30}</span><span className="small muted">30 days</span></div>
        <div><span className="db-num">{d.magnet_downloads_30}</span><span className="small muted">lead magnet sign-ups</span></div>
      </div>
      <span className="small muted">Views to inquiries (30 days): {d.conversion_pct == null ? "no views yet" : `${num(d.conversion_pct)}%`}</span>
    </>
  ),
  sops: (d) => (
    <>
      <div className="db-pair"><span className="db-big">{d.written}</span><span className="small muted">SOPs written</span></div>
      {d.has_map ? <span className="small muted">{d.without_sop} {d.without_sop === 1 ? "process has" : "processes have"} no SOP yet.</span>
        : <span className="small muted">Map your processes to see which need an SOP.</span>}
    </>
  ),
  knowledge: (d) => (
    <>
      <div className="db-pair"><span className="db-big">{d.percent}%</span><span className="small muted">complete</span></div>
      <div className="meter"><i style={{ width: `${d.percent}%` }} /></div>
      {d.next ? <span className="small muted">Next: {d.next}</span> : <span className="small muted">Everything is filled in.</span>}
    </>
  ),
  growth: (d) => (
    <>
      <div className="db-trio">
        <div><span className="db-num">{d.ai_replies_week}</span><span className="small muted">AI replies this week</span></div>
        <div><span className="db-num">{d.calls_week}</span><span className="small muted">calls this week</span></div>
        <div><span className="db-num">{num(d.hours_saved_month)}</span><span className="small muted">hours saved this month</span></div>
      </div>
      <span className="small muted">Hours saved are <Est /> from the drafts and messages your AI team wrote.</span>
    </>
  ),
};

function Tile({ t }) {
  const { me, upgrade } = useHub();
  if (t.state === "teaser" && t.opens_day) {   // v2.3: the live event opens this tile's engine on a later day
    return (
      <article className={`db-tile teaser ${t.size} t-${t.key}`}>
        <div className="db-head"><span className="label">{t.title}</span><Pill>Day {t.opens_day}</Pill></div>
        <p className="small muted">{t.what}.</p>
        <span className="small">Opens on <b className="paper">Day {t.opens_day}</b> of the event.</span>
      </article>
    );
  }
  if (t.state === "teaser") {
    return (
      <article className={`db-tile teaser ${t.size} t-${t.key}`}>
        <div className="db-head"><span className="label">{t.title}</span><Pill kind="money">{t.tier_name}</Pill></div>
        <p className="small muted">{t.what}.</p>
        <span className="small">Unlocks with <b className="paper">{t.tier_name}</b>.</span>
        {me?.workspace?.role === "owner"
          ? <div><button className="btn sm money" onClick={() => upgrade(t.tier, t.feature)}>See {t.tier_name}</button></div>
          : <span className="small muted">Ask the business owner to upgrade.</span>}
      </article>
    );
  }
  const d = t.data || {};
  const render = RENDER[t.key];
  return (
    <article className={`db-tile ${t.size} t-${t.key}`}>
      <Link className="db-cover" to={t.link} aria-label={`Open ${t.title}`} />
      <div className="db-head"><span className="label">{t.title}</span><span className="db-go" aria-hidden="true">→</span></div>
      {d.error ? <p className="small muted">{d.error}</p>
        : t.empty ? (
          <div className="db-empty">
            {t.empty_state?.text && <p className="small muted">{t.empty_state.text}</p>}
            {t.empty_state?.path && <Link className="btn sm" to={t.empty_state.path}>{t.empty_state.action}</Link>}
          </div>
        ) : render ? render(d) : null}
    </article>
  );
}

function Editor({ data, onSaved, onCancel }) {
  const { toast } = useHub();
  const [rows, setRows] = useState(() => [
    ...data.tiles.map((t) => ({ key: t.key, title: t.title, state: t.state, hidden: false })),
    ...data.hidden.map((t) => ({ ...t, hidden: true })),
  ]);
  const [err, setErr] = useState("");
  const move = (i, by) => {
    const j = i + by;
    if (j < 0 || j >= rows.length) return;
    const next = rows.slice();
    [next[i], next[j]] = [next[j], next[i]];
    setRows(next);
  };
  const save = async () => {
    const d = await api("/dashboard/layout", { method: "PUT", body: { order: rows.map((r) => r.key), hidden: rows.filter((r) => r.hidden).map((r) => r.key) } });
    toast("Dashboard saved");
    onSaved(d);
  };
  const reset = async () => {
    const d = await api("/dashboard/layout", { method: "DELETE" });
    toast("Back to the standard dashboard");
    onSaved(d);
  };
  return (
    <div className="panel active">
      <div className="stack" style={{ gap: 4 }}>
        <span className="label">Edit dashboard</span>
        <p className="small muted">Move tiles up or down, and hide the ones you don't need. Locked tiles always come after yours.</p>
      </div>
      <ol className="db-edit">
        {rows.map((r, i) => (
          <li key={r.key} className={r.hidden ? "off" : ""}>
            <span className="db-edit-name">{r.title}{r.state === "teaser" && <span className="small muted"> · locked</span>}</span>
            <div className="row" style={{ gap: 6, flexWrap: "nowrap" }}>
              <button className="btn sm ghost" disabled={i === 0} onClick={() => move(i, -1)} aria-label={`Move ${r.title} up`}>↑</button>
              <button className="btn sm ghost" disabled={i === rows.length - 1} onClick={() => move(i, 1)} aria-label={`Move ${r.title} down`}>↓</button>
              <Switch checked={!r.hidden} disabled={r.key === "today"} label={r.hidden ? "Hidden" : "Shown"}
                onChange={(on) => setRows(rows.map((x) => (x.key === r.key ? { ...x, hidden: !on } : x)))} />
            </div>
          </li>
        ))}
      </ol>
      <FormError msg={err} />
      <div className="row">
        <Busy className="btn primary" onError={setErr} run={save}>Save dashboard</Busy>
        <button className="btn ghost" onClick={onCancel}>Cancel</button>
        {data.customised && <Busy className="btn sm ghost" onError={setErr} run={reset}>Reset to standard</Busy>}
      </div>
    </div>
  );
}

export default function Dashboard() {
  const [loaded, reload, err] = useLoad("/dashboard");
  const [data, setData] = useState(null);
  const [at, setAt] = useState(null);
  const [editing, setEditing] = useState(false);
  useEffect(() => { if (loaded) { setData(loaded); setAt(new Date().toISOString()); } }, [loaded]);

  // Live while the tab is open: the server caches for a minute, so this never costs more than one count a minute.
  const every = (data?.refresh_seconds || 60) * 1000;
  const tick = useCallback(() => { if (document.visibilityState === "visible" && !editing) reload(); }, [reload, editing]);
  useEffect(() => {
    const id = setInterval(tick, every);
    document.addEventListener("visibilitychange", tick);
    return () => { clearInterval(id); document.removeEventListener("visibilitychange", tick); };
  }, [tick, every]);

  const head = { kicker: "Start · Dashboard", id: "DB-01", title: <>Your business, <em>today</em>.</>,
    sub: "Live from your hub: leads, money, posts, targets and what needs you. Tap any tile to open it." };
  if (!data) return <Sheet {...head}>{err ? <LoadError err={err} /> : <Loading />}</Sheet>;
  return (
    <Sheet {...head} actions={!editing && <button className="btn sm ghost" onClick={() => setEditing(true)}>Edit dashboard</button>}>
      {editing && <Editor data={data} onCancel={() => setEditing(false)} onSaved={(d) => { setData(d); setAt(new Date().toISOString()); setEditing(false); }} />}
      <div className="db-grid">
        {data.tiles.map((t) => <Tile key={t.key} t={t} />)}
      </div>
      <p className="small muted">Updated {ago(at)} · refreshes every minute while this page is open.{data.hidden.length > 0 && ` ${data.hidden.length} hidden — Edit dashboard to show ${data.hidden.length === 1 ? "it" : "them"}.`}</p>
    </Sheet>
  );
}
