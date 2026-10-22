import React, { useState } from "react";
import { Link } from "react-router-dom";
import { ago, api, localPhone } from "../api.js";
import { Busy, Copy, InviteLine, Locked, Modal, Sheet, useHub, useLoad } from "../ui.jsx";

const STATUS = { new: "New", contacted: "Contacted", won: "Won", lost: "Lost" };

export default function Leads() {
  const { me, refresh } = useHub();
  const [leads, reload] = useLoad("/leads");
  const [draft, setDraft] = useState(null);
  const setStatus = async (id, status) => { await api(`/leads/${id}`, { method: "PATCH", body: { status } }); reload(); };
  const waiting = (leads || []).filter((l) => l.status === "new").length;
  const title = !leads?.length ? <>Your leads will <em>arrive</em> here.</>
    : waiting ? <>{waiting} {waiting === 1 ? "lead" : "leads"} waiting for a <em>reply</em>.</>
    : <>Every lead has a <em>reply</em>.</>;
  return (
    <Sheet kicker="Systems · Leads" id="SY-02" pillar="Systems" step={3} title={title}
      sub="Every inquiry from your website lands here, and you get a WhatsApp alert the moment it comes in.">
      <Locked feature="lead_autoreply" what="Instant WhatsApp reply to every new lead" />
      {leads && !leads.length && (
        <div className="empty">
          <h2>No leads yet.</h2>
          <p className="muted">{me.site?.status === "live" ? "Share your website link where customers already talk to you — WhatsApp Status, groups, your Google listing." : "Put your website live first — the inquiry form brings leads here."}</p>
          <Link className="btn" to="/website">{me.site?.status === "live" ? "Share my website" : "Go to website"}</Link>
        </div>
      )}
      {leads?.length > 0 && (
        <table className="t cards">
          <thead><tr><th>Customer</th><th>Message</th><th>Status</th><th>Received</th><th /></tr></thead>
          <tbody>{leads.map((l) => (
            <tr key={l.id}>
              <td><b>{l.name}</b><span className="small muted show-sm"> · {ago(l.created_at)}</span><div className="small muted mono">{localPhone(l.phone)}</div>{l.auto_reply?.sent && <div className="small" style={{ color: "var(--aqua)" }}>Auto-replied</div>}</td>
              <td className="small" style={{ maxWidth: 360 }}>{l.message || <span className="muted">No message</span>}</td>
              <td><select className="select" style={{ minWidth: 130 }} aria-label="Status" value={l.status} onChange={(e) => setStatus(l.id, e.target.value)}>
                {Object.entries(STATUS).map(([k, v]) => <option key={k} value={k}>{v}</option>)}</select></td>
              <td className="small muted hide-sm">{ago(l.created_at)}</td>
              <td><Busy className="btn sm ai" run={async () => { setDraft({ lead: l, ...(await api(`/leads/${l.id}/reply`, { method: "POST" })) }); refresh(); }}>Draft reply</Busy></td>
            </tr>))}
          </tbody>
        </table>
      )}
      {draft && (
        <Modal onClose={() => setDraft(null)}>
          <span className="kicker">Reply to {draft.lead.name}</span>
          <textarea className="textarea" style={{ minHeight: 200 }} value={draft.reply} onChange={(e) => setDraft({ ...draft, reply: e.target.value })} />
          <div className="row">
            {draft.whatsapp_link && <a className="btn primary" target="_blank" rel="noopener" onClick={() => setStatus(draft.lead.id, "contacted")}
              href={`${draft.whatsapp_link.split("?")[0]}?text=${encodeURIComponent(draft.reply)}`}>Open in WhatsApp</a>}
            <Copy text={draft.reply} />
            <button className="btn ghost" onClick={() => setDraft(null)}>Close</button>
          </div>
          <InviteLine />
        </Modal>
      )}
    </Sheet>
  );
}
