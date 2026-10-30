import React, { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api, localPhone, rs } from "../api.js";
import { Busy, Copy, Field, FormError, Gate, LoadError, Sheet, Switch, canAct, useHub, useLoad } from "../ui.jsx";
import { Empty, Loading, Num, Pill } from "../v2.jsx";
import SalesUpload from "./SalesUpload.jsx";
import "./moneyinsights.css";
import "./agents22.css";

/* Win-Back (v2.2, Action Program): customers who haven't bought for a while get a short personal hello on WhatsApp — and by
   email when there's an address. The hub drafts them every Tuesday; nothing goes out until someone taps Send. */

const HEAD = { kicker: "AI team · Win-Back", id: "AG-03", pillar: "AI team" };
const fmt = (iso) => (iso ? new Date(`${iso}T00:00:00`).toLocaleDateString("en-IN", { day: "numeric", month: "short", year: "numeric" }) : "");
const when = (iso) => (iso ? `${new Date(iso).toLocaleString("en-IN", { timeZone: "Asia/Kolkata", weekday: "long", day: "numeric", month: "short", hour: "numeric", minute: "2-digit" })} (India time)` : "");
const rupee = (n) => rs(Math.round(n || 0));
const WA = { pending: ["Waiting for you", "warn"], sent: ["Sent on WhatsApp", "on"], skipped: ["Skipped", "off"], expired: ["No longer needed", "off"], failed: ["Not sent", "warn"] };
const EM = { draft: ["Email ready", "warn"], sent: ["Email sent", "on"], skipped: ["Email skipped", "off"], expired: ["Email no longer needed", "off"] };

function Sample({ d, reload }) {
  const { me, toast } = useHub();
  const owner = canAct(me, "owner");
  const s = d.sample;
  const [read, setRead] = useState(false);
  return (
    <div className={`panel stack ${d.on ? "" : "active"}`}>
      <div className="row between">
        <span className="label">What a message looks like</span>
        <Pill kind={d.on ? "on" : ""}>{d.on ? "On" : "Off"}</Pill>
      </div>
      <p className="small muted">{s.for_name ? `For ${s.for_name} · ${s.kind_label.toLowerCase()}.` : "An example."} Each week the AI writes a fresh,
        short message like this for each kind of customer — 1 AI token a week. It never adds a price or a discount; only the offer you type below goes in, word for word.</p>
      <div className="prompt">{s.text}</div>
      <p className="small muted">By email, the subject is “{s.subject}”, with your business name at the end and a line saying they can reply to stop these.</p>
      {owner ? (d.on ? (
        <div className="row">
          <span className="small">Win-Back is on. {d.next_run_at ? `Next: ${when(d.next_run_at)}.` : ""}</span>
          <Busy className="btn sm ghost" run={async () => { await api("/win-back/settings", { method: "PUT", body: { on: false } }); toast("Win-Back is off"); reload(); }}>Switch off</Busy>
        </div>
      ) : (
        <div className="stack" style={{ gap: 8 }}>
          <Switch checked={read} onChange={setRead} label="I've read this message and I'm happy for it to go out in my business's name" />
          <div className="row">
            <Busy className="btn primary" disabled={!read} run={async () => {
              await api("/win-back/settings", { method: "PUT", body: { on: true, sample_key: s.key } });
              toast("Win-Back is on. Messages will wait in your Send list every Tuesday.");
              reload();
            }}>Switch Win-Back on</Busy>
            <span className="small muted">Every message still waits for you to tap Send.</span>
          </div>
        </div>
      )) : <p className="small muted">{d.on ? "Win-Back is on." : "Win-Back is off."} The owner switches it on.</p>}
    </div>
  );
}

