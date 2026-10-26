import React, { useEffect, useState } from "react";
import { api, waLink } from "../api.js";
import { Busy, Copy, FormError, Modal } from "../ui.jsx";
import "./templates.css";

/**
 * "Use a template": 20 ready WhatsApp messages (no AI token), filled with the customer's name and your business's details.
 * Pick one, change any word, then send it from your own WhatsApp.
 *   onUse(text)          put the message into a reply box on this page instead of opening WhatsApp (Chats)
 *   onSend(id, text)     record the send first; returns { link } to open (Inquiries)
 *   otherwise            opens WhatsApp to `phone` with the message typed
 */
export default function TemplatePicker({ name = "", phone = "", onUse, onSend, className = "btn sm ghost", label = "Use a template" }) {
  const [open, setOpen] = useState(false);
  return (
    <>
      <button type="button" className={className} onClick={() => setOpen(true)}>{label}</button>
      {open && <Picker name={name} phone={phone} onUse={onUse} onSend={onSend} onClose={() => setOpen(false)} />}
    </>
  );
}

function Picker({ name, phone, onUse, onSend, onClose }) {
  const [data, setData] = useState(null);
  const [err, setErr] = useState("");
  const [group, setGroup] = useState("");
  const [pick, setPick] = useState(null);
  const [text, setText] = useState("");
  useEffect(() => {
    api(`/wa-templates?name=${encodeURIComponent(name || "")}`)
      .then((d) => { setData(d); setGroup(d.groups[0]); })
      .catch((e) => setErr(e.message));
  }, [name]);
  const choose = (t) => { setPick(t); setText(t.text); };
  const shown = (data?.templates || []).filter((t) => t.group === group);
  const send = async () => {
    const w = window.open("", "_blank");
    const link = onSend ? (await onSend(pick.id, text)).link : waLink(text, phone);
    if (w) w.location = link; else window.location.href = link;
    onClose();
  };
  return (
    <Modal onClose={onClose}>
      <div className="row between">
        <span className="kicker">{pick ? pick.title : `Ready messages${name ? ` for ${name}` : ""}`}</span>
        <button type="button" className="btn sm ghost" onClick={pick ? () => setPick(null) : onClose}>{pick ? "Back" : "Close"}</button>
      </div>
      <FormError msg={err} />
      {!data && !err && <p className="small muted">Loading…</p>}
      {data && !pick && (
        <>
          <div className="tpl-groups" role="tablist" aria-label="Kind of message">
            {data.groups.map((g) => <button key={g} type="button" role="tab" aria-selected={g === group} className={`tpl-group ${g === group ? "on" : ""}`} onClick={() => setGroup(g)}>{g}</button>)}
          </div>
          <div className="tpl-list">
            {shown.map((t) => (
              <button key={t.id} type="button" className="tpl-item" onClick={() => choose(t)}>
                <b>{t.title}</b><span>{t.text}</span>
              </button>
            ))}
          </div>
          <p className="small muted">No AI token. Pick one, change anything you like, then send.</p>
        </>
      )}
      {pick && (
        <>
          <textarea className="textarea" style={{ minHeight: 160 }} maxLength={3000} aria-label="Message" value={text} onChange={(e) => setText(e.target.value)} />
          <div className="row">
            {onUse
              ? <button type="button" className="btn primary" disabled={text.trim().length < 2} onClick={() => { onUse(text, pick.id); onClose(); }}>Use this message</button>
              : <Busy className="btn primary" disabled={text.trim().length < 2} onError={setErr} run={send}>Send on WhatsApp</Busy>}
            <Copy text={text} className="btn ghost" />
          </div>
        </>
      )}
    </Modal>
  );
}
