import React, { useEffect, useMemo, useState } from "react";
import { Link, useLocation, useNavigate } from "react-router-dom";
import { ago, api } from "../api.js";
import { Busy, Copy, Field, FormError, Sheet, Switch, canAct, useHub, useLoad } from "../ui.jsx";
import { Empty, Loading, Pill, Tabs } from "../v2.jsx";
import "./inquiries.css";

const STATUS = { new: "New", proposal_sent: "Proposal sent", in_pipeline: "In pipeline" };
// iPhone and iPad can't share from WhatsApp into a web app (no Web Share Target on iOS): there, pasting is the main way in
const IOS = typeof navigator !== "undefined" && (/iPad|iPhone|iPod/.test(navigator.userAgent) || (navigator.platform === "MacIntel" && navigator.maxTouchPoints > 1));
const STATUS_KIND = { new: "warn", proposal_sent: "on", in_pipeline: "off" };

/** Settings: the inquiry address, and what goes out on its own. */
function Setup({ s, reload }) {
  const { me, toast } = useHub();
  const owner = canAct(me, "owner");
  const save = async (body, msg) => { await api("/inquiries/settings", { method: "PUT", body }); toast(msg); reload(); };
  if (!s) return null;
  return (
    <div className="panel stack inq-setup">
      <span className="label">Your inquiry email address</span>
      {s.address
        ? <div className="row"><code className="inq-addr">{s.address}</code><Copy text={s.address} /></div>
        : <p className="small muted">Email inquiries aren't set up on this hub yet. Website, lead magnet and WhatsApp inquiries still come in here.</p>}
      {s.address && <p className="small muted">Put it on your website or visiting card, or set your Gmail or Outlook to forward inquiry emails to it. Each email becomes an inquiry here.</p>}
      {owner ? (
        <div className="stack" style={{ gap: 8 }}>
          <Switch checked={s.auto_email} label="Reply to email inquiries automatically with the proposal"
            onChange={(v) => save({ auto_email: v }, v ? "Email inquiries get the proposal automatically" : "Proposals wait for you")} />
          <span className="hint">
            The proposal uses only what's in your Knowledge Hub — a price only if you wrote it there. Replies come to {s.reply_to || "your email (add it in your Knowledge Hub)"}.
            {" "}At most {s.daily_limit} a day, one per customer a day; one with a link always waits for you.{s.email_ready ? "" : " Email isn't set up on this hub yet, so nothing can go out until it is."}
          </span>
          <Switch checked={s.auto_web} label="Also write a proposal for website and lead magnet inquiries"
            onChange={(v) => save({ auto_web: v }, v ? "Website inquiries get a proposal too" : "Website inquiries: no automatic proposal")} />
          <span className="hint">Each proposal uses 1 AI run. WhatsApp proposals always wait for you to tap Send.</span>
          {s.followups?.available && (
            <>
              <Switch checked={s.followups.on} label={`Follow up on every inquiry — day ${s.followups.days.join(", ").replace(/, (\d+)$/, " and $1")}`}
                onChange={async (v) => { await api("/agents/followup", { method: "PATCH", body: { on: v } }); toast(v ? "Follow-ups on" : "Follow-ups off"); reload(); }} />
              <span className="hint">On each of those days a short follow-up is written for you (1 AI run) and waits here for you to tap Send on WhatsApp. It stops as soon as you mark the inquiry won or lost.{s.followups.opt_in && !s.followups.on ? " It's off until you switch it on." : ""}</span>
            </>
          )}
        </div>
      ) : <p className="small muted">The owner chooses what goes out on its own.</p>}
    </div>
  );
}

/** A WhatsApp inquiry pasted or shared into the hub. */
function Paste({ prefill, onDone }) {
  const pick = (t) => (String(t || "").match(/(?:\+?91[\s-]?)?[6-9]\d{4}[\s-]?\d{5}/) || [""])[0];
  const [f, setF] = useState({ name: "", phone: pick(prefill), message: prefill || "" });
  const [err, setErr] = useState("");
  const { toast } = useHub();
  const fromClipboard = async () => {
    try {
      const t = (await navigator.clipboard.readText()).trim();
      if (!t) { setErr("Nothing copied yet. In WhatsApp, press and hold the message, tap Copy, then come back."); return; }
      setF((cur) => ({ ...cur, message: t.slice(0, 3000), phone: cur.phone || pick(t) }));
      setErr("");
    } catch { setErr("Your phone didn't allow pasting from here. Press and hold in the message box below, then tap Paste."); }
  };
  return (
    <div className="panel stack">
      <span className="label">{IOS ? "Paste a WhatsApp inquiry" : "New WhatsApp inquiry"}</span>
      <p className="small muted">{IOS
        ? "In WhatsApp, press and hold the customer's message and tap Copy. Come back here and tap Paste the message. Add their number and the hub writes a proposal you send with one tap."
        : "Paste the customer's message and number. The hub writes a proposal you send from your own WhatsApp with one tap."}</p>
      {navigator.clipboard?.readText && <button type="button" className={`btn ${IOS ? "primary" : "sm"}`} onClick={fromClipboard}>Paste the message</button>}
      <div className="inq-grid">
        <Field label="Customer's name"><input className="input" value={f.name} maxLength={80} onChange={(e) => setF({ ...f, name: e.target.value })} /></Field>
        <Field label="WhatsApp number"><input className="input" inputMode="tel" value={f.phone} maxLength={24} onChange={(e) => setF({ ...f, phone: e.target.value })} /></Field>
      </div>
      <Field label="Their message"><textarea className="input" rows={4} value={f.message} maxLength={3000} onChange={(e) => setF({ ...f, message: e.target.value })} /></Field>
      <FormError msg={err} />
      <div className="row">
        <Busy className="btn primary ai" onError={setErr} disabled={!f.phone || f.message.trim().length < 2} run={async () => {
          await api("/inquiries/whatsapp", { method: "POST", body: f });
          toast("Added. The proposal is being written.");
          setF({ name: "", phone: "", message: "" });
          onDone();
        }}>Add inquiry</Busy>
        <span className="small muted">Uses 1 AI run.</span>
      </div>
    </div>
  );
}

function Proposal({ it, reload }) {
  const { me, toast } = useHub();
  const manager = canAct(me, "manager");
  const p = it.proposal;
  const [open, setOpen] = useState(false);
  if (!p && it.status !== "new") return null;   // already being handled in the pipeline
  const writeOne = manager && <Busy className="btn sm ghost ai" run={async () => { await api(`/inquiries/${it.id}/proposal`, { method: "POST" }); reload(); }}>Write one</Busy>;
  if (!p) return <p className="small muted">No proposal for this one. {writeOne}</p>;
  if (p.status === "skipped") return <p className="small muted">No automatic reply: {p.why} {writeOne}</p>;
  const text = it.email && p.email_text ? p.email_text : p.whatsapp_text;
  return (
    <div className="stack inq-prop">
      <div className="row between">
        <span className="small">
          {p.written_by === "template" ? "A short reply asking what they need" : p.matched ? `Proposal: ${(p.offers || []).join(", ")}` : "Questions to understand the need"}
          {p.status === "sent" ? <> · <b className="ok-text">emailed {ago(p.sent_at)}</b></> : null}
          {p.approval_status === "sent" ? <> · <b className="ok-text">sent on WhatsApp</b></> : null}
          {p.replied_at ? <> · <b>they replied</b></> : null}
        </span>
        <button className="btn sm ghost" onClick={() => setOpen(!open)}>{open ? "Hide" : "Read it"}</button>
      </div>
      {open && <div className="prompt">{p.subject && it.email ? <><b>{p.subject}</b>{"\n\n"}</> : null}{text}</div>}
      {p.held && <span className="small" style={{ color: "var(--amber)" }}>{p.held}</span>}
      {p.send_error && <span className="small" style={{ color: "var(--amber)" }}>Not emailed: {p.send_error}</span>}
      {manager && <div className="row">
        {it.email && p.status !== "sent" && (
          <Busy className="btn sm primary" run={async () => { await api(`/inquiries/${it.id}/send-email`, { method: "POST" }); toast(`Emailed to ${it.email}`); reload(); }}>Send email</Busy>
        )}
        {it.phone && p.approval_id && p.approval_status === "pending" && (
          <Busy className="btn sm primary" run={async () => {
            const w = window.open("", "_blank");
            const r = await api(`/inquiries/${it.id}/whatsapp`, { method: "POST" });
            if (w) w.location = r.link; else window.location.href = r.link;
            reload();
          }}>Send on WhatsApp</Busy>
        )}
        <Copy text={text || ""} label="Copy" className="btn sm ghost" />
        <Busy className="btn sm ghost ai" run={async () => { await api(`/inquiries/${it.id}/proposal`, { method: "POST" }); toast("Written again"); reload(); }}>Write again</Busy>
      </div>}
    </div>
  );
}

const when = (iso) => new Date(iso).toLocaleString("en-IN", { weekday: "short", day: "numeric", month: "short", hour: "numeric", minute: "2-digit" });

/** The follow-up automation's message for this inquiry: send it from here with one tap, or skip it. */
function FollowUp({ it, reload }) {
  const { me, toast } = useHub();
  const [open, setOpen] = useState(false);
  const f = it.followups;
  if (!f || (!f.ready && !f.next_at && !f.sent)) return null;
  const manager = canAct(me, "manager") && me.features?.approvals;
  return (
    <div className="stack inq-prop">
      <div className="row between">
        <span className="small">
          {f.ready ? <b>Follow-up {f.ready.n} of {f.ready.of} is ready</b> : f.next_at ? `Next follow-up ${when(f.next_at)}` : null}
          {f.sent ? <>{f.ready || f.next_at ? " · " : ""}<b className="ok-text">{f.sent} sent</b></> : null}
        </span>
        {f.ready && <button className="btn sm ghost" onClick={() => setOpen(!open)}>{open ? "Hide" : "Read it"}</button>}
      </div>
      {f.ready && open && <div className="prompt">{f.ready.text}</div>}
      {f.ready && manager && it.phone && (
        <div className="row">
          <Busy className="btn sm primary" run={async () => {
            const w = window.open("", "_blank");
            const r = await api(`/approvals/${f.ready.id}/own`, { method: "POST" });
            if (w) w.location = r.link; else window.location.href = r.link;
            reload();
          }}>Send on WhatsApp</Busy>
          <Busy className="btn sm ghost" run={async () => { await api(`/approvals/${f.ready.id}/skip`, { method: "POST" }); toast("Skipped"); reload(); }}>Skip this one</Busy>
        </div>
      )}
    </div>
  );
}

export default function Inquiries() {
  const { me } = useHub();
  const loc = useLocation();
  const nav = useNavigate();
  const shared = useMemo(() => {
    if (!loc.pathname.endsWith("/new")) return null;
    const q = new URLSearchParams(loc.search);
    return [q.get("title"), q.get("text"), q.get("url")].filter(Boolean).join("\n").trim();
  }, [loc.pathname, loc.search]);
  const [tab, setTab] = useState("all");
  const [data, reload, err] = useLoad("/inquiries");
  const [s, reloadS] = useLoad(canAct(me, "manager") ? "/inquiries/settings" : "/inquiries");
  const [paste, setPaste] = useState(!!shared);
  useEffect(() => { if (shared) setPaste(true); }, [shared]);
  // proposals are written in the background: look again shortly after anything new
  useEffect(() => { const t = setInterval(() => { if (document.visibilityState === "visible") reload(); }, 15000); return () => clearInterval(t); }, [reload]);
  const items = (data?.items || []).filter((i) => tab === "all" || i.status === tab);
  const c = data?.counts || {};
  return (
    <Sheet kicker="Systems · Inquiries" id="SY-12" pillar="Systems"
      title={c.new ? <>{c.new} {c.new === 1 ? "inquiry" : "inquiries"} <em>waiting</em>.</> : <>Every inquiry, <em>answered</em>.</>}
      sub="Website, lead magnet, email and WhatsApp inquiries in one place, each with a proposal written from your Knowledge Hub."
      actions={<button className={`btn${IOS && !paste ? " primary" : ""}`} onClick={() => { setPaste(!paste); if (shared) nav("/inquiries", { replace: true }); }}>
        {paste ? "Close" : IOS ? "Paste a WhatsApp inquiry" : "New WhatsApp inquiry"}</button>}>
      {paste && <Paste prefill={shared || ""} onDone={() => { setPaste(false); if (shared) nav("/inquiries", { replace: true }); reload(); }} />}
      {canAct(me, "manager") && s && s.address !== undefined && <Setup s={s} reload={reloadS} />}
      <Tabs value={tab} onChange={setTab} tabs={[["all", "All"], ["new", `New${c.new ? ` · ${c.new}` : ""}`], ["proposal_sent", "Proposal sent"], ["in_pipeline", "In pipeline"]]} />
      {!data ? (err ? <FormError msg={err.message} /> : <Loading />) : !items.length ? (
        <Empty>{tab === "all" ? (IOS ? "No inquiries yet. Share your website, or paste a WhatsApp inquiry with the button above." : "No inquiries yet. Share your website, or add a WhatsApp inquiry above.") : "Nothing here."}</Empty>
      ) : (
        <div className="stack">
          {items.map((it) => (
            <div key={it.id} className="panel stack inq-item">
              <div className="row between">
                <div className="stack" style={{ gap: 2 }}>
                  <b>{it.name || it.email || it.phone}</b>
                  <span className="small muted">{[it.phone, it.email].filter(Boolean).join(" · ")}</span>
                </div>
                <div className="row" style={{ gap: 6 }}>
                  <Pill>{it.channel_label}</Pill>
                  <Pill kind={STATUS_KIND[it.status]}>{STATUS[it.status]}</Pill>
                  <span className="small muted">{ago(it.created_at)}</span>
                </div>
              </div>
              {it.message && <p className="inq-msg">{it.message}</p>}
              {(it.attachments || []).length > 0 && (
                <div className="row small">{it.attachments.map((a) => (
                  <a key={a.id} className="btn sm ghost" href={`/api/inquiries/${it.id}/files/${a.id}`}>{a.name} · {Math.max(1, Math.round(a.size / 1024))} KB</a>))}
                </div>
              )}
              {it.channel === "email" && it.verified === false && <span className="small muted">The sender's address couldn't be confirmed — check before you reply.</span>}
              {(it.replies || []).map((r, i) => <div key={i} className="inq-reply small"><b>They replied {ago(r.at)}:</b> {r.text}</div>)}
              <Proposal it={it} reload={reload} />
              <FollowUp it={it} reload={reload} />
              <div className="small">{me.features?.crm && me.features?.pipeline
                ? <Link to={`/pipeline?deal=${it.id}`}>Open the deal</Link>   /* v2.3: the deal (and its contact in the CRM) */
                : <><Link to="/leads">Open in Leads</Link>{me.features?.pipeline ? <> · <Link to="/pipeline">Pipeline</Link></> : null}</>}
                {me.features?.quotations && canAct(me, "manager") ? <> · <Link to={`/quotes?inquiry=${it.id}`}>Make a quotation</Link></> : null}</div>
            </div>
          ))}
        </div>
      )}
    </Sheet>
  );
}
