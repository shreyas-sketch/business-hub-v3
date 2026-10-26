import React, { useEffect, useState } from "react";
import { ago, api } from "../api.js";
import { Busy, Copy, Field, FormError, Sheet, useHub, useLoad } from "../ui.jsx";
import { Loading, Pill } from "../v2.jsx";
import "./running.css";

// Option names for select fields. The WhatsApp service names come from the server (view.services), so the card itself only
// ever says "WhatsApp tool" — whichever service the number runs on.
const OPTION = { twilio: "Cloud phone number", sip: "SIP trunk", demo: "Demo (test only)" };

const WHAT = {
  whatsapp: "Connect the WhatsApp tool your business number runs on. Replies, reminders and follow-ups can then go out from your own number.",
  employz: "Every inquiry lands in your CRM's pipeline. Your CRM's contacts, pipelines and opportunities show in the hub to view (changes are made in Employz.ai).",
  voice: "The AI Telecaller calls new leads from your voice agent and its phone number.",
  tally: "Read sales and money owed from the accounting software on your office computer.",
  email: "Send emails from your own address.",
  calendar: "The booking link your AI staff share when a customer wants a meeting.",
  razorpay: "Every payment a customer makes through your payment gateway becomes a GST invoice on its own — or marks the matching amount in Money owed paid. A refund becomes a credit note. Every invoice also goes to your payment gateway account: with a pay link while it's unpaid, as a record once it's paid.",
};

// Where to paste the webhook address. The steps name the service only once it's picked (like the WhatsApp tool's picker).
const HOOK_HINT = {
  whatsapp: "Paste this as the webhook address in your WhatsApp tool, so customer messages come into your hub. For Meta, it's the callback URL and the verify token is the last part of the address.",
  voice: "Paste this as the post-call webhook in your voice agent service, so every call comes back here with its summary.",
  employz: "Paste this into the webhook step of a workflow in your CRM, so deals you move there move here too.",
  razorpay: "In your payment gateway's webhook settings (usually Account & Settings → Webhooks), add this address with the events payment.captured, refund.processed, invoice.paid, invoice.partially_paid, invoice.expired and invoice.cancelled, and type the same webhook secret as above. Try it in test mode first: a test payment shows up as an invoice here.",
};

// WhatsApp tool: only the fields the chosen service needs (view.service_fields), so the form stays short.
// CRM: the pipeline and its 5 stage ids are filled by the pipeline picker (EmployzImport), not typed in.
const PICKED = /^(pipeline_id|stage_)/;
const onlyFor = (view, vals) => {
  if (view.kind === "employz") return view.fields.filter((f) => !PICKED.test(f.key));
  const allowed = view.service_fields?.[vals.provider || ""];
  return allowed ? view.fields.filter((f) => allowed.includes(f.key)) : view.kind === "whatsapp" ? view.fields.filter((f) => f.key === "provider") : view.fields;
};

const fromView = (view) => Object.fromEntries(view.fields.map((f) => [f.key, f.type === "secret" ? "" : String(f.value ?? "")]));

