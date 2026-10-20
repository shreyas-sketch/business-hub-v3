import React, { useState } from "react";
import { ago, api } from "../api.js";
import { Busy, Copy, Field, FormError, Sheet, useHub, useLoad } from "../ui.jsx";
import { Loading, Pill } from "../v2.jsx";
import "./running.css";

const OPTION = { aisensy: "AiSensy", meta: "Meta (WhatsApp Cloud API)", twilio: "Twilio", sip: "SIP trunk", demo: "Demo (test only)" };

const WHAT = {
  whatsapp: "Your Sales Executive replies, and reminders and campaigns go out, from your own WhatsApp Business number.",
  employz: "Every inquiry lands in your Employz.ai pipeline, and moves when you move it in the hub.",
  voice: "The AI Telecaller calls new leads from this ElevenLabs agent and phone number.",
  tally: "Read sales and money owed from Tally on your office computer.",
  email: "Send emails from your own address.",
  calendar: "The booking link your AI staff share when a customer wants a meeting.",
  razorpay: "Every payment a customer makes through your own Razorpay becomes a GST invoice on its own — or marks the matching amount in Money owed paid. A refund becomes a credit note.",
};

const HOOK_HINT = {
  whatsapp: "Paste this as the webhook in AiSensy, or as the callback URL in your Meta app (the verify token is the last part of the address).",
  voice: "Paste this as the post-call webhook in ElevenLabs, so every call comes back here with its summary.",
  employz: "Paste this into the webhook step of an Employz.ai workflow, so deals you move there move here too.",
  razorpay: "In Razorpay → Account & Settings → Webhooks, add this address with the events payment.captured and refund.processed, and type the same webhook secret as above. Try it in test mode first: a test payment shows up as an invoice here.",
};

// WhatsApp: hide the other provider's fields so the form stays short.
const HIDE = {
  whatsapp: {
    aisensy: ["phone_number_id", "access_token", "app_secret", "language"],
    meta: ["api_key", "project_id", "project_password"],
  },
};

const fromView = (view) => Object.fromEntries(view.fields.map((f) => [f.key, f.type === "secret" ? "" : String(f.value ?? "")]));

/** The form for one connection. Secrets are never shown: a saved one stays unless a new value is typed. Only changed fields are sent. */
export function ConnectionForm({ view, onSaved, children }) {
  const { toast } = useHub();
  const [vals, setVals] = useState(() => fromView(view));
  const [err, setErr] = useState("");
  const set = (k) => (e) => setVals({ ...vals, [k]: e.target.value });
  const changed = view.fields.filter((f) => (f.type === "secret" ? vals[f.key].trim() !== "" : vals[f.key] !== String(f.value ?? "")));
  const hidden = HIDE[view.kind]?.[vals.provider || "aisensy"] || [];
  const shown = view.fields.filter((f) => !hidden.includes(f.key));
  return (
    <div className="stack">
      <div className="grid2">
        {shown.map((f) => {
          const hint = f.type === "secret" && f.set ? `Saved — enter a new value to replace.${f.help ? ` ${f.help}` : ""}` : f.help;
          return (
            <Field key={f.key} label={f.label} hint={hint}>
              {f.type === "select" ? (
                <select className="select" value={vals[f.key]} onChange={set(f.key)}>
                  <option value="">Choose…</option>
                  {(f.options || []).map((o) => <option key={o} value={o}>{OPTION[o] || o}</option>)}
                  {vals[f.key] && !(f.options || []).includes(vals[f.key]) && <option value={vals[f.key]}>{OPTION[vals[f.key]] || vals[f.key]}</option>}
                </select>
              ) : f.type === "secret" ? (
                <input className="input" type="password" autoComplete="new-password" maxLength={2000} value={vals[f.key]} onChange={set(f.key)}
                  placeholder={f.set ? "••••••••" : ""} />
              ) : (
                <input className="input" type={f.type === "number" ? "number" : "text"} inputMode={f.type === "number" ? "numeric" : undefined}
                  maxLength={2000} value={vals[f.key]} onChange={set(f.key)} />
              )}
            </Field>
          );
        })}
      </div>
      <FormError msg={err} />
      <div className="row">
        <Busy className="btn primary" disabled={!changed.length} onError={setErr} run={async () => {
          const values = Object.fromEntries(changed.map((f) => [f.key, vals[f.key].trim()]));
          const v = await api(`/connections/${view.kind}`, { method: "PUT", body: { values } });
          setVals(fromView(v));
          toast(v.connected ? "Saved — connected" : "Saved");
          onSaved(v);
        }}>Save</Busy>
        {children}
      </div>
    </div>
  );
}

/** The address another service calls with events, with where to paste it. */
export function Webhook({ kind, url }) {
  if (!url) return null;
  return (
    <div className="stack" style={{ gap: 6 }}>
      <span className="label">Webhook address</span>
      <div className="rb-hook">
        <input className="input" readOnly value={url} aria-label="Webhook address" onFocus={(e) => e.target.select()} />
        <Copy text={url} />
      </div>
      <span className="hint">{HOOK_HINT[kind]}</span>
    </div>
  );
}

