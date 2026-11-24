import React, { useCallback, useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { ago, api, rs } from "../api.js";
import { FormError, Sheet, Switch, useHub, useLoad } from "../ui.jsx";
import { Loading, Pill } from "../v2.jsx";
import "./aiteam.css";

/* v2.4 AI team (Start → AI team): one card per AI role, with what it did today and this month. Every number is counted on
   the server from the owner's own records (GET /api/ai-team). "Working" shows only while a job really runs; nothing here
   moves unless the records do. Refreshes every 15 seconds while the page is visible, and stops when it is hidden. */

const n = (x) => new Intl.NumberFormat("en-IN", { maximumFractionDigits: 1 }).format(Number(x) || 0);
const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
const dshort = (iso) => {
  const [, m, d] = String(iso || "").split("-").map(Number);
  return m && d ? `${d} ${MONTHS[m - 1]}` : "";
};
const when = (iso) => (iso ? new Date(iso).toLocaleString("en-IN", { day: "numeric", month: "short", hour: "numeric", minute: "2-digit" }) : "");
const STATE_KIND = { working: "ai", waiting: "warn", setup: "warn", off: "off", idle: "" };

/** Polls while the tab is visible; stops when it is hidden and picks up again (with a fresh load) when it comes back. */
function usePoll(reload, seconds) {
  useEffect(() => {
    let id = null;
    const start = () => { if (id == null) id = setInterval(reload, seconds * 1000); };
    const stop = () => { if (id != null) { clearInterval(id); id = null; } };
    const onVis = () => { if (document.visibilityState === "visible") { reload(); start(); } else stop(); };
    if (document.visibilityState === "visible") start();
    document.addEventListener("visibilitychange", onVis);
    return () => { stop(); document.removeEventListener("visibilitychange", onVis); };
  }, [reload, seconds]);
}

function Value({ c, v }) {
  if (c.kind === "money") return <span className="money">{rs(v)}</span>;
  if (c.kind === "avg") return v == null ? <span className="muted" title="No calls scored in this period">–</span> : <>{n(v)}</>;
  return <>{n(v)}</>;
}

function StatePill({ state, label }) {
  return <span className={`at-state s-${state}`}><Pill kind={STATE_KIND[state]}>{state === "working" && <i className="at-dot" aria-hidden="true" />}{label}</Pill></span>;
}

function RoleCard({ r, canSwitch, onSwitch }) {
  const [saving, setSaving] = useState(false);
  const [err, setErr] = useState("");
  const flip = async (on) => {
    setSaving(true); setErr("");
    try { await onSwitch(r.key, on); } catch (e) { if (!e.handled) setErr(e.message); } finally { setSaving(false); }
  };
  return (
    <article className={`at-card s-${r.state}`}>
      <div className="at-card-head">
        <div className="at-who"><h3>{r.title}</h3><p className="small muted">{r.job}</p></div>
        <StatePill state={r.state} label={r.state_label} />
      </div>
      {r.waiting_text && <Link className="at-wait" to={r.waiting_link || r.page}>{r.waiting_text} <span aria-hidden="true">→</span></Link>}
      {r.setup && <p className="at-setup small">{r.setup}</p>}
      <table className="at-counts">
        <thead><tr><th scope="col"><span className="at-sr">What</span></th><th scope="col">Today</th><th scope="col">This month</th></tr></thead>
        <tbody>{r.counts.map((c) => (
          <tr key={c.key}><th scope="row">{c.label}</th><td><Value c={c} v={c.today} /></td><td><Value c={c} v={c.month} /></td></tr>
        ))}</tbody>
      </table>
      {r.next && <p className="small muted at-next">{r.next.text} <Link to={r.next.path}>Open</Link></p>}
      <div className="at-last small">
        {r.last
          ? <><span className="muted">Last:</span> <Link to={r.last.link}>{r.last.text}</Link> <time className="muted" dateTime={r.last.at} title={when(r.last.at)}>· {ago(r.last.at)}</time></>
          : <span className="muted">No finished action yet.</span>}
      </div>
      <div className="at-card-foot">
        {canSwitch
          ? <Switch checked={r.on} disabled={saving} label={r.on ? "On" : "Switched off"} onChange={flip} />
          : <span className="small muted">{r.on ? "On" : "Switched off"} · only the owner can change this</span>}
        {r.page && <Link className="small" to={r.page}>Open</Link>}
      </div>
      <FormError msg={err} />
    </article>
  );
}

function Weekly({ w, canSwitch, onSwitch }) {
  const [err, setErr] = useState("");
  const [saving, setSaving] = useState(false);
  if (!w?.available) return null;
  const flip = async (on) => {
    setSaving(true); setErr("");
    try { await onSwitch("growth_weekly", on); } catch (e) { if (!e.handled) setErr(e.message); } finally { setSaving(false); }
  };
  return (
    <div className="panel at-weekly">
      <div className="row between">
        <span className="label">Last week · {dshort(w.from)} – {dshort(w.to)}</span>
        {canSwitch
          ? <Switch checked={w.on} disabled={saving} label={w.on ? "Monday line on WhatsApp" : "Monday line off"} onChange={flip} />
          : <span className="small muted">{w.on ? "Monday line on" : "Monday line off"}</span>}
      </div>
      {w.line ? <p className="at-weekly-line">{w.line}</p> : <p className="small muted">Nothing to report for last week. The Monday line only goes out when there is something to report.</p>}
      {w.sent && <span className="small muted">Sent {when(w.sent.at)}{w.sent.whatsapp ? " to your WhatsApp" : " in your hub (WhatsApp isn't set up)"}.</span>}
      {!w.sent && w.line && w.on && <span className="small muted">Every Monday at 9am this line goes to your WhatsApp.</span>}
      <FormError msg={err} />
    </div>
  );
}

function Strip({ s }) {
  if (!s) return null;
  const cells = [["Businesses found", n(s.found)], ["Messages you sent", n(s.sent)], ["Replies", n(s.replies)],
    ["Deals from new customers", n(s.deals)], ["Value you entered", <span className="money">{rs(s.value)}</span>]];
  return (
    <section className="at-strip" aria-label="This month">
      <span className="label">This month</span>
      <div className="at-strip-grid">
        {cells.map(([k, v]) => <div key={k} className="at-cell"><span className="at-num">{v}</span><span className="small muted">{k}</span></div>)}
      </div>
      {s.next && <p className="small muted">{s.next.text} <Link to={s.next.path}>Open</Link></p>}
    </section>
  );
}

function Hours({ h }) {
  if (!h) return null;
  return (
    <section className="panel at-hours" aria-label="Hours saved">
      <div className="row between">
        <span className="label">Hours saved this month</span>
        <Pill>Estimated</Pill>
      </div>
      <div className="row" style={{ alignItems: "baseline" }}>
        <span className="at-big">{n(h.hours)} h</span>
        <span className="small muted">= the actions below × the minutes a person would spend on each</span>
      </div>
      <p className="small muted">The minutes are our assumptions, not measured. Only this number is an estimate; every other number on this page is counted from your records.</p>
      <table className="at-min">
        <thead><tr><th scope="col">Action</th><th scope="col">Minutes each</th><th scope="col">Done this month</th></tr></thead>
        <tbody>{h.rows.map((r) => <tr key={r.key}><th scope="row">{r.what}</th><td>{r.minutes}</td><td>{n(r.n)}</td></tr>)}</tbody>
      </table>
    </section>
  );
}

const FEED_STATUS = { skipped: "Skipped", failed: "Not done", error: "Not done" };

function Feed({ feed }) {
  return (
    <section className="at-feed" aria-label="Latest actions">
      <span className="label">Latest actions</span>
      {!feed.length ? <p className="small muted">Nothing yet. Each finished action appears here, with a link to what was done.</p> : (
        <ol className="at-feed-list">
          {feed.map((f) => (
            <li key={f.id} className={f.status !== "done" ? "not-done" : ""}>
              <time dateTime={f.at} title={when(f.at)}>{ago(f.at)}</time>
              <span className="at-feed-role">{f.role_title}</span>
              <Link to={f.link} className="at-feed-text">{f.text}</Link>
              {f.status !== "done" && <Pill kind="off">{FEED_STATUS[f.status] || "Not done"}</Pill>}
            </li>
          ))}
        </ol>
      )}
    </section>
  );
}

export default function AiTeam() {
  const { toast } = useHub();
  const [data, reload, err] = useLoad("/ai-team");
  const [at, setAt] = useState(null);
  useEffect(() => { if (data) setAt(new Date().toISOString()); }, [data]);
  const refresh = useCallback(() => reload(), [reload]);
  usePoll(refresh, data?.refresh_seconds || 15);

  const onSwitch = async (role, on) => {
    await api(`/ai-team/roles/${role}`, { method: "PATCH", body: { on } });
    toast(on ? "Switched on" : "Switched off");
    await reload();
  };

  const head = { kicker: "Start · AI team", id: "AI-01", title: <>Your AI team, <em>at work</em>.</>,
    sub: "What each AI role did today and this month, counted from your own records. A switched-off role does no automatic work." };
  if (!data) return <Sheet {...head}>{err ? <FormError msg={err.message} /> : <Loading />}</Sheet>;
  return (
    <Sheet {...head}>
      <Weekly w={data.weekly} canSwitch={data.can_switch} onSwitch={onSwitch} />
      <Strip s={data.strip} />
      {!data.roles.length ? <p className="muted">No AI role is part of your plan yet.</p> : (
        <div className="at-grid">
          {data.roles.map((r) => <RoleCard key={r.key} r={r} canSwitch={data.can_switch} onSwitch={onSwitch} />)}
        </div>
      )}
      <Hours h={data.hours} />
      <Feed feed={data.feed} />
      <p className="small muted">Updated {ago(at)} · refreshes every {data.refresh_seconds} seconds while this page is open.</p>
    </Sheet>
  );
}