/** The form for one connection. Secrets are never shown: a saved one stays unless a new value is typed. Only changed fields are sent. */
export function ConnectionForm({ view, onSaved, children }) {
  const { toast } = useHub();
  const [vals, setVals] = useState(() => fromView(view));
  const [err, setErr] = useState("");
  const set = (k) => (e) => setVals({ ...vals, [k]: e.target.value });
  const changed = view.fields.filter((f) => (f.type === "secret" ? vals[f.key].trim() !== "" : vals[f.key] !== String(f.value ?? "")));
  const shown = onlyFor(view, vals);
  const names = view.services || OPTION;
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
                  {(f.options || []).map((o) => <option key={o} value={o}>{names[o] || OPTION[o] || o}</option>)}
                  {vals[f.key] && !(f.options || []).includes(vals[f.key]) && <option value={vals[f.key]}>{names[vals[f.key]] || OPTION[vals[f.key]] || vals[f.key]}</option>}
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

const SYNC_STATE = {
  never: ["Not imported yet", "off"], running: ["Importing…", ""], idle: ["Up to date", "on"], failed: ["Last import failed", "warn"],
  needs_attention: ["Needs attention", "warn"], disconnected: ["Disconnected", "off"],
};
const KINDS = [["pipelines", "Pipelines"], ["contacts", "Contacts"], ["opportunities", "Opportunities"]];

/** CRM (Employz.ai): which pipeline new hub leads go to, and the read-only import of its contacts, pipelines and opportunities. */
export function EmployzImport() {
  const { toast } = useHub();
  const [st, reloadSt] = useLoad("/employz/import");
  const [pipes, setPipes] = useState(null);
  const [pipesErr, setPipesErr] = useState("");
  const [pick, setPick] = useState("");
  const [map, setMap] = useState({});           // hub step → Employz.ai stage id
  const [remote, setRemote] = useState(null);
  const [err, setErr] = useState("");
  // a pipeline's steps start from what's saved for it, else from the stages whose names match
  const startMap = (r, id) => (id && id === r?.selected ? r.saved : (r?.pipelines || []).find((p) => p.id === id)?.suggested) || {};
  const choose = (id, r = remote) => { setPick(id); setMap(startMap(r, id)); };
  const loadPipes = () => api("/employz/remote-pipelines").then((r) => { setRemote(r); setPipes(r.pipelines); choose(r.selected || "", r); setPipesErr(""); })
    .catch((e) => setPipesErr(e.message || "Employz.ai couldn't be read just now."));
  useEffect(() => { loadPipes(); }, []);
  useEffect(() => {                          // while an import runs, its progress refreshes on its own
    if (!st?.running) return undefined;
    const t = setInterval(reloadSt, 3000);
    return () => clearInterval(t);
  }, [st?.running]);
  const [label, kind] = SYNC_STATE[st?.state] || SYNC_STATE.never;
  const chosen = (pipes || []).find((p) => p.id === pick);
  return (
    <div className="panel flat stack" style={{ gap: 12 }}>
      <div className="stack" style={{ gap: 6 }}>
        <span className="label">Where new hub leads go</span>
        {pipesErr ? <p className="small" style={{ color: "var(--grey)" }}>{pipesErr}</p> : !pipes ? <Loading /> : (
          <div className="row" style={{ gap: 8, flexWrap: "wrap" }}>
            <select className="select" value={pick} onChange={(e) => choose(e.target.value)} aria-label="Pipeline for new leads">
              <option value="">Choose a pipeline…</option>
              {pipes.map((p) => <option key={p.id} value={p.id}>{p.name} · {p.stages.length} stages</option>)}
            </select>
          </div>
        )}
        {chosen && (
          <div className="stack" style={{ gap: 8 }}>
            <span className="hint">As an inquiry moves through the hub's 5 steps, its opportunity moves to the stage you pick here.</span>
            <div className="ez-steps">
              {Object.entries(remote?.steps || {}).map(([k, name], i) => (
                <label key={k} className="ez-step">
                  <span>{i + 1} · {name}</span>
                  <select className="select" value={map[k] || ""} aria-label={`Stage for ${name}`} onChange={(e) => setMap({ ...map, [k]: e.target.value })}>
                    <option value="">Pick a stage…</option>
                    {chosen.stages.map((st) => <option key={st.id} value={st.id}>{st.name}</option>)}
                  </select>
                </label>
              ))}
            </div>
            <div className="row">
              <Busy className="btn" disabled={Object.keys(remote?.steps || {}).some((k) => !map[k])} onError={setErr} run={async () => {
                const r = await api("/employz/push-pipeline", { method: "POST", body: { pipeline_id: pick, stages: map } });
                setRemote((x) => ({ ...x, selected: pick, saved: map }));
                toast(`New leads go to ${r.pipeline.name}`);
              }}>Use this pipeline</Busy>
            </div>
          </div>
        )}
      </div>
      <div className="stack" style={{ gap: 6 }}>
        <div className="row between">
          <span className="label">Import from Employz.ai (view only)</span>
          <Pill kind={kind}>{label}</Pill>
        </div>
        {st && (
          <>
            <p className="small muted">
              {st.last_ok_at ? <>Last synced {ago(st.last_ok_at)}. </> : null}
              Your contacts, pipelines and opportunities are copied in every {st.every_minutes} minutes. Change them in Employz.ai; the hub never changes anything there.
            </p>
            {st.running && st.step && <p className="small">Reading {st.step}… {Object.entries(st.pages || {}).map(([k, n]) => `${k}: ${n} ${n === 1 ? "page" : "pages"}`).join(" · ")}</p>}
            {st.error && <p className="small" style={{ color: "var(--grey)" }}>{st.error.message}</p>}
            <table className="t cards">
              <thead><tr><th>Kind</th><th>In the hub</th><th>In Employz.ai</th><th>Skipped</th></tr></thead>
              <tbody>{KINDS.map(([k, name]) => {
                const cov = st.coverage?.[k] || {};
                return (
                  <tr key={k}>
                    <td><b>{name}</b></td>
                    <td data-l="In the hub">{st.in_hub?.[k] ?? 0}</td>
                    <td data-l="In Employz.ai" className="small muted">{cov.source ?? "—"}</td>
                    <td data-l="Skipped" className="small muted">{cov.rejected || 0}</td>
                  </tr>
                );
              })}</tbody>
            </table>
            {st.rejected?.length > 0 && (
              <details className="small muted"><summary style={{ cursor: "pointer" }}>Why some were skipped</summary>
                <ul>{st.rejected.map((r, i) => <li key={i}>{r.type}: {r.reason}</li>)}</ul>
              </details>
            )}
          </>
        )}
        <FormError msg={err} />
        <div className="row">
          <Busy className="btn primary" disabled={!!st?.running} onError={setErr} run={async () => {
            await api("/employz/import", { method: "POST" });
            toast("Import started");
            reloadSt();
          }}>{st?.running ? "Importing…" : "Import now"}</Busy>
        </div>
      </div>
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
          {c.kind === "employz" && c.connected && <EmployzImport />}
          <Webhook kind={c.kind} url={c.webhook} />
        </>
      )}
    </div>
  );
}

