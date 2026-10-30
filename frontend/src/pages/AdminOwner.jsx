import React, { useCallback, useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { ago, api, day, localPhone } from "../api.js";
import { Busy, Copy, Field, FormError, LoadError, Sheet, Stat, useHub, useLoad } from "../ui.jsx";
import { Empty, Loading, Pill } from "../v2.jsx";
import { GrantAdd } from "./AdminFeatures.jsx";
import QuoteStatus from "../components/QuoteStatus.jsx";
import "./admin.css";

const TIERS = ["free", "lite", "program", "running", "growth", "office"];
const rank = (t) => Math.max(TIERS.indexOf(t), 0);
const SOURCE = { purchase: "paid once", subscription: "Membership subscription", trial: "invite trial", welcome: "full hub from signup", admin: "set by the team", hub_admin: "admin number — everything unlocked" };
// WhatsApp tool: the server says which fields each service needs (c.service_fields) and the services' names (c.services).
const OPTION_NAMES = { twilio: "Twilio", sip: "SIP trunk", demo: "Demo (test setup)" };

/* ── Plan ── */
function PlanPanel({ o, tierNames, onSaved }) {
  const { toast } = useHub();
  const [plan, setPlan] = useState(o.plan);
  useEffect(() => setPlan(o.plan), [o.plan]);
  const paid = Object.entries(o.access || {});
  return (
    <div className="panel">
      <span className="label">Plan</span>
      <p className="small muted">The plan set on the account. If the owner paid for a higher plan, has Membership or is on an invite trial, the higher one is used.</p>
      <div className="row">
        <select className="select" style={{ maxWidth: 300 }} aria-label="Plan set on the account" value={plan} onChange={(e) => setPlan(e.target.value)}>
          {TIERS.map((t) => <option key={t} value={t}>{tierNames[t]}</option>)}
        </select>
        <Busy className="btn sm primary" disabled={plan === o.plan} run={async () => {
          await api(`/admin/users/${o.id}`, { method: "PATCH", body: { plan } });
          toast(`Plan set to ${tierNames[plan]}. The owner gets a notice in their hub.`);
          onSaved();
        }}>Set plan</Busy>
      </div>
      <p className="small">In use now: <b>{o.plan_name}</b> <span className="muted">({SOURCE[o.plan_source] || o.plan_source})</span></p>
      <div className="row">
        <span className="small">Full hub from signup: <b>{o.welcome ? "on" : "off"}</b></span>
        <Busy className="btn sm" run={async () => {
          await api(`/admin/users/${o.id}`, { method: "PATCH", body: { welcome: !o.welcome } });
          toast(o.welcome ? "Full hub switched off. The owner keeps what their plan gives them." : "Full hub switched on for this owner.");
          onSaved();
        }}>{o.welcome ? "Switch off" : "Switch on"}</Busy>
      </div>
      {paid.length > 0 && (
        <div className="stack" style={{ gap: 4 }}>
          {paid.map(([t, until]) => (
            <span key={t} className="small muted">{tierNames[t] || t}: paid, {new Date(until) > new Date() ? `access until ${day(until)}` : `ended ${day(until)}`}</span>
          ))}
        </div>
      )}
    </div>
  );
}

/* ── Extras given to this owner ── */
function OwnerGrants({ o, tierNames, onChange }) {
  const { toast } = useHub();
  const granted = new Set(o.grants.map((g) => g.feature));
  const options = o.features.filter((f) => f.tier !== "free" && (!f.has || granted.has(f.key)));
  return (
    <div className="stack">
      <span className="label">Extra features</span>
      {o.grants.length ? (
        <table className="t cards">
          <thead><tr><th>Feature</th><th>Until</th><th>Status</th><th /></tr></thead>
          <tbody>{o.grants.map((g) => (
            <tr key={g.feature}>
              <td><b>{g.label}</b></td>
              <td data-l="Until">{day(g.until)}</td>
              <td data-l="Status">{g.active ? <Pill kind="on">Active</Pill> : <Pill kind="off">Ended</Pill>}</td>
              <td>
                <Busy className="btn sm ghost" run={async () => {
                  if (!window.confirm(`Take ${g.label} back?`)) return;
                  await api(`/admin/config/grants/${o.id}/${g.feature}`, { method: "DELETE" });
                  toast("Taken back.");
                  onChange();
                }}>{g.active ? "Take back" : "Remove"}</Busy>
              </td>
            </tr>
          ))}</tbody>
        </table>
      ) : <p className="small muted">No extra features. Give one below to let this owner try it without changing their plan.</p>}
      <GrantAdd userId={o.id} features={options} tierNames={tierNames} onDone={onChange} />
    </div>
  );
}

/* ── The 6-month setup ── */
function Step({ uid, st, onSaved }) {
  const { toast } = useHub();
  const [note, setNote] = useState(st.note || "");
  const [done, setDone] = useState(st.done);
  const [busy, setBusy] = useState(false);
  useEffect(() => setNote(st.note || ""), [st.note]);
  useEffect(() => setDone(st.done), [st.done]);
  const save = async (next) => {
    setBusy(true);
    setDone(next);
    try {
      await api(`/admin/owners/${uid}/setup/${st.key}`, { method: "PUT", body: { done: next, note: note.trim() } });
      toast(next && !st.done ? "Marked done. The owner gets a notice." : "Saved.");
      await onSaved();
    } catch (e) {
      setDone(st.done);
      if (!e.handled) toast(e.message, "err");
    } finally {
      setBusy(false);
    }
  };
  return (
    <div className={`ad-step ${done ? "done" : ""}`}>
      <input type="checkbox" checked={done} disabled={busy} aria-label={`${st.label}: done`} onChange={(e) => save(e.target.checked)} />
      <div className="step-body">
        <span>{st.label}</span>
        {st.done && st.done_at && <span className="small muted">Done {day(st.done_at)}</span>}
        <div className="step-note">
          <input className="input" maxLength={300} aria-label={`Note for ${st.label}`} placeholder="Note for the owner (optional)" value={note} onChange={(e) => setNote(e.target.value)} />
          {note.trim() !== (st.note || "") && <button className="btn sm" disabled={busy} onClick={() => save(done)}>Save note</button>}
        </div>
      </div>
    </div>
  );
}

function SetupSteps({ o }) {
  const [s, reload, error] = useLoad(`/admin/owners/${o.id}/setup`, [o.id]);
  if (error) return <LoadError err={error} />;
  if (!s) return <Loading />;
  let month = 0;
  return (
    <div className="stack">
      <div className="row between">
        <span className="label">6-month setup</span>
        <span className="small muted">{s.done} of {s.total} done{s.started_at ? ` · started ${day(s.started_at)}` : ""}</span>
      </div>
      <div className="meter"><i style={{ width: `${s.total ? (s.done / s.total) * 100 : 0}%` }} /></div>
      {rank(o.effective_plan) < rank("growth") && (
        <p className="small muted">The setup is part of Growth Mentorship; this owner is on {o.plan_name}. Ticks are kept and show on their Your setup page once they move up.</p>
      )}
      <div className="steps">
        {s.steps.map((st) => {
          const head = st.month !== month ? <div className="steps-month">Month {st.month}</div> : null;
          month = st.month;
          return <React.Fragment key={st.key}>{head}<Step uid={o.id} st={st} onSaved={reload} /></React.Fragment>;
        })}
      </div>
    </div>
  );
}

/* ── Connections, set up by the team ── */
function ConnForm({ uid, c, onSaved }) {
  const { toast } = useHub();
  const init = useCallback(() => Object.fromEntries(c.fields.map((f) => [f.key, f.type === "secret" ? "" : String(f.value ?? "")])), [c]);
  const [v, setV] = useState(init);
  const [err, setErr] = useState("");
  const [startOpen] = useState(!c.connected && c.available); // open the ones still to set up on this plan; then the admin decides
  useEffect(() => { setV(init()); setErr(""); }, [init]);
  const allowed = c.service_fields?.[v.provider || ""];
  const hidden = (f) => c.kind === "whatsapp" && (allowed ? !allowed.includes(f.key) : f.key !== "provider");
  const names = { ...OPTION_NAMES, ...(c.services || {}) };
  const [kind, text] = !c.connected ? ["off", "Not set up"] : c.status === "ok" ? ["on", "Working"] : c.status === "error" ? ["warn", "Test failed"] : ["on", "Saved · not tested"];
  return (
    <details className="panel conn" open={startOpen}>
      <summary>
        <h3>{c.label}</h3>
        <span className="row" style={{ gap: 6 }}>
          <Pill kind={kind}>{text}</Pill>
          {!c.available && <Pill kind="warn">Needs {c.tier_name}</Pill>}
        </span>
      </summary>
      {!c.available && <p className="small muted">This owner's plan doesn't include it yet. You can fill it in now; it starts working when they move to {c.tier_name}.</p>}
      {(c.note || c.updated_by) && (
        <p className="small muted">
          {c.note ? <>Last test{c.checked_at ? ` ${ago(c.checked_at)}` : ""}: {c.note}. </> : null}
          {c.updated_by ? `Last saved by ${c.updated_by === "you" ? "the owner" : c.updated_by}.` : ""}
        </p>
      )}
      <div className="conn-fields">
        {c.fields.filter((f) => !hidden(f)).map((f) => {
          const set = (e) => setV({ ...v, [f.key]: e.target.value });
          const hint = [f.type === "secret" && f.set ? "Saved. Leave empty to keep it" : "", f.help].filter(Boolean).join(" · ");
          return (
            <Field key={f.key} label={f.label} hint={hint || undefined}>
              {f.type === "select" ? (
                <select className="select" value={v[f.key]} onChange={set}>
                  <option value="">Choose…</option>
                  {[...new Set([...(f.options || []), ...(v[f.key] ? [v[f.key]] : [])])].map((o) => <option key={o} value={o}>{names[o] || o}</option>)}
                </select>
              ) : f.type === "secret" ? (
                <input className="input mono" type="password" autoComplete="new-password" maxLength={2000} value={v[f.key]} onChange={set}
                  placeholder={f.set ? "••••••••  saved" : "Not set"} />
              ) : (
                <input className="input" autoComplete="off" maxLength={2000} inputMode={f.type === "number" ? "numeric" : undefined} value={v[f.key]} onChange={set} />
              )}
            </Field>
          );
        })}
      </div>
      {c.webhook && (
        <Field label="Webhook address" hint="Paste this where the provider asks for a webhook URL, so replies and call results reach the hub.">
          <div className="hook"><input className="input mono" readOnly value={c.webhook} aria-label={`${c.label} webhook address`} /><Copy text={c.webhook} /></div>
        </Field>
      )}
      <FormError msg={err} />
      <div className="row">
        <Busy className="btn sm primary" onError={setErr} run={async () => {
          const values = {};
          for (const f of c.fields) {
            const x = String(v[f.key] ?? "").trim();
            if (f.type !== "secret" || x) values[f.key] = x; // an empty secret keeps the saved one
          }
          await api(`/admin/owners/${uid}/connections/${c.kind}`, { method: "PUT", body: { values } });
          toast(`${c.label}: saved.`);
          await onSaved();
        }}>Save</Busy>
        <Busy className="btn sm" disabled={!c.connected} onError={setErr} run={async () => {
          const r = await api(`/admin/owners/${uid}/connections/${c.kind}/test`, { method: "POST" });
          toast(r.note || (r.ok ? "It works." : "The test didn't go through."), r.ok ? "ok" : "err");
          await onSaved();
        }}>Test it</Busy>
        {!c.connected && <span className="small muted">Save the details first, then test.</span>}
      </div>
    </details>
  );
}

function OwnerConnections({ o }) {
  const [list, reload, error] = useLoad(`/admin/owners/${o.id}/connections`, [o.id]);
  return (
    <div className="stack">
      <span className="label">Connections</span>
      <p className="small muted">The WhatsApp tool, Employz.ai and the AI Telecaller, set up by the team for this owner. Keys and passwords are never shown again after saving.</p>
      {error ? <LoadError err={error} />
        : !list ? <Loading />
        : !list.length ? <Empty>No connections are switched on for anyone. Switch on WhatsApp chats, Employz.ai CRM or AI Telecaller in Features & plans.</Empty>
        : list.map((c) => <ConnForm key={c.kind} uid={o.id} c={c} onSaved={reload} />)}
    </div>
  );
}

/* ── Page ── */
/** The owner's last inquiries and what happened to each one's draft quotation, so a missing one explains itself. */
function OwnerInquiries({ o }) {
  return (
    <div className="panel stack" style={{ gap: 12 }}>
      <span className="label">Inquiries and draft quotations</span>
      {!o.quotations_open && <p className="small" style={{ color: "var(--amber)" }}>No draft quotations for this owner: {o.quotations_why}.</p>}
      {!o.inquiries?.length ? <p className="small muted">No inquiries yet.</p> : o.inquiries.map((x) => (
        <div key={x.id} className="stack" style={{ gap: 4 }}>
          <span className="small"><b>{x.name || "A customer"}</b>{x.phone ? ` · ${localPhone(x.phone)}` : ""} · {ago(x.created_at)}{x.message ? <span className="muted"> · “{x.message}”</span> : null}</span>
          <QuoteStatus leadId={x.id} q={x.quote} canMake={false} link={false} />
        </div>
      ))}
    </div>
  );
}

export default function AdminOwner() {
  const { id } = useParams();
  const { me } = useHub();
  const [o, reload, error] = useLoad(`/admin/owners/${id}`, [id]);
  const tierNames = Object.fromEntries(TIERS.map((t) => [t, me?.tiers?.[t]?.name || t]));
  const back = <Link className="back-link" to="/admin">← All owners</Link>;
  if (error) {
    return <Sheet kicker="Admin · Owner" id="A-04" pillar="Admin" title={<>Owner not <em>found</em>.</>} sub={error.message} actions={back} />;
  }
  if (!o) return <Sheet kicker="Admin · Owner" id="A-04" pillar="Admin" title={<>One <em>owner</em>.</>} actions={back}><Loading /></Sheet>;
  const has = o.features.filter((f) => f.has).length;
  const about = [o.industry, o.city].filter(Boolean).join(" · ");
  return (
    <Sheet kicker="Admin · Owner" id="A-04" pillar="Admin" actions={back}
      title={<>{o.business || "No business name yet"} · <em>{o.plan_name}</em></>}
      sub={`${o.name ? `${o.name} · ` : ""}${localPhone(o.phone)}${about ? ` · ${about}` : ""} · joined ${day(o.created_at)}${o.cohort ? ` · cohort ${o.cohort}` : ""}${o.disabled ? " · paused" : ""}`}>
      <div className="statgrid">
        <Stat label="Plan in use" value={<span style={{ fontSize: 18 }}>{o.plan_name}</span>} note={SOURCE[o.plan_source] || o.plan_source} />
        <Stat label="Features" value={has} note={`of ${o.features.length} switched on`} />
        <Stat label="Team logins" value={o.team} />
        <Stat label="Bonus AI tokens" value={o.bonus_runs} />
        <Stat label="Website" value={<span style={{ fontSize: 18 }}>{o.site?.status || "Not made"}</span>}
          note={o.site?.url ? <a href={o.site.url} target="_blank" rel="noopener noreferrer">{o.site.domain || "Open website"}</a> : undefined} />
      </div>
      <PlanPanel o={o} tierNames={tierNames} onSaved={reload} />
      <OwnerGrants o={o} tierNames={tierNames} onChange={reload} />
      <OwnerInquiries o={o} />
      <SetupSteps o={o} />
      <OwnerConnections o={o} />
    </Sheet>
  );
}
