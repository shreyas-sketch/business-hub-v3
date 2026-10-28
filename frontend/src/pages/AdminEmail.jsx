import React, { useState } from "react";
import { Link } from "react-router-dom";
import { api, day } from "../api.js";
import { Busy, FormError, LoadError, Sheet, useHub, useLoad } from "../ui.jsx";
import { Empty, Loading, Pill, Tabs } from "../v2.jsx";

/* Admin → Email: is the sending domain set up, which owners bounce or get complaints, and a switch to pause one owner's
   automatic emails (follow-ups, proposals, invoices). A check that wasn't run shows as "not checked" — never as passed. */

function Check({ label, value, note }) {
  const state = value === true ? ["on", "Passes"] : value === false ? ["warn", "Fails"] : ["off", "Not checked"];
  return (
    <div className="row between" style={{ padding: "10px 0", borderBottom: "1px solid var(--ink-line)" }}>
      <span>{label}{note && <span className="small muted"> · {note}</span>}</span>
      <Pill kind={state[0]}>{state[1]}</Pill>
    </div>
  );
}

const pct = (n, digits = 1) => `${Number(n || 0).toFixed(digits)}%`;

export default function AdminEmail() {
  const { toast } = useHub();
  const [days, setDays] = useState("30");
  const [d, reload, err] = useLoad(`/admin/email?days=${days}`);
  const pause = async (id, paused, who) => {
    if (paused && !window.confirm(`Pause automatic emails for ${who || "this owner"}? Their follow-ups, proposals and invoice emails stop until you resume them.`)) return;
    await api(`/admin/owners/${id}/email-pause`, { method: "POST", body: { paused } });
    toast(paused ? "Paused" : "Resumed");
    reload();
  };
  const h = d?.health;
  return (
    <Sheet kicker="Admin · Email" id="AD-09" title={<>Customer email <em>health</em>.</>}
      sub="Follow-ups, proposals and invoices go out by email from the hub's sending domain. Keep bounces and complaints low, or the provider stops sending for everyone."
      actions={<Link className="btn sm ghost" to="/admin">Back to admin</Link>}>
      {err ? <LoadError err={err} /> : !d ? <Loading /> : (
        <>
          <div className="panel">
            <span className="label">Sending domain</span>
            <div className="stack" style={{ gap: 0 }}>
              <Check label="Email provider" value={h.configured ? true : false} note={h.configured ? h.provider : `${h.provider} — nothing is sent to customers`} />
              <div className="row between" style={{ padding: "10px 0", borderBottom: "1px solid var(--ink-line)" }}>
                <span>Sending domain</span><span className="mono small">{h.domain || "not set"}</span>
              </div>
              <div className="row between" style={{ padding: "10px 0", borderBottom: "1px solid var(--ink-line)" }}>
                <span>Inbound (reply) domain</span><span className="mono small">{h.inbound_domain || "not set"}</span>
              </div>
              <Check label="SPF" value={h.spf} />
              <Check label="DKIM" value={h.dkim} note={h.dkim === null ? "checked with the provider once it's connected" : ""} />
              <Check label="DMARC" value={h.dmarc} />
              <Check label="Domain verified with the provider" value={h.verified} />
            </div>
            {h.error && <FormError msg={`The provider check failed: ${h.error}`} />}
          </div>

          <div className="row between">
            <span className="small muted">Warning above {d.thresholds.bounce_rate}% bounces or {d.thresholds.complaint_rate}% complaints.</span>
            <Tabs tabs={[["7", "7 days"], ["30", "30 days"], ["90", "90 days"]]} value={days} onChange={setDays} />
          </div>

          {!d.owners.length ? <Empty>No customer emails sent in the last {d.days} days.</Empty> : (
            <table className="t cards">
              <thead><tr><th>Owner</th><th>Sent</th><th>Bounced</th><th>Complaints</th><th>Failed</th><th /></tr></thead>
              <tbody>{d.owners.map((o) => (
                <tr key={o.owner_id}>
                  <td>
                    <Link to={`/admin/owners/${o.owner_id}`}><b>{o.business || o.name || "Owner"}</b></Link>
                    {o.business && o.name && <div className="small muted">{o.name}</div>}
                    <div className="row" style={{ gap: 6, marginTop: 6 }}>
                      {o.warning && <Pill kind="warn">Check this owner</Pill>}
                      {o.paused && <Pill kind="off">Paused</Pill>}
                    </div>
                  </td>
                  <td data-l="Sent">{o.sent}</td>
                  <td data-l="Bounced">{o.bounced} <span className="small muted">({pct(o.bounce_rate)})</span></td>
                  <td data-l="Complaints">{o.complained} <span className="small muted">({pct(o.complaint_rate, 2)})</span></td>
                  <td data-l="Failed">{o.failed}</td>
                  <td>
                    <Busy className={`btn sm ${o.paused ? "" : "ghost"}`} run={() => pause(o.owner_id, !o.paused, o.business || o.name)}>
                      {o.paused ? "Resume emails" : "Pause emails"}
                    </Busy>
                  </td>
                </tr>
              ))}</tbody>
            </table>
          )}

          {d.paused.length > 0 && (
            <div className="panel flat">
              <span className="label">Paused owners</span>
              {d.paused.map((p) => (
                <div key={p.owner_id} className="row between" style={{ padding: "8px 0", borderBottom: "1px solid var(--ink-line)" }}>
                  <span>{p.business || p.name || "Owner"}{p.at && <span className="small muted"> · since {day(p.at)}</span>}</span>
                  <Busy className="btn sm" run={() => pause(p.owner_id, false)}>Resume emails</Busy>
                </div>
              ))}
            </div>
          )}
        </>
      )}
    </Sheet>
  );
}