function Settings({ d, reload }) {
  const { me, toast } = useHub();
  const owner = canAct(me, "owner");
  const [f, setF] = useState(d.settings);
  const [err, setErr] = useState("");
  useEffect(() => setF(d.settings), [d.settings]);
  const set = (k) => (v) => setF((x) => ({ ...x, [k]: v }));
  const changed = JSON.stringify(f) !== JSON.stringify(d.settings);
  return (
    <div className="panel stack">
      <span className="label">Who gets a message</span>
      <div className="wb-grid">
        <Field label="Not bought for">
          <select className="select" value={f.days} disabled={!owner} onChange={(e) => set("days")(Number(e.target.value))}>
            {d.limits.days.map((n) => <option key={n} value={n}>{n} days or more</option>)}
          </select>
        </Field>
        <Field label="Bought before">
          <select className="select" value={f.min_purchases} disabled={!owner} onChange={(e) => set("min_purchases")(Number(e.target.value))}>
            <option value={1}>At least once</option>
            <option value={2}>At least twice</option>
          </select>
        </Field>
        <Field label="Messages a week" hint={`Up to ${d.limits.per_week_max}. Biggest spenders first.`}>
          <Num value={f.per_week} min={1} max={d.limits.per_week_max} disabled={!owner} onChange={set("per_week")} />
        </Field>
      </div>
      <Field label="Your comeback offer (optional)" hint="Typed by you and added word for word, like “Free delivery on your next order this month”. Leave it empty for no offer — the AI never makes one up.">
        <input className="input" maxLength={160} value={f.offer} disabled={!owner} onChange={(e) => set("offer")(e.target.value)} placeholder="No offer" />
      </Field>
      <div className="stack" style={{ gap: 6 }}>
        <Switch checked={f.channels.whatsapp} disabled={!owner} onChange={(v) => set("channels")({ ...f.channels, whatsapp: v })} label="WhatsApp — ready in your Send list" />
        <Switch checked={f.channels.email} disabled={!owner} onChange={(v) => set("channels")({ ...f.channels, email: v })}
          label={`Email too, when you have their address${d.email_ready ? "" : " (email isn't set up on this hub yet)"}`} />
      </div>
      <p className="small muted">Nobody gets a second Win-Back message within {d.limits.no_repeat_days} days, and customers you marked “opted out” never do.</p>
      <FormError msg={err} />
      {owner && (
        <div className="row">
          <Busy className="btn" disabled={!changed} onError={setErr} run={async () => {
            await api("/win-back/settings", { method: "PUT", body: { days: f.days, min_purchases: f.min_purchases, per_week: f.per_week || 1, offer: f.offer, channels: f.channels } });
            toast("Saved");
            reload();
          }}>Save</Busy>
          {changed && <span className="small muted">Read the message above again after saving — an offer changes it.</span>}
        </div>
      )}
    </div>
  );
}

function Draft({ x, d, reload }) {
  const { toast, refresh } = useHub();
  const [open, setOpen] = useState(false);
  const wa = x.whatsapp;
  const em = x.email_draft;
  const done = () => { reload(); refresh?.(); };
  const tap = async () => {
    try { await api(`/win-back/${x.id}/whatsapp`, { method: "POST" }); }   // opened, not sent: asked on the card
    catch (e) { if (!e.handled) toast(e.message, "err"); }
    done();
  };
  const waiting = wa?.status === "pending" || em?.status === "draft";
  return (
    <div>
      <div className="who">
        <b>{x.name}</b>
        <span className="small muted">{[x.phone && localPhone(x.phone), x.email].filter(Boolean).join(" · ")}</span>
        <span className="small muted">Last bought {fmt(x.last_purchase)} — {x.days_since} days ago · {x.purchases} {x.purchases === 1 ? "purchase" : "purchases"}</span>
        <span className="row" style={{ gap: 6 }}>
          {wa && <Pill kind={(WA[wa.status] || [])[1]}>{(WA[wa.status] || [wa.status])[0]}</Pill>}
          {em && <Pill kind={(EM[em.status] || [])[1]}>{(EM[em.status] || [em.status])[0]}</Pill>}
        </span>
      </div>
      <span className="amt">{rupee(x.total)}<small>spent so far</small></span>
      <div className="acts" style={{ display: "grid", gap: 8 }}>
        {(waiting || open) && <div className="prompt">{x.text}</div>}
        <div className="row">
          {wa?.status === "pending" && <a className={`btn sm ${wa.opened ? "" : "primary"}`} href={wa.link} target="_blank" rel="noopener" onClick={tap}>{wa.opened ? "Open WhatsApp again" : "Send on WhatsApp"}</a>}
          {wa?.status === "pending" && wa.opened && (
            <Busy className="btn sm primary" run={async () => { await api(`/win-back/${x.id}/whatsapp-sent`, { method: "POST" }); toast(`Marked as sent to ${x.name}`); done(); }}>Yes, it's sent</Busy>
          )}
          {wa?.status === "pending" && d.connected && (
            <Busy className="btn sm" run={async () => { await api(`/win-back/${x.id}/send`, { method: "POST" }); toast(`Sent to ${x.name} from your number`); done(); }}>Send from my number</Busy>
          )}
          {em?.status === "draft" && (d.email_ready
            ? <Busy className="btn sm" run={async () => { await api(`/win-back/${x.id}/send-email`, { method: "POST" }); toast(`Emailed ${x.email}`); done(); }}>Send email</Busy>
            : <Copy className="btn sm ghost" label="Copy the email" text={`${x.subject}\n\n${x.email_text}`} />)}
          {waiting && <Busy className="btn sm ghost" run={async () => { await api(`/win-back/${x.id}/skip`, { method: "POST" }); toast("Skipped"); done(); }}>Skip</Busy>}
          {!waiting && <button className="btn sm ghost" onClick={() => setOpen(!open)}>{open ? "Hide" : "Read it"}</button>}
        </div>
      </div>
    </div>
  );
}

