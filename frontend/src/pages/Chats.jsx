import React, { useEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { ago, api, localPhone } from "../api.js";
import { Busy, FormError, Sheet, Switch, canAct, useHub, useLoad } from "../ui.jsx";
import { Empty, Loading, Pill } from "../v2.jsx";
import "./growth.css";

const POLL_MS = 20000;
const INTENT = { sales: "Sales", support: "Customer care" };
const LEAD_STATUS = { new: "new", contacted: "contacted", won: "won", lost: "lost" };

const phoneOf = (t) => localPhone("+" + t.phone);
const nameOf = (t) => t.name || phoneOf(t);
const sentence = (s) => (/[.!?…]$/.test(s.trim()) ? s.trim() : `${s.trim()}.`);

/** "3:45 pm" today, "8 Oct, 3:45 pm" on other days. */
function when(iso) {
  if (!iso) return "";
  const d = new Date(iso);
  const time = d.toLocaleTimeString("en-IN", { hour: "numeric", minute: "2-digit" });
  return d.toDateString() === new Date().toDateString() ? time : `${d.toLocaleDateString("en-IN", { day: "numeric", month: "short" })}, ${time}`;
}

function ThreadButton({ t, on, onOpen }) {
  return (
    <button className={on ? "on" : ""} onClick={() => onOpen(t.id)} aria-current={on ? "true" : undefined}>
      <span className="chat-top">
        <span className="chat-name">{nameOf(t)}</span>
        <span className="row" style={{ gap: 6, flexWrap: "nowrap", flex: "none" }}>
          <span className="small muted">{ago(t.last_at)}</span>
          {t.unread > 0 && <span className="chat-unread" aria-label={`${t.unread} unread`}>{t.unread}</span>}
        </span>
      </span>
      {t.last_text && <span className="chat-last">{t.last_dir === "out" ? "↩ " : ""}{t.last_text}</span>}
      <span className="row" style={{ gap: 6 }}>
        {t.needs_person && <Pill kind="on">Needs you</Pill>}
        {!t.needs_person && t.ai_on === false && <Pill kind="off">Your team has it</Pill>}
        {INTENT[t.intent] && <Pill>{INTENT[t.intent]}</Pill>}
      </span>
      {t.needs_person && t.handover_reason && <span className="small">{t.handover_reason}</span>}
    </button>
  );
}

function ThreadList({ threads, open, onOpen }) {
  const waiting = threads.filter((t) => t.needs_person);
  const rest = threads.filter((t) => !t.needs_person);
  return (
    <div className="chat-list">
      {waiting.length > 0 && <span className="label">Needs you · {waiting.length}</span>}
      {waiting.length > 0 && <div className="listbox">{waiting.map((t) => <ThreadButton key={t.id} t={t} on={open === t.id} onOpen={onOpen} />)}</div>}
      {rest.length > 0 && <span className="label">{waiting.length ? "Other chats" : "All chats"}</span>}
      {rest.length > 0 && <div className="listbox">{rest.map((t) => <ThreadButton key={t.id} t={t} on={open === t.id} onOpen={onOpen} />)}</div>}
    </div>
  );
}

function Conversation({ id, tick, connected, aiOnAll, onBack, onChanged }) {
  const { me, toast } = useHub();
  const [data, reload, error] = useLoad(`/chats/${id}`);
  const [text, setText] = useState("");
  const [err, setErr] = useState("");
  const box = useRef(null);
  const seen = useRef(false);

  useEffect(() => { if (tick) reload(); }, [tick]);
  // Opening a chat marks it read on the server; refresh the list once so the unread count clears.
  useEffect(() => { if (data && !seen.current) { seen.current = true; onChanged(); } }, [data]);
  const count = data?.messages?.length || 0;
  useEffect(() => { if (box.current) box.current.scrollTop = box.current.scrollHeight; }, [count]);

  if (error) return <Empty>This chat isn't available any more.</Empty>;
  if (!data) return <Loading />;
  const t = data.thread;
  const patch = async (body, msg) => { await api(`/chats/${id}`, { method: "PATCH", body }); toast(msg); reload(); onChanged(); };

  return (
    <>
      <button className="btn sm ghost chat-back" onClick={onBack}>← All chats</button>
      <div className={`panel ${t.needs_person ? "active" : ""}`}>
        <div className="row between" style={{ alignItems: "flex-start" }}>
          <div className="stack" style={{ gap: 4, minWidth: 0 }}>
            <h3 style={{ overflowWrap: "anywhere" }}>{nameOf(t)}</h3>
            <span className="small muted mono">{phoneOf(t)}</span>
          </div>
          <div className="row" style={{ gap: 8 }}>
            {(t.needs_person || t.ai_on === false) && (
              <Busy className="btn sm primary" run={() => patch({ ai_on: true }, "The AI replies in this chat again")}>Let the AI reply again</Busy>
            )}
            {(t.needs_person || t.ai_on !== false) && (
              <Busy className="btn sm ghost" run={() => patch({ ai_on: false, needs_person: false }, "It's yours — the AI stays quiet in this chat")}>I'll handle this</Busy>
            )}
          </div>
        </div>
        {t.needs_person
          ? <p className="small"><b style={{ color: "var(--aqua)" }}>Needs you:</b> {sentence(t.handover_reason || "the AI handed this chat to a person")} The AI has stopped replying here.</p>
          : t.ai_on === false
            ? <p className="small muted">Your team is handling this chat. The AI stays quiet here.</p>
            : <p className="small muted">{aiOnAll ? "The AI replies here within seconds and hands over when a person is needed." : "AI replies are switched off for all chats, so your team replies."}</p>}
        {t.summary && <p className="small muted">What they want: {t.summary}</p>}
        {data.lead && (
          <p className="small">On your leads list as <b>{data.lead.name}</b> ({LEAD_STATUS[data.lead.status] || data.lead.status}). <Link to="/leads">Open leads</Link></p>
        )}
      </div>

      <div className="bubbles chat-msgs" ref={box}>
        {data.messages.map((m) => (
          <div key={m.id} className={`bubble ${m.dir === "out" ? "out" : ""}`}>
            {m.text}
            <span className="small muted">
              {m.dir === "in" ? "" : m.by === "ai" ? <><b className="ai-tag">AI</b> · </> : m.by === me.user.id ? "You · " : "Your team · "}
              {when(m.at)}
            </span>
          </div>
        ))}
      </div>

      {!connected ? (
        <p className="small muted">You can reply here once your WhatsApp number is connected.</p>
      ) : data.can_reply ? (
        <div className="stack">
          <textarea className="textarea" rows={3} maxLength={2000} value={text} onChange={(e) => setText(e.target.value)}
            aria-label={`Reply to ${nameOf(t)}`} placeholder="Type your reply…" />
          <FormError msg={err} />
          <div className="row">
            <Busy className="btn primary" disabled={!text.trim()} onError={setErr} run={async () => {
              await api(`/chats/${id}/reply`, { method: "POST", body: { text: text.trim() } });
              setText("");
              reload();
              onChanged();
            }}>Send reply</Busy>
            <span className="small muted">Goes from your own WhatsApp number.</span>
          </div>
        </div>
      ) : (
        <div className="panel flat">
          <span className="label">The 24-hour rule</span>
          <p className="small">It's been more than 24 hours since {nameOf(t)} last wrote. WhatsApp only lets a business write first with an approved
            template, so the reply box is closed until they message again.</p>
          <div className="row">
            <Link className="btn sm" to="/approvals">Open the Send list</Link>
            <a className="btn sm ghost" href={`tel:+${t.phone}`}>Call {t.name ? t.name.split(" ")[0] : "them"}</a>
          </div>
        </div>
      )}
    </>
  );
}

function NotConnected({ owner }) {
  return (
    <div className="panel active">
      <span className="label">Your WhatsApp number isn't connected yet</span>
      <p className="small">Our team connects your own WhatsApp Business number (through its WhatsApp tool) as part of your setup. Once it's connected, every
        customer chat shows here, the AI replies within seconds, day and night, and your team can reply from this page.</p>
      {owner ? <div><Link className="btn sm" to="/connections">See connections</Link></div>
        : <p className="small muted">Ask the business owner to finish the connection.</p>}
    </div>
  );
}

export default function Chats() {
  const { me, toast } = useHub();
  const [data, reload] = useLoad("/chats");
  const [open, setOpen] = useState(null);
  const [tick, setTick] = useState(0);
  const split = useRef(null);
  const owner = canAct(me, "owner");

  useEffect(() => {
    const timer = setInterval(() => { if (!document.hidden) { reload(); setTick((n) => n + 1); } }, POLL_MS);
    return () => clearInterval(timer);
  }, [reload]);

  const openThread = (tid) => {
    setOpen(tid);
    // On a phone the list gives way to the chat: bring its top into view, below the sticky top bar.
    if (window.innerWidth <= 900) setTimeout(() => { if (split.current) window.scrollTo({ top: split.current.getBoundingClientRect().top + window.scrollY - 76 }); }, 0);
  };
  const switchAi = async (on) => {
    try {
      await api("/chats-ai", { method: "POST", body: { on } });
      toast(on ? "The AI replies to your chats again" : "AI replies are off. Your team replies to every chat.");
      reload();
    } catch (e) { if (!e.handled) toast(e.message, "err"); }
  };

  const n = data?.waiting || 0;
  const title = n ? <>{n} {n === 1 ? "chat needs" : "chats need"} <em>you</em>.</> : <>Every WhatsApp chat in <em>one</em> place.</>;
  const actions = data && (owner
    ? <Switch checked={data.ai_on} onChange={switchAi} label={data.ai_on ? "AI replies on" : "AI replies off"} />
    : <Pill kind={data.ai_on ? "on" : "off"}>{data.ai_on ? "AI replies on" : "AI replies off"}</Pill>);

  return (
    <Sheet kicker="Systems · WhatsApp chats" id="GR-01" pillar="Systems" title={title} actions={actions}
      sub="A shared inbox for your own WhatsApp number. The AI answers customers within seconds and hands a chat to you when a person is needed.">
      {!data ? <Loading /> : (
        <>
          {!data.connected && <NotConnected owner={owner} />}
          {data.connected && !data.ai_on && (
            <p className="small muted">AI replies are switched off for every chat. Customers wait for your team until you switch them back on.</p>
          )}
          {!data.threads.length ? (
            <Empty>{data.connected
              ? "No chats yet. When a customer messages your WhatsApp number, the chat shows here and the AI replies within seconds."
              : "Chats show here once your WhatsApp number is connected."}</Empty>
          ) : (
            <div ref={split} className={`chat-split ${open ? "has-open" : ""}`}>
              <ThreadList threads={data.threads} open={open} onOpen={openThread} />
              <div className="chat-pane">
                {open
                  ? <Conversation key={open} id={open} tick={tick} connected={data.connected} aiOnAll={data.ai_on} onBack={() => setOpen(null)} onChanged={reload} />
                  : <Empty>Pick a chat to read it and reply.</Empty>}
              </div>
            </div>
          )}
        </>
      )}
    </Sheet>
  );
}
