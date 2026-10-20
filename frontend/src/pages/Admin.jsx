import React, { useState } from "react";
import { Link } from "react-router-dom";
import { api, localPhone } from "../api.js";
import { Busy, Copy, Field, Sheet, useHub, useLoad } from "../ui.jsx";
import AdminPayments from "../components/AdminPayments.jsx";
import AdminDomains from "../components/AdminDomains.jsx";

const TIERS = ["free", "lite", "program", "running", "growth", "office"];
const pct = (n, d) => (d ? `${Math.round((n / d) * 100)}%` : "—");

const SOURCE_NAMES = { direct: "Came directly", cohort: "Workshop join link", badge: "Website badge", invite: "Invite link", showcase: "Showcase" };
const SOURCE_LABEL = { purchase: "paid", subscription: "Membership", trial: "trial", admin: "set" };

export default function Admin() {
  const { toast, me } = useHub();
  const [days, setDays] = useState(7);
  const [p, reloadPulse] = useLoad(`/admin/pulse?days=${days}`, [days]);
  const [q, setQ] = useState("");
  const [users, reloadUsers] = useLoad(`/admin/users?q=${encodeURIComponent(q)}`, [q]);
  const [cohort, setCohort] = useState({ code: "", name: "", workshop_date: "" });
  if (!p) return null;
  const a = p.activation;
  return (
    <Sheet kicker="Admin · Pulse" id="A-01" pillar="Admin" title={<>Activation, then stickiness, then <em>referral</em>.</>}
      actions={<div className="tabs">{[7, 30].map((d) => <button key={d} className={days === d ? "on" : ""} onClick={() => setDays(d)}>{d} days</button>)}</div>}>
      <nav className="row" aria-label="Admin">
        <Link className="btn sm" to="/admin/features">Features &amp; plans</Link>
        <Link className="btn sm" to="/admin/event">Live event</Link>
        <Link className="btn sm" to="/admin/day1">Day-1 reports</Link>
        <Link className="btn sm" to="/admin/calls">Member calls</Link>
        <Link className="btn sm ghost" to="/admin/recordings">Recordings</Link>
      </nav>
      <div className="grid3">
        <div className="panel"><span className="label">Owners</span><span className="num">{p.totals.owners}</span><span className="small muted">+{p.totals.signups} in {days} days · {p.totals.referred_signups} referred</span></div>
        <div className="panel"><span className="label">Viral K · {days} days</span><span className="num aqua">{p.viral.k ?? "—"}</span><span className="small muted">{p.viral.definition}</span></div>
        <div className="panel"><span className="label">Websites live</span><span className="num">{a.site_live}</span><span className="small muted">{pct(a.site_live, a.owners)} of owners · {p.totals.leads} leads in {days} days</span></div>
      </div>
      <div className="panel">
        <span className="label">Activation funnel</span>
        <div className="progress">
          {[["Business Brain", a.profile], ["Brand message", a.brand], ["Website live", a.site_live], ["First lead", a.first_lead]].map(([l, n]) => (
            <div key={l}><span className="state">{pct(n, a.owners)}</span><span>{l}</span><span className="num" style={{ fontSize: 24 }}>{n}</span></div>))}
        </div>
      </div>
      <div className="grid3">
        <div className="panel"><span className="label">Where new owners came from</span>
          {Object.entries(p.viral.by_source).map(([k, v]) => <div key={k} className="row between small"><span>{SOURCE_NAMES[k] || k}</span><b>{v}</b></div>)}
          <hr className="rule" /><span className="small muted">Link visits — badge {p.viral.visits.badge}, invite {p.viral.visits.invite} · rewards given {p.viral.rewards}</span></div>
        <div className="panel"><span className="label">AI usage · {days} days</span><span className="num">{p.ai.runs}</span>
          <span className="small">runs · est. <span className="money">₹{p.ai.cost_inr}</span></span><span className="small muted">{p.ai.limit_hits} {p.ai.limit_hits === 1 ? "owner" : "owners"} hit their limit</span></div>
        <div className="panel"><span className="label">Plans</span>
          {Object.entries(p.plans.mix).filter(([, v]) => v).map(([k, v]) => <div key={k} className="row between small"><span>{me?.tiers?.[k]?.name || k}</span><b>{v}</b></div>)}
          <span className="small muted">{p.plans.trials} on a referral trial · upgrade clicks: {Object.entries(p.revenue_intent.upgrade_clicks).map(([k, v]) => `${me?.tiers?.[k]?.name || k} ${v}`).join(", ") || "none"}</span></div>
      </div>

      <AdminPayments />

      <div className="panel">
        <span className="label">Workshop cohorts</span>
        <div className="grid3 form-row" style={{ gridTemplateColumns: "1fr 2fr 1fr auto", alignItems: "end" }}>
          <Field label="Code"><input className="input mono" placeholder="BAI-OCT-11" value={cohort.code} onChange={(e) => setCohort({ ...cohort, code: e.target.value.toUpperCase() })} /></Field>
          <Field label="Name"><input className="input" placeholder="Business AI workshop · 11 Oct" value={cohort.name} onChange={(e) => setCohort({ ...cohort, name: e.target.value })} /></Field>
          <Field label="Date"><input className="input" type="date" value={cohort.workshop_date} onChange={(e) => setCohort({ ...cohort, workshop_date: e.target.value })} /></Field>
          <Busy className="btn" run={async () => { const r = await api("/admin/cohorts", { method: "POST", body: { ...cohort, workshop_date: cohort.workshop_date || null } }); toast(`Join link: ${r.join_link}`); setCohort({ code: "", name: "", workshop_date: "" }); reloadPulse(); }}>Create</Busy>
        </div>
        {p.cohorts.length > 0 && (
          <table className="t cards"><thead><tr><th>Cohort</th><th>Signups</th><th>Live</th><th>Brought in</th><th>Join link</th></tr></thead>
            <tbody>{p.cohorts.map((c) => <tr key={c.code}><td><b className="mono">{c.code}</b><div className="small muted">{c.name}</div></td>
              <td data-l="Signups">{c.signups}</td><td data-l="Websites live">{c.sites_live}</td><td data-l="Brought in">{c.brought_in}</td>
              <td><Copy text={`${window.location.origin}/join?c=${c.code}`} label="Copy join link" /></td></tr>)}</tbody></table>
        )}
      </div>

      <div className="stack">
        <div className="row between"><span className="label">Owners</span><input className="input" style={{ maxWidth: 280 }} placeholder="Search phone, business or cohort" value={q} onChange={(e) => setQ(e.target.value)} /></div>
        <table className="t cards">
          <thead><tr><th>Owner</th><th>Plan</th><th>Website</th><th>Leads</th><th>Brought in</th><th>Bonus runs</th></tr></thead>
          <tbody>{users?.map((u) => (
            <tr key={u.id}>
              <td>{u.team ? <b>{u.team.name || "Team member"}</b>
                : <Link to={`/admin/owners/${u.id}`} title="Open this owner: plan, extras, setup and connections"><b>{u.business || "No business name yet"}</b></Link>}
                {u.disabled && <span className="small muted"> · paused</span>}
                {u.grants?.length > 0 && <span className="small muted"> · {u.grants.length} extra</span>}
                {u.team && <div className="small muted">Team · {u.team.role} at {u.team.of || "another business"}</div>}
                <div className="small muted mono">{localPhone(u.phone)}{u.cohort ? ` · ${u.cohort}` : ""}{u.referred ? " · referred" : ""}</div>
                {u.role !== "admin" && <button className="btn sm ghost" style={{ marginTop: 6 }} onClick={async () => {
                  if (!u.disabled && !window.confirm(`Pause ${u.business || u.phone}? They are logged out and their website goes offline until you resume.`)) return;
                  await api(`/admin/users/${u.id}`, { method: "PATCH", body: { disabled: !u.disabled } }); reloadUsers(); toast(u.disabled ? "Resumed" : "Paused");
                }}>{u.disabled ? "Resume" : "Pause"}</button>}</td>
              <td data-l="Plan">{u.team ? <span className="small muted">Uses the owner's plan</span> : <div><select className="select" aria-label="Plan" value={u.plan} onChange={async (e) => { await api(`/admin/users/${u.id}`, { method: "PATCH", body: { plan: e.target.value } }); reloadUsers(); toast("Plan updated"); }}>
                {TIERS.map((t) => <option key={t} value={t}>{me?.tiers?.[t]?.name || t}</option>)}</select>
                {u.effective_plan !== u.plan && <div className="small muted">{SOURCE_LABEL[u.plan_source] || u.plan_source}: {me?.tiers?.[u.effective_plan]?.name || u.effective_plan}</div>}</div>}</td>
              <td data-l="Website">{u.slug ? <a href={`/s/${u.slug}`} target="_blank" rel="noopener">{u.site}</a> : "—"}</td>
              <td data-l="Leads">{u.leads}</td><td data-l="Brought in">{u.brought_in}</td>
              <td data-l="Bonus runs"><div className="row"><span>{u.bonus_runs}</span><button className="btn sm ghost" onClick={async () => { await api(`/admin/users/${u.id}`, { method: "PATCH", body: { add_bonus_runs: 10 } }); reloadUsers(); }}>+10</button></div></td>
            </tr>))}
          </tbody>
        </table>
      </div>
      <AdminDomains />
    </Sheet>
  );
}