function Drafts({ d, reload }) {
  const { me, toast } = useHub();
  const manager = canAct(me, "manager");
  const list = d.drafts;
  const ready = list.filter((x) => x.whatsapp?.status === "pending" || x.email_draft?.status === "draft").length;
  return (
    <div className="panel stack">
      <div className="row between">
        <span className="label">This week{ready ? ` · ${ready} waiting for you` : ""}</span>
        {manager && d.on && (
          d.ran_today ? <span className="small muted">Ran today</span>
            : <Busy className="btn sm ai" run={async () => { const r = await api("/win-back/run", { method: "POST" }); toast(r.note); reload(); }}>Run now</Busy>
        )}
      </div>
      {!list.length ? (
        <Empty>{d.on ? `Nothing drafted yet. Messages for up to ${d.settings.per_week} customers are written on ${when(d.next_run_at)}.` : "Switch Win-Back on to get this week's messages."}</Empty>
      ) : (
        <div className="mi-list">{list.map((x) => <Draft key={x.id} x={x} d={d} reload={reload} />)}</div>
      )}
      {list.some((x) => x.whatsapp?.status === "pending") && (
        <p className="small muted">“Send on WhatsApp” opens your own WhatsApp with the message typed — you send it from there. These also wait in your <Link to="/approvals">Send list</Link>.</p>
      )}
    </div>
  );
}

function Candidates({ d }) {
  const [all, setAll] = useState(null);
  const shown = all || d.candidates.top;
  const n = d.candidates.count;
  return (
    <div className="panel stack">
      <span className="label">Who's due · {n} {n === 1 ? "customer" : "customers"}</span>
      <p className="small muted">Bought at least {d.settings.min_purchases === 1 ? "once" : "twice"}, and not in the last {d.settings.days} days (nor longer than 2 years ago). Biggest spenders first.</p>
      {!shown.length ? <Empty>Nobody right now. Upload your sales below so the hub knows when each customer last bought.</Empty> : (
        <div className="mi-list">
          {shown.map((c) => (
            <div key={c.key}>
              <div className="who">
                <b>{c.name}</b>
                <span className="small muted">Last bought {fmt(c.last_purchase)} — {c.days_since} days ago · {c.purchases} {c.purchases === 1 ? "purchase" : "purchases"}{c.source === "customers list" ? " · from your Customers list" : ""}</span>
                <span className="small muted">{c.channels.length ? `By ${c.channels.join(" and ").replace("whatsapp", "WhatsApp")}` : "No channel switched on for this customer"}</span>
              </div>
              <span className="amt">{rupee(c.total)}<small>spent</small></span>
            </div>
          ))}
        </div>
      )}
      {!all && n > shown.length && <Busy className="btn sm ghost" run={async () => setAll((await api("/win-back/candidates")).items)}>See all {n}</Busy>}
    </div>
  );
}

function Results({ r }) {
  return (
    <div className="panel stack">
      <span className="label">What it brought back</span>
      <p className="mi-line">{r.messaged ? r.line : "Nothing sent yet."}</p>
      <div className="statgrid">
        <div className="stat"><span className="label">Messaged</span><span className="num">{r.messaged}</span><span className="small muted">sent by you{r.weeks ? ` in ${r.weeks} ${r.weeks === 1 ? "week" : "weeks"}` : ""}</span></div>
        <div className="stat"><span className="label">Bought again</span><span className="num">{r.bought_again}</span><span className="small muted">within {r.window_days} days</span></div>
        <div className="stat"><span className="label">Their purchases</span><span className="num money">{rupee(r.amount)}</span><span className="small muted">from your sales records</span></div>
      </div>
      <p className="small muted">Counted, not estimated: a customer counts only when your sales (invoices, Razorpay, your uploaded sheet or your Customers list) show a purchase after the message. Sales you haven't uploaded yet don't count.</p>
    </div>
  );
}

function Inner() {
  const [d, reload, err] = useLoad("/win-back");
  if (!d) return <Sheet {...HEAD} title={<>Bring back customers who <em>went quiet</em>.</>}>{err ? <LoadError err={err} /> : <Loading />}</Sheet>;
  return (
    <Sheet {...HEAD} title={<>Bring back customers who <em>went quiet</em>.</>}
      sub={`Every Tuesday, customers who haven't bought in ${d.settings.days} days get a short, personal hello on WhatsApp — and by email when you have their address. The hub writes it; nothing goes out until you tap Send.`}>
      {d.last_run?.note && <p className="small muted">Last run: {d.last_run.note}</p>}
      <Sample d={d} reload={reload} />
      <Drafts d={d} reload={reload} />
      <Results r={d.results} />
      <Candidates d={d} />
      <Settings d={d} reload={reload} />
      <SalesUpload onChange={reload} note={d.data.little ? "Your Tally sales register gives the hub the last-purchase dates. Export it from Tally (Sales Register → Excel) and upload it here." : ""} />
    </Sheet>
  );
}

export default function WinBack() {
  return (
    <Gate feature="win_back" role="manager" kicker={HEAD.kicker} id={HEAD.id}
      what="Customers who haven't bought in 60 days or more get a personal message on WhatsApp or email — drafted for you, sent when you tap.">
      <Inner />
    </Gate>
  );
}