/** Connections your plan doesn't include yet: one quiet line per plan, not a wall of locked forms. With the v2.3 engine
    menu, only the ones the Membership itself opens are listed (spec §14: show only the connections the plan uses). */
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
  const { me } = useHub();
  const [list, reload] = useLoad("/connections");
  const engines = !!me?.menu?.[0]?.section;   // v2.3: the engine menu is on
  const open = (list || []).filter((c) => c.available);
  const locked = (list || []).filter((c) => !c.available && (!engines || ["free", "lite"].includes(me.feature_tiers?.[c.feature])));
  // v2.3 spec §14 "change connections column": each connection is named by its category (CRM, Payment gateway, WhatsApp
  // tool…), not the software company; the company only shows as the choice in its picker (app/connections.py SERVICES).
  const done = open.filter((c) => c.connected).length;
  const title = !list ? <>Your own accounts, <em>connected</em>.</>
    : done && done === open.length ? <>All your accounts are <em>connected</em>.</>
    : <>{done} of {open.length} {open.length === 1 ? "account" : "accounts"} <em>connected</em>.</>;
  return (
    <Sheet kicker="Systems · Connections" id="GM-04" pillar="Systems" title={title}
      sub="Your hub works from your own accounts — your payment gateway, your WhatsApp tool and your CRM. Our team can set these up for you; you can also add or change the details here.">
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
