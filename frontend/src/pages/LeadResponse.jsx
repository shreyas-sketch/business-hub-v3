import React, { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../api.js";
import { Busy, Field, FormError, Gate, LoadError, Sheet, Switch, canAct, useHub, useLoad } from "../ui.jsx";
import { Loading, Pill } from "../v2.jsx";
import "./agents22.css";

/* Lead Response (v2.2, Growth Mentorship): the first reply to every new inquiry, within seconds, from the owner's own WhatsApp
   number — with a call or visit to book. Prices and quotes always go to a person. Every number on this page is measured. */

const HEAD = { kicker: "AI team · Lead Response", id: "AG-04", pillar: "AI team" };
const REASON = { "not-connected": "WhatsApp number not connected", "no-template": "template not set", "no-phone": "no mobile number",
  "email-not-verified": "email sender not verified", "skipped-repeat": "already replied to that number today",
  "skipped-cap": "daily limit reached", "owner-whatsapp": "WhatsApp didn't accept it" };
const secs = (s) => (s == null ? "—" : s < 1 ? "under 1 sec" : s < 60 ? `${Math.round(s)} sec` : `${Math.floor(s / 60)} min ${Math.round(s % 60)} sec`);

function Status({ d, reload }) {
  const { me, toast } = useHub();
  const owner = canAct(me, "owner");
  const c = d.connection;
  const save = async (body, msg) => { await api("/lead-response/settings", { method: "PUT", body }); toast(msg); reload(); };
  return (
    <div className={`panel stack ${d.settings.on ? "" : "active"}`}>
      <div className="row between">
        <span className="label">Status</span>
        <Pill kind={d.settings.on && d.connected ? "on" : "warn"}>{d.settings.on ? (d.connected ? "On" : "On — but can't send yet") : "Off"}</Pill>
      </div>
      <ul className="lr-channels">
        <li className={c.number ? "" : "no"}><span className="mark">{c.number ? "✓" : "✗"}</span><span>Your WhatsApp number {c.number ? "is connected" : "isn't connected yet"}</span></li>
        <li className={c.template ? "" : "no"}><span className="mark">{c.template ? "✓" : "✗"}</span>
          <span>{c.template ? <>Template “{c.template_name}” is set</> : <>The “first reply with a booking” template isn't set yet</>}</span></li>
      </ul>
      {!d.connected && (
        <div className="notice small">
          <span>Until both are done, new inquiries get no first reply from Lead Response — and nothing is ever sent from the hub's own number instead.</span>
          {owner && <Link className="btn sm" to="/connections">Open Connections</Link>}
        </div>
      )}
      {d.note && !d.settings.on && <div className="notice small"><span>{d.note}</span></div>}
      {owner ? (d.settings.on
        ? <div className="row"><span className="small">Every new inquiry gets the first reply below at once.</span>
            <Busy className="btn sm ghost" run={() => save({ on: false }, "Lead Response is off")}>Switch off</Busy></div>
        : <p className="small muted">Read the first reply below, then switch it on there.</p>
      ) : <p className="small muted">The owner switches Lead Response on or off.</p>}
      {d.settings.on && d.instant_reply_on && <p className="small muted">This first reply replaces the plain instant reply for website, lead magnet and email inquiries — never both.</p>}
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
  const own = !d.what_options.includes(f.what);
  const changed = ["booking", "booking_link", "what", "hours", "prices_to_person"].some((k) => f[k] !== d.settings[k]);
  return (
    <div className="panel stack">
      <span className="label">The booking</span>
      <div className="lr-seg" role="radiogroup" aria-label="How they book">
        <button type="button" role="radio" aria-checked={f.booking === "times"} className={`btn sm ${f.booking === "times" ? "on" : "ghost"}`}
          disabled={!owner} onClick={() => set("booking")("times")}>Offer two times</button>
        <button type="button" role="radio" aria-checked={f.booking === "link"} className={`btn sm ${f.booking === "link" ? "on" : "ghost"}`}
          disabled={!owner} onClick={() => set("booking")("link")}>Send my calendar link</button>
      </div>
      {f.booking === "link" ? (
        <Field label="Your booking page" hint="Your Calendly, Google appointment page or Employz.ai calendar. It must start with https://">
          <input className="input" inputMode="url" maxLength={300} value={f.booking_link} disabled={!owner} placeholder="https://"
            onChange={(e) => set("booking_link")(e.target.value)} />
        </Field>
      ) : (
        <Field label="Your hours for calls and visits" hint="Like 10-19 for 10am to 7pm, India time. Times are offered inside these hours, never on a Sunday. The hub doesn't see your calendar — you confirm when they reply.">
          <input className="input" maxLength={5} value={f.hours} disabled={!owner} onChange={(e) => set("hours")(e.target.value)} />
        </Field>
      )}
      <Field label="What they book">
        <select className="select" value={own ? "__own" : f.what} disabled={!owner}
          onChange={(e) => set("what")(e.target.value === "__own" ? "" : e.target.value)}>
          {d.what_options.map((w) => <option key={w} value={w}>{w}</option>)}
          <option value="__own">Something else…</option>
        </select>
      </Field>
      {own && (
        <Field label="In your words" hint="A few words, like “a free design consultation”.">
          <input className="input" maxLength={40} value={f.what} disabled={!owner} onChange={(e) => set("what")(e.target.value)} />
        </Field>
      )}
      <Switch checked={f.prices_to_person} disabled={!owner} onChange={set("prices_to_person")}
        label="Prices, quotes and discounts always come to me" />
      <span className="hint">{f.prices_to_person
        ? "The WhatsApp AI never shares a price or a discount. It says your team will share it and hands the chat to you."
        : "The WhatsApp AI may share prices you wrote in your Knowledge Hub. It still never invents one or negotiates."}</span>
      <FormError msg={err} />
      {owner && (
        <div className="row">
          <Busy className="btn" disabled={!changed} onError={setErr} run={async () => {
            const r = await api("/lead-response/settings", { method: "PUT", body: { booking: f.booking, booking_link: f.booking_link, what: f.what, hours: f.hours, prices_to_person: f.prices_to_person } });
            toast(d.settings.on && !r.settings.on ? "Saved. The booking message changed, so Lead Response is off until you read it and switch it on again." : "Saved");
            reload();
          }}>Save</Busy>
        </div>
      )}
    </div>
  );
}

function Preview({ d, reload }) {
  const { me, toast } = useHub();
  const owner = canAct(me, "owner");
  const p = d.preview;
  const [read, setRead] = useState(false);
  return (
    <div className={`panel stack ${d.settings.on ? "" : "active"}`}>
      <span className="label">The first reply</span>
      <p className="small muted">It goes as your approved WhatsApp template. If your template reads “{p.template_hint}”, the customer gets:</p>
      <div className="prompt">{p.text}</div>
      <p className="small muted">{"{{3}}"}, the booking line: “{p.wording}”{d.settings.booking === "times" ? " The two times are worked out when each inquiry comes in." : ""}</p>
      {owner && !d.settings.on && (
        <div className="stack" style={{ gap: 8 }}>
          <Switch checked={read} onChange={setRead} label="I've read this first reply and I'm happy for it to go out in my business's name" />
          <div className="row">
            <Busy className="btn primary" disabled={!read} run={async () => {
              await api("/lead-response/settings", { method: "PUT", body: { on: true, sample_key: p.key } });
              toast("Lead Response is on");
              reload();
            }}>Switch Lead Response on</Busy>
            <span className="small muted">If you change the booking later, it switches off until you read it again.</span>
          </div>
        </div>
      )}
      {owner && d.connected && (
        <div className="row">
          <Busy className="btn sm" run={async () => { await api("/lead-response/test", { method: "POST" }); toast("Sent to your own WhatsApp"); }}>Send it to my WhatsApp</Busy>
          <span className="small muted">To see it exactly as a customer would.</span>
        </div>
      )}
    </div>
  );
}

function Stats({ s }) {
  const misses = Object.entries(s.not_sent || {});
  return (
    <div className="panel stack">
      <span className="label">Last {s.days} days</span>
      <div className="statgrid">
        <div className="stat"><span className="label">Inquiries</span><span className="num">{s.inquiries}</span><span className="small muted">website, lead magnet, email</span></div>
        <div className="stat"><span className="label">First replies sent</span><span className="num">{s.first_replies}</span><span className="small muted">each with a booking line</span></div>
        <div className="stat"><span className="label">Typical time</span><span className="num">{secs(s.median_seconds)}</span><span className="small muted">half were faster</span></div>
        <div className="stat"><span className="label">Slowest 1 in 10</span><span className="num">{secs(s.p90_seconds)}</span><span className="small muted">9 in 10 were faster</span></div>
        <div className="stat"><span className="label">Wrote back</span><span className="num">{s.replied}</span><span className="small muted">on WhatsApp after the reply</span></div>
      </div>
      {misses.length > 0 && <p className="small muted">Not replied to: {misses.map(([k, n]) => `${n} — ${REASON[k] || k}`).join(" · ")}.</p>}
      <p className="small muted">Measured from the moment the inquiry came in to the moment the reply was handed to WhatsApp.</p>
    </div>
  );
}

function Channels({ list }) {
  return (
    <div className="panel stack">
      <span className="label">Where inquiries come from</span>
      <ul className="lr-channels">
        {list.map((c) => (
          <li key={c.key} className={c.ok ? "" : "no"}>
            <span className="mark" aria-label={c.ok ? "Covered" : "Not covered"}>{c.ok ? "✓" : "✗"}</span>
            <b>{c.label}</b>
            <span className="small muted note">{c.note}</span>
          </li>
        ))}
      </ul>
      <p className="small muted">Instagram and Facebook messages need your Employz.ai inbox connected first. That's a setup step the team does with you — ask on your next setup call.</p>
    </div>
  );
}

function Inner() {
  const [d, reload, err] = useLoad("/lead-response");
  const title = <>Every inquiry answered in <em>2 minutes</em>.</>;
  if (!d) return <Sheet {...HEAD} title={title}>{err ? <LoadError err={err} /> : <Loading />}</Sheet>;
  return (
    <Sheet {...HEAD} title={title}
      sub="Every new inquiry gets a first reply within 2 minutes, day or night — with a call or visit booked. Prices and quotes come to you.">
      <Status d={d} reload={reload} />
      <Preview d={d} reload={reload} />
      <Settings d={d} reload={reload} />
      <Stats s={d.stats} />
      <Channels list={d.channels} />
    </Sheet>
  );
}

export default function LeadResponse() {
  return (
    <Gate feature="lead_response" role="manager" kicker={HEAD.kicker} id={HEAD.id}
      what="Every new inquiry gets a first reply within 2 minutes, day or night, from your own WhatsApp number — with a call or visit booked. Prices and quotes come to you.">
      <Inner />
    </Gate>
  );
}
