import React from "react";
import { Link } from "react-router-dom";
import { ago, api } from "../api.js";
import { Busy, Sheet, Stat, useHub, useLoad } from "../ui.jsx";
import { Empty, Loading, Pill } from "../v2.jsx";
import { ConnectionForm, EmployzImport, Webhook } from "./Connections.jsx";
import "./running.css";

const STAGE = { identification: "1 · Identification", logic: "2 · Logic", pain: "3 · Pain", vision: "4 · Vision", close: "5 · Close" };
const STATUS = { new: "New", contacted: "Contacted", won: "Won", lost: "Lost" };
const HOW = "Every inquiry from your website goes into your Employz.ai pipeline, and moves when you move it in the hub.";

function Recent({ rows }) {
  if (!rows.length) return <Empty>No inquiries synced yet. The next inquiry from your website goes straight into Employz.ai.</Empty>;
  return (
    <table className="t cards">
      <thead><tr><th>Inquiry</th><th>Stage</th><th>Status</th><th>Synced</th></tr></thead>
      <tbody>{rows.map((r, i) => (
        <tr key={i}>
          <td>
            <b>{r.name || "Inquiry"}</b>
            {r.error && <div className="small" style={{ color: "var(--grey)" }}>Didn't sync: {r.error}</div>}
          </td>
          <td data-l="Stage" className="small">{STAGE[r.stage] || r.stage}</td>
          <td data-l="Status">{r.error ? <Pill kind="warn">Not synced</Pill> : <Pill kind={r.status === "won" ? "on" : ""}>{STATUS[r.status] || r.status || "New"}</Pill>}</td>
          <td data-l="Synced" className="small muted">{r.synced_at ? ago(r.synced_at) : "—"}</td>
        </tr>
      ))}</tbody>
    </table>
  );
}

export default function Crm() {
  const { me, toast } = useHub();
  const [d, reload] = useLoad("/crm");
  const sheet = (title, children, actions) => (
    <Sheet kicker="Systems · Employz.ai CRM" id="RB-03" pillar="Systems" title={title} sub={HOW} actions={actions}>{children}</Sheet>
  );
  if (!d) return sheet(<>Your pipeline in <em>Employz.ai</em>.</>, <Loading />);
  const more = me.features?.connections && <Link to="/connections">See all your connections</Link>;

  if (!d.ready) {
    return sheet(<>Connect your <em>CRM</em>.</>, (
      <>
        <div className="panel active">
          <span className="label">How it gets connected</span>
          <p>Our team connects your Employz.ai account for you: the CRM, the Football Field pipeline with its 5 stages, your team's logins and the calendar.
            Once it's connected, every inquiry is added there on its own.</p>
          <p className="small muted">Already have the details? Paste them below. It connects once the sub-account id and the private integration token are both saved.{more ? <> {more}.</> : null}</p>
        </div>
        <div className="panel">
          <div className="row between"><h3>Employz.ai details</h3><Pill kind={d.connection.status ? "" : "off"}>{d.connection.status ? "Not finished" : "Not set up"}</Pill></div>
          <ConnectionForm key={JSON.stringify(d.connection.fields)} view={d.connection} onSaved={reload} />
          <p className="small muted">Keys and tokens are stored encrypted and never shown again.</p>
        </div>
      </>
    ));
  }

  const notSynced = Math.max(0, d.total - d.synced - d.failed);
  return sheet(<>Your pipeline in <em>Employz.ai</em>.</>, (
    <>
      <div className="statgrid">
        <Stat label="In Employz.ai" value={d.synced} note={`of ${d.total} ${d.total === 1 ? "inquiry" : "inquiries"}`} />
        <Stat label="Not synced yet" value={notSynced} note={notSynced ? "Sync now sends them" : "None waiting"} />
        <Stat label="Didn't sync" value={d.failed} note={d.failed ? "Sync now tries again" : "No problems"} />
      </div>
      <div className="row between">
        <span className="label">Latest synced inquiries</span>
        <Busy className="btn sm" run={async () => {
          const r = await api("/crm/sync", { method: "POST" });
          toast(r.tried ? `Synced ${r.synced} of ${r.tried}` : "Everything is already in Employz.ai");
          reload();
        }}>Sync now</Busy>
      </div>
      <Recent rows={d.recent || []} />
      <EmployzImport />
      <details className="panel flat">
        <summary className="label" style={{ cursor: "pointer" }}>Connection details</summary>
        <div className="stack" style={{ marginTop: 14 }}>
          <ConnectionForm key={JSON.stringify(d.connection.fields)} view={d.connection} onSaved={reload} />
          <Webhook kind="employz" url={d.connection.webhook} />
          {more && <p className="small muted">{more}.</p>}
        </div>
      </details>
    </>
  ), d.open_link ? <a className="btn primary" href={d.open_link} target="_blank" rel="noreferrer">Open Employz.ai</a> : null);
}
