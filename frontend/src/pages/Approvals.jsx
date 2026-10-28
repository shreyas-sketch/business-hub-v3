import React, { useState } from "react";
import { Link } from "react-router-dom";
import { ago, api, localPhone } from "../api.js";
import { Busy, Sheet, useHub, useLoad } from "../ui.jsx";
import { Loading, Pill, Tabs } from "../v2.jsx";
import "./program.css";

const DONE = { sent: "Sent", skipped: "Skipped", failed: "Not sent", expired: "No longer needed" };
const TEMPLATED = ["review", "reminder"];

/** The text as the server will keep it (follow-ups are kept on one line), so the WhatsApp link matches what's saved. */
const tidy = (a, text) => (["followup", "office"].includes(a.kind) ? text.split(/\s+/).join(" ").trim() : text.trim());
/** The approval's own wa.me link with the (possibly edited) text typed in. */
const linkFor = (a, text) => `${a.own_link.split("?")[0]}?text=${encodeURIComponent(text)}`;

/** One message the hub prepared. Change it, then send it from your own WhatsApp — or skip it. */
function Message({ a, connected, onDone }) {
  const { refresh, toast } = useHub();
  const [text, setText] = useState(a.text);
  const clean = tidy(a, text);
  const changed = clean !== a.text;
  const okText = clean.length >= 2;
  const save = async () => { if (changed) await api(`/approvals/${a.id}`, { method: "PATCH", body: { text: clean } }); };
  const finish = (msg) => { if (msg) toast(msg); onDone(); refresh(); };
  const guard = async (fn) => { try { await fn(); } catch (e) { if (e.status === 409) finish(); throw e; } };
  const who = a.to_name || "the customer";

  // The link opens in the click itself (no pop-up blocker). Opening WhatsApp isn't sending: the hub asks, and only marks it
  // sent once the owner says it went.
  const [asking, setAsking] = useState(!!a.opened_at);
  const openedOwn = async () => {
    setAsking(true);
    try {
      await save();
      await api(`/approvals/${a.id}/opened`, { method: "POST" });
    } catch (e) {
      if (e.status === 409) finish();
      if (!e.handled) toast(e.message, "err");
    }
  };

  return (
    <div className="panel pg-send">
      <div className="row between" style={{ alignItems: "flex-start" }}>
        <div className="stack" style={{ gap: 4 }}>
          <b>{a.title}</b>
          <span className="meta small muted">
            <span>To {a.to_name || "customer"}</span>
            {a.to_phone && <span className="mono">{localPhone(a.to_phone)}</span>}
            <span>{a.agent_name} · {ago(a.created_at)}</span>
          </span>
        </div>
        {a.money && <Pill kind="money">Money — always waits for you</Pill>}
      </div>
      {a.send_error && <div className="notice small">{a.send_error}</div>}
      {asking && (
        <div className="row notice small">
          <span>Did the message go to {who} on WhatsApp?</span>
          <Busy className="btn sm primary" run={() => guard(async () => {
            await api(`/approvals/${a.id}/own`, { method: "POST" });
            finish(`Marked as sent to ${who}`);
          })}>Yes, it's sent</Busy>
          <button className="btn sm ghost" onClick={() => setAsking(false)}>Not yet</button>
        </div>
      )}
      <textarea className="textarea" aria-label={`Message to ${who}`} maxLength={900} value={text} onChange={(e) => setText(e.target.value)} />
      {connected && TEMPLATED.includes(a.kind) && (
        <span className="hint">Sent from your number, this goes as your approved WhatsApp template with these details. Your own edits go out when you tap Send on WhatsApp.</span>
      )}
      <div className="row">
        {okText
          ? <a className={`btn ${asking ? "" : "primary"}`} href={linkFor(a, clean)} target="_blank" rel="noopener" onClick={openedOwn}>{asking ? "Open WhatsApp again" : "Send on WhatsApp"}</a>
          : <button className="btn primary" disabled>Send on WhatsApp</button>}
        {connected && (
          <Busy className="btn" disabled={!okText} run={() => guard(async () => {
            await save();
            await api(`/approvals/${a.id}/send`, { method: "POST" });
            finish(`Sent to ${who} from your number`);
          })}>Send from my number</Busy>
        )}
        <Busy className="btn ghost" run={() => guard(async () => {
          await api(`/approvals/${a.id}/skip`, { method: "POST" });
          finish("Skipped");
        })}>Skip</Busy>
      </div>
    </div>
  );
}

function Waiting({ connected }) {
  const { me } = useHub();
  const [list, reload] = useLoad("/approvals?status=pending");
  if (!list) return <Loading />;
  if (!list.length) {
    return (
      <div className="empty">
        <h2>Nothing is waiting.</h2>
        <p className="muted">When the hub prepares a message for a customer — a follow-up, a review request, a payment reminder — it waits here until you send it.</p>
        {me.features?.automations && <Link className="btn" to="/agents">See your {(me.feature_info?.automations?.label || "automations").toLowerCase()}</Link>}
      </div>
    );
  }
  return <div className="stack" style={{ gap: 16 }}>{list.map((a) => <Message key={a.id} a={a} connected={connected} onDone={reload} />)}</div>;
}

function Done({ show }) {
  const [list] = useLoad("/approvals?status=done");
  if (!list) return <Loading />;
  const items = list.filter((a) => (show === "sent" ? a.status === "sent" : a.status !== "sent"));
  if (!items.length) {
    return <div className="empty"><p className="muted">{show === "sent" ? "Messages you send are listed here." : "Messages you skip, or that were no longer needed, are listed here."}</p></div>;
  }
  return (
    <div className="timeline">
      {items.map((a) => (
        <div key={a.id} className={a.status === "sent" ? "done" : a.status === "failed" ? "fail" : ""}>
          <div className="row between">
            <b>{a.title}</b>
            <span className={`token ${a.status === "sent" ? "" : "grey"}`}>
              {DONE[a.status] || a.status}{a.status === "sent" ? (a.via === "own" ? " · your WhatsApp" : a.decided_by === "agent" ? " · on its own" : " · your number") : ""}
            </span>
          </div>
          <span className="small muted">To {a.to_name || "customer"} · {a.agent_name} · {ago(a.decided_at || a.created_at)}{a.note ? ` · ${a.note}` : ""}</span>
          <span className="small" style={{ whiteSpace: "pre-wrap", overflowWrap: "anywhere" }}>{a.text}</span>
        </div>
      ))}
    </div>
  );
}

export default function Approvals() {
  const { me } = useHub();
  const [tab, setTab] = useState("waiting");
  const [status] = useLoad("/approvals/status");
  const n = me.approvals_waiting || 0;
  const connected = !!status?.connected;
  return (
    <Sheet kicker="Start · Send list" id="AP-01" pillar="Systems"
      title={n ? <>{n} {n === 1 ? "message is" : "messages are"} ready to <em>send</em>.</> : <>Nothing goes out until you tap <em>Send</em>.</>}
      sub="Messages the hub prepared for your customers. Nothing goes out until you tap Send. Read each one, change anything, then send it from your own WhatsApp.">
      {status && !connected && status.plan_has_number && (
        <div className="notice small"><span>Connect your WhatsApp number to send from it without opening WhatsApp.</span><Link className="btn sm" to="/connections">Connect</Link></div>
      )}
      <Tabs tabs={[["waiting", `Waiting${n ? ` (${n})` : ""}`], ["sent", "Sent"], ["skipped", "Skipped"]]} value={tab} onChange={setTab} />
      {tab === "waiting" ? <Waiting connected={connected} /> : <Done key={tab} show={tab} />}
    </Sheet>
  );
}
