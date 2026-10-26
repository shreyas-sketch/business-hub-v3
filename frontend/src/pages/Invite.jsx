import React, { useEffect, useState } from "react";
import { ago, api, day } from "../api.js";
import { paise } from "../billing.js";
import { Busy, Copy, Field, Gate, Sheet, Stat, useHub, useLoad } from "../ui.jsx";

function statusOf(c) {
  if (c.status === "pending") return c.ready ? ["Waiting for approval", ""] : [`Refund window until ${day(c.available_at)}`, "grey"];
  if (c.status === "approved") return ["Approved · paying soon", ""];
  if (c.status === "paid") return [`Paid ${day(c.paid_at)}`, ""];
  return [c.void_reason === "refunded" ? "Void · payment refunded" : "Void", "grey"];
}

function Payout({ e, reload }) {
  const { toast } = useHub();
  const [form, setForm] = useState({ upi_id: e.payout?.upi_id || "", name: e.payout?.name || "" });
  const [editing, setEditing] = useState(!e.payout);
  useEffect(() => { setForm({ upi_id: e.payout?.upi_id || "", name: e.payout?.name || "" }); setEditing(!e.payout); }, [e.payout]);
  const save = async () => {
    await api("/earnings/payout", { method: "PUT", body: form });
    toast("Payout details saved");
    reload();
  };
  return (
    <div className="panel">
      <span className="label">Where we pay you</span>
      {!editing ? (
        <div className="row between">
          <div className="stack" style={{ gap: 2 }}><span className="mono">{e.payout.upi_id}</span><span className="small muted">{e.payout.name}</span></div>
          <button className="btn sm ghost" onClick={() => setEditing(true)}>Change</button>
        </div>
      ) : (
        <>
          <div className="grid2 form-row">
            <Field label="UPI ID" hint="For example yourname@okhdfcbank">
              <input className="input mono" autoComplete="off" spellCheck="false" value={form.upi_id} placeholder="yourname@okhdfcbank"
                onChange={(ev) => setForm({ ...form, upi_id: ev.target.value.trim() })} />
            </Field>
            <Field label="Name on the account">
              <input className="input" value={form.name} placeholder="As your bank shows it" onChange={(ev) => setForm({ ...form, name: ev.target.value })} />
            </Field>
          </div>
          <div className="row">
            <Busy className="btn primary" run={save} disabled={!form.upi_id || form.name.trim().length < 2}>Save payout details</Busy>
            {e.payout && <button className="btn ghost" onClick={() => setEditing(false)}>Cancel</button>}
          </div>
        </>
      )}
    </div>
  );
}

function Earnings({ e, reload }) {
  if (!e) return null;
  const { percent, months, hold_days: hold } = e.rules;
  return (
    <>
      <div className="stack">
        <span className="label">Your earnings</span>
        <div className="statgrid">
          <Stat label="In refund window or waiting" value={paise(e.totals.pending)} money note={`Held ${hold} days, then approved`} />
          <Stat label="Approved" value={paise(e.totals.approved)} money note="Paying to your UPI soon" />
          <Stat label="Paid to you" value={paise(e.totals.paid)} money note="All time" />
        </div>
      </div>
      <div className="grid3">
        <div className="panel"><span className="label">1 · They pay</span><p className="small">You earn {percent}% of every payment an owner you invited makes in their first {months} months: Membership and every program.</p></div>
        <div className="panel"><span className="label">2 · Refund window</span><p className="small">Each commission waits {hold} days in case the payment is refunded. A refunded payment earns nothing.</p></div>
        <div className="panel"><span className="label">3 · Paid to your UPI</span><p className="small">Once the Business AI team approves it, we send it to your UPI ID and you get a note here with the reference.</p></div>
      </div>
      {!e.payout && e.commissions.length > 0 && (
        <div className="notice"><span>Add your UPI ID below so we can pay your earnings.</span></div>
      )}
      <Payout e={e} reload={reload} />
      <div className="stack">
        <span className="label">Commissions</span>
        {e.commissions.length ? (
          <table className="t cards">
            <thead><tr><th>Business</th><th>Their payment</th><th>You earn</th><th>Status</th></tr></thead>
            <tbody>{e.commissions.map((c) => {
              const [label, tone] = statusOf(c);
              return (
                <tr key={c.id}>
                  <td><b>{c.friend}</b><div className="small muted">{c.tier_name} · {day(c.created_at)}</div></td>
                  <td data-l="Their payment">{c.payment_amount ? <span className="money">{paise(c.payment_amount)}</span> : "—"}</td>
                  <td data-l={`You earn (${c.percent}%)`}><span className="money">{paise(c.amount)}</span></td>
                  <td data-l="Status"><span className={`token ${tone}`}>{label}</span>
                    {c.status === "paid" && c.reference && <div className="small muted mono">Ref {c.reference}</div>}</td>
                </tr>
              );
            })}</tbody>
          </table>
        ) : <div className="empty"><p className="muted">No commissions yet. When an owner you invited pays for Membership or a program, your {percent}% shows here.</p></div>}
      </div>
    </>
  );
}

function InviteInner() {
  const [r] = useLoad("/referrals");
  const [e, reloadEarnings] = useLoad("/earnings");
  if (!r) return null;
  const { percent, months, hold_days: hold } = e?.rules || { percent: 30, months: 12, hold_days: 7 };
  const msg = encodeURIComponent(`I put my business website live with OwnerDesk — free, took 10 minutes, and inquiries come straight to WhatsApp. Set yours up here: ${r.link}`);
  return (
    <Sheet kicker="Grow · Invite & earn" id="U-02" pillar="Grow" money title={<>Bring owners in. Earn <em>{percent}%</em>.</>}
      sub={`Invite business owners you know. They start with a month of Membership features. You're rewarded when their website goes live, and you earn ${percent}% of what they pay in their first ${months} months, paid after a ${hold}-day refund window once approved.`}>
      <div className="panel active">
        <span className="label">Your invite link</span>
        <div className="row"><input className="input mono" readOnly value={r.link} style={{ flex: 1, minWidth: 220 }} /><Copy text={r.link} /></div>
        <div><a className="btn sm" href={`https://wa.me/?text=${msg}`} target="_blank" rel="noopener">Send on WhatsApp</a></div>
        <p className="small muted">The badge on your website carries this link too.</p>
      </div>
      <div className="grid3">
        <div className="panel"><span className="label">Your friend gets</span><h3>{r.rules.friend_trial_days} days of Membership features</h3></div>
        <div className="panel"><span className="label">First friend live</span><h3>{r.rules.first_reward_days} days of Membership for you</h3></div>
        <div className="panel"><span className="label">Every friend after</span><h3>+{r.rules.bonus_runs} AI tokens</h3></div>
      </div>
      <Earnings e={e} reload={reloadEarnings} />
      <div className="stack">
        <span className="label">Owners you invited</span>
        {r.friends.length ? (
          <table className="t"><thead><tr><th>Business</th><th>Joined</th><th>Website</th></tr></thead>
            <tbody>{r.friends.map((f, i) => <tr key={i}><td>{f.business}</td><td className="small muted">{ago(f.joined_at)}</td>
              <td>{f.live ? <span className="token">Live</span> : <span className="token grey">Setting up</span>}</td></tr>)}</tbody></table>
        ) : <p className="muted">No one yet.</p>}
      </div>
    </Sheet>
  );
}

export default function Invite() {
  return <Gate role="owner" kicker="Grow · Invite & earn" id="U-02"><InviteInner /></Gate>;
}