function state(c) {
  if (!c.available) return ["Locked", "off"];
  if (c.status === "error") return ["Check failed", "warn"];
  if (c.connected) return [c.status === "ok" ? "Connected · tested" : "Connected", "on"];
  if (c.status) return ["Not finished", ""];
  return ["Not set up", "off"];
}

function Panel({ c, reload }) {
  const { me, toast, upgrade } = useHub();
  const [label, kind] = state(c);
  const stored = !!(c.status || c.updated_by);
  const [editing, setEditing] = useState(!c.connected);   // a working connection stays folded until you want to change it
  const actions = (
    <>
      {c.connected && (
        <Busy className="btn" run={async () => {
          const r = await api(`/connections/${c.kind}/test`, { method: "POST" });
          toast(r.note || (r.ok ? "Working" : "Not working"), r.ok ? "ok" : "err");
          reload();
        }}>Test the connection</Busy>
      )}
      {stored && (
        <Busy className="btn ghost" run={async () => {
          if (!window.confirm(`Remove the ${c.label} connection? Your AI staff stop using it until it's added again.`)) return;
          await api(`/connections/${c.kind}`, { method: "DELETE" });
          toast("Removed");
          setEditing(true);
          reload();
        }}>Remove</Busy>
      )}
    </>
  );
  return (
    <div className={`panel ${c.connected ? "active" : ""}`}>
      <div className="row between">
        <h3>{c.label}</h3>
        <Pill kind={kind}>{label}</Pill>
      </div>
      {WHAT[c.kind] && <p className="small muted">{WHAT[c.kind]}</p>}
      {(c.note || c.updated_by) && (
        <p className="small muted">
          {c.note && <>{c.status === "error" ? "Last check: " : ""}<span style={{ color: "var(--paper)" }}>{c.note}</span>{c.checked_at ? ` · ${ago(c.checked_at)}` : ""}</>}
          {c.note && c.updated_by ? " · " : ""}
          {c.updated_by && `Last saved by ${c.updated_by}`}
        </p>
      )}
      {!c.available ? (
        <div className="row between panel flat" style={{ padding: "12px 16px" }}>
          <span className="small muted">Unlocks with <b style={{ color: "var(--paper)" }}>{c.tier_name}</b></span>
          {me.workspace?.role === "owner" && me.feature_tiers?.[c.feature] && (
            <button className="btn sm money" onClick={() => upgrade(me.feature_tiers[c.feature], c.feature)}>See {c.tier_name}</button>
          )}
        </div>
      ) : (
        <>
          {editing ? (
            <ConnectionForm key={JSON.stringify(c.fields)} view={c} onSaved={reload}>{actions}</ConnectionForm>
          ) : (
            <div className="row">
              <button className="btn" onClick={() => setEditing(true)}>Change the details</button>
              {actions}
            </div>
          )}
          <Webhook kind={c.kind} url={c.webhook} />
        </>
      )}
    </div>
  );
}

/** Connections your plan doesn't include yet: one quiet line per plan, not a wall of locked forms. */
function Later({ locked }) {
  const { me, upgrade } = useHub();
  const RANK = ["free", "lite", "program", "running", "growth", "office"];
  const rank = (c) => RANK.indexOf(me.feature_tiers?.[c.feature] || "office");
  const byTier = {};
  [...locked].sort((a, b) => rank(a) - rank(b)).forEach((c) => { (byTier[c.tier_name] = byTier[c.tier_name] || []).push(c); });
  return (
    <div className="panel flat stack" style={{ gap: 8 }}>
      <span className="label">With a higher plan</span>
      {Object.entries(byTier).map(([tier, cs]) => (
        <div key={tier} className="row between" style={{ gap: 10 }}>
          <span className="small muted"><b style={{ color: "var(--paper)" }}>{tier}:</b> {cs.map((c) => c.label).join(" · ")}</span>
          {me.workspace?.role === "owner" && me.feature_tiers?.[cs[0].feature] && (
            <button className="btn sm money" onClick={() => upgrade(me.feature_tiers[cs[0].feature], cs[0].feature)}>See {tier}</button>
          )}
        </div>
      ))}
    </div>
  );
}

export default function Connections() {
  const [list, reload] = useLoad("/connections");
  const open = (list || []).filter((c) => c.available);
  const locked = (list || []).filter((c) => !c.available);
  const done = open.filter((c) => c.connected).length;
  const title = !list ? <>Your own accounts, <em>connected</em>.</>
    : done && done === open.length ? <>All your accounts are <em>connected</em>.</>
    : <>{done} of {open.length} {open.length === 1 ? "account" : "accounts"} <em>connected</em>.</>;
  return (
    <Sheet kicker="Systems · Connections" id="GM-04" pillar="Systems" title={title}
      sub="Your hub works from your own accounts — your payment gateway, and on higher plans your WhatsApp number, CRM and voice agent. Our team can set these up for you; you can also add or change the details here.">
      <p className="small muted">Keys, tokens and passwords are stored encrypted and are never shown again after you save them. Leave a saved one empty to keep it.</p>
      {!list ? <Loading /> : (
        <div className="stack" style={{ gap: 16 }}>
          {open.map((c) => <Panel key={c.kind} c={c} reload={reload} />)}
          {!open.length && <p className="muted">Your plan doesn't include any connections yet.</p>}
          {locked.length > 0 && <Later locked={locked} />}
        </div>
      )}
    </Sheet>
  );
}
