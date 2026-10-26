import React, { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api, day } from "../api.js";
import { Busy, Field, FormError, Modal, Sheet, Stat, canAct, useHub, useLoad } from "../ui.jsx";
import { Empty, Loading, Num, Pill } from "../v2.jsx";
import "./growth.css";

const DAILY_CAP = 200;
const KINDS = {
  revival: ["Old-customer revival", "Bring back customers who haven't bought in a while."],
  festival: ["Festival offer", "A reason to buy now, tied to a festival or season."],
  referral: ["Referral drive", "Ask happy customers to introduce a friend."],
  custom: ["Your own idea", "Anything else you want to tell past customers."],
};
const STATUS = { draft: ["Draft", ""], running: ["Sending", "on"], done: ["Sent", "on"], stopped: ["Stopped", "off"] };
const TIERS = [["A", "A · best customers", "The customers who make up 70% of your sales"], ["B", "B · regular", "The next 20% of your sales"],
  ["C", "C · occasional", "Everyone else, and customers without a group"]];
const NOTES = {
  revival: "e.g. 10% off the next order for anyone who hasn't bought since March",
  festival: "e.g. Diwali: free chimney with any kitchen booked before 30 October",
  referral: "e.g. ₹2,000 off for both of you when a friend books a kitchen",
  custom: "What do you want to tell them?",
};

const blankAudience = () => ({ tiers: ["A", "B", "C"], quiet_days: 0, include_leads: false, lead_days: 30 });
const clamp = (v) => Math.max(0, Math.min(1000, Math.round(Number(v) || 0)));
const cleanAudience = (a) => ({ tiers: a.tiers, quiet_days: clamp(a.quiet_days), include_leads: !!a.include_leads, lead_days: clamp(a.lead_days) });
const hasLink = (t) => /https?:\/\/|www\./i.test(t);

function audienceText(a = {}) {
  const tiers = a.tiers?.length ? a.tiers : ["A", "B", "C"];
  let s = tiers.length === 3 ? "All your customers" : `Customer group${tiers.length > 1 ? "s" : ""} ${tiers.join(" and ")}`;
  if (a.quiet_days) s += ` who haven't bought in ${a.quiet_days} days`;
  if (a.include_leads) s += `, plus past leads older than ${a.lead_days} days`;
  return s;
}

/** The live count of who matches an audience, asked again a moment after each change. */
function useAudienceCount(audience) {
  const [res, setRes] = useState(null);
  const key = JSON.stringify(cleanAudience(audience));
  useEffect(() => {
    let live = true;
    const t = setTimeout(() => {
      api("/campaigns/preview", { method: "POST", body: JSON.parse(key) }).then((r) => live && setRes(r)).catch(() => live && setRes(null));
    }, 350);
    return () => { live = false; clearTimeout(t); };
  }, [key]);
  return res;
}

function AudienceForm({ value, onChange }) {
  const set = (patch) => onChange({ ...value, ...patch });
  const toggle = (t) => {
    const on = value.tiers.includes(t);
    if (on && value.tiers.length === 1) return; // at least one group
    set({ tiers: on ? value.tiers.filter((x) => x !== t) : [...value.tiers, t].sort() });
  };
  return (
    <div className="stack">
      <span className="label">Who gets it</span>
      <div className="g-checks">
        {TIERS.map(([k, label, hint]) => (
          <label key={k} className={`g-check ${value.tiers.includes(k) ? "on" : ""}`} title={hint}>
            <input type="checkbox" checked={value.tiers.includes(k)} onChange={() => toggle(k)} />{label}
          </label>
        ))}
      </div>
      <span className="hint">A = the customers who make up 70% of your sales, B = the next 20%, C = everyone else. Keep at least one group ticked.</span>
      <div className="g-inline small">
        <span>Only customers who haven't bought in</span>
        <Num min={0} max={1000} value={value.quiet_days} onChange={(v) => set({ quiet_days: v })} aria-label="Days since their last purchase" />
        <span>days <span className="muted">(0 = everyone)</span></span>
      </div>
      <label className={`g-check ${value.include_leads ? "on" : ""}`} style={{ justifySelf: "start" }}>
        <input type="checkbox" checked={value.include_leads} onChange={(e) => set({ include_leads: e.target.checked })} />
        Also past leads who never bought
      </label>
      {value.include_leads && (
        <div className="g-inline small">
          <span>Leads older than</span>
          <Num min={0} max={1000} value={value.lead_days} onChange={(v) => set({ lead_days: v })} aria-label="Lead age in days" />
          <span>days</span>
        </div>
      )}
    </div>
  );
}

function Reach({ preview }) {
  if (!preview) return <span className="small muted">Counting…</span>;
  return (
    <div className="stack" style={{ gap: 4 }}>
      <span><b style={{ fontFamily: "var(--head)", fontSize: 22 }}>{preview.count}</b> {preview.count === 1 ? "person matches" : "people match"}</span>
      {preview.count > 0
        ? <span className="small muted">{preview.sample.join(", ")}{preview.count > preview.sample.length ? "…" : ""}</span>
        : <span className="small muted">Nobody yet. Import your customer list on the Customers page, or choose more groups.</span>}
      {preview.count > DAILY_CAP && <span className="small muted">At {DAILY_CAP} a day, this takes about {Math.ceil(preview.count / DAILY_CAP)} days.</span>}
    </div>
  );
}

function NewCampaign({ onSaved, onCancel }) {
  const { refresh, toast } = useHub();
  const [kind, setKind] = useState("revival");
  const [notes, setNotes] = useState("");
  const [title, setTitle] = useState("");
  const [message, setMessage] = useState("");
  const [audience, setAudience] = useState(blankAudience());
  const [err, setErr] = useState("");
  const preview = useAudienceCount(audience);
  const ready = title.trim().length >= 3 && message.trim().length >= 10;
  return (
    <div className="panel active">
      <span className="label">New campaign</span>
      <div className="grid2">
        <Field label="What kind" hint={KINDS[kind][1]}>
          <select className="select" value={kind} onChange={(e) => setKind(e.target.value)}>
            {Object.entries(KINDS).map(([k, [label]]) => <option key={k} value={k}>{label}</option>)}
          </select>
        </Field>
        <Field label="The offer or reason to write (optional)">
          <textarea className="textarea" rows={2} maxLength={600} value={notes} onChange={(e) => setNotes(e.target.value)} placeholder={NOTES[kind]} style={{ minHeight: 64 }} />
        </Field>
      </div>
      <div className="row">
        <Busy className="btn ai" onError={setErr} run={async () => {
          const d = await api("/campaigns/draft", { method: "POST", body: { kind, notes: notes.trim() } });
          setTitle(d.title);
          setMessage(d.message);
          refresh();
        }}>{message ? "Write it again" : "Write it for me"}</Busy>
        <span className="small muted">Uses one AI run. Then change anything you like.</span>
      </div>
      <Field label="Name (only you see this)">
        <input className="input" maxLength={80} value={title} onChange={(e) => setTitle(e.target.value)} placeholder="e.g. Diwali kitchen offer" />
      </Field>
      <Field label={`Message · ${message.length}/700`} hint="Your approved template adds each person's name and your business name around this. No links — plain messages are trusted more.">
        <textarea className="textarea" rows={5} maxLength={700} value={message} onChange={(e) => { setMessage(e.target.value); setErr(""); }} />
      </Field>
      {hasLink(message) && !err && <FormError msg="Take the link out — campaign messages can't include links." />}
      <AudienceForm value={audience} onChange={setAudience} />
      <Reach preview={preview} />
      <FormError msg={err} />
      <div className="row">
        <Busy className="btn primary" disabled={!ready} onError={setErr} run={async () => {
          await api("/campaigns", { method: "POST", body: { kind, title: title.trim(), message: message.trim(), audience: cleanAudience(audience) } });
          toast("Saved as a draft");
          onSaved();
        }}>Save as draft</Busy>
        <button className="btn ghost" onClick={onCancel}>Cancel</button>
      </div>
    </div>
  );
}

function ApproveModal({ c, preview, onClose, onDone }) {
  const { toast } = useHub();
  const n = preview.count;
  return (
    <Modal onClose={onClose}>
      <span className="kicker">Approve and start</span>
      <h2>Send “{c.title}” to <em>{n}</em> {n === 1 ? "person" : "people"}?</h2>
      <p className="sub">It goes from your own WhatsApp number as your approved template, between 10am and 7pm, at most {DAILY_CAP} a day
        {n > DAILY_CAP ? ` — about ${Math.ceil(n / DAILY_CAP)} days in all` : ""}. Each number gets it once. You can stop it any time.</p>
      <div className="prompt">{c.message}</div>
      <div className="row">
        <Busy className="btn primary" disabled={!n} run={async () => {
          await api(`/campaigns/${c.id}/approve`, { method: "POST" });
          toast("Approved. Sending starts in working hours.");
          onDone();
        }}>Approve and start sending</Busy>
        <button className="btn ghost" onClick={onClose}>Not now</button>
      </div>
      {!n && <p className="small muted">Nobody matches this audience yet. Import your customer list on the Customers page first.</p>}
    </Modal>
  );
}

function CampaignCard({ c, owner, connected, reload }) {
  const { toast } = useHub();
  const [confirm, setConfirm] = useState(null);
  const [label, kind] = STATUS[c.status] || [c.status, ""];
  const done = (c.sent || 0) + (c.failed || 0);
  const pct = c.recipients ? Math.min(100, Math.round((done / c.recipients) * 100)) : 0;
  return (
    <div className={`panel ${c.status === "running" ? "active" : ""}`}>
      <div className="row between" style={{ alignItems: "flex-start" }}>
        <div className="stack" style={{ gap: 4, minWidth: 0 }}>
          <h3 style={{ overflowWrap: "anywhere" }}>{c.title}</h3>
          <span className="small muted">{KINDS[c.kind]?.[0] || c.kind} · {day(c.created_at)}</span>
        </div>
        <Pill kind={kind}>{label}</Pill>
      </div>
      <div className="prompt">{c.message}</div>
      <span className="small muted">{audienceText(c.audience)}</span>
      {c.note && ((c.status === "running" && (!done || !connected)) || c.status === "stopped") && <p className="small" style={{ color: "var(--aqua)" }}>{c.note}</p>}
      {c.status !== "draft" && (
        <div className="stack" style={{ gap: 6 }}>
          <div className="meter" role="progressbar" aria-valuenow={pct} aria-valuemin={0} aria-valuemax={100} aria-label="Sent so far"><i style={{ width: `${pct}%` }} /></div>
          <span className="small">{c.sent || 0} sent{c.failed ? ` · ${c.failed} failed` : ""} · {c.recipients || 0} in all</span>
        </div>
      )}
      <div className="row">
        {c.status === "draft" && (owner
          ? <Busy className="btn primary" run={async () => setConfirm(await api("/campaigns/preview", { method: "POST", body: c.audience || {} }))}>Approve &amp; start</Busy>
          : <span className="small muted">Waiting for the owner to approve.</span>)}
        {c.status === "draft" && (
          <Busy className="btn ghost" run={async () => {
            if (!window.confirm(`Delete the draft “${c.title}”?`)) return;
            await api(`/campaigns/${c.id}`, { method: "DELETE" });
            toast("Deleted");
            reload();
          }}>Delete</Busy>
        )}
        {c.status === "running" && owner && (
          <Busy className="btn ghost" run={async () => {
            if (!window.confirm("Stop this campaign? Nobody else will get it.")) return;
            await api(`/campaigns/${c.id}/stop`, { method: "POST" });
            toast("Stopped");
            reload();
          }}>Stop</Busy>
        )}
      </div>
      {confirm && <ApproveModal c={c} preview={confirm} onClose={() => setConfirm(null)} onDone={() => { setConfirm(null); reload(); }} />}
    </div>
  );
}

export default function Campaigns() {
  const { me } = useHub();
  const [data, reload] = useLoad("/campaigns");
  const [adding, setAdding] = useState(false);
  const owner = canAct(me, "owner");
  const running = data?.campaigns.some((c) => c.status === "running");
  const title = running ? <>Your campaign is <em>sending</em>.</>
    : data?.this_month ? <>This month's campaign is <em>out</em>.</>
    : <>One campaign a month brings customers <em>back</em>.</>;
  return (
    <Sheet kicker="Scale · Money campaigns" id="GR-03" pillar="Scale" money title={title}
      sub={`Once a month, a message to your own past customers and leads — sent from your own WhatsApp number as an approved template, 10am–7pm, at most ${DAILY_CAP} a day. You approve it once; it runs.`}>
      {!data ? <Loading /> : (
        <>
          <div className="statgrid">
            <Stat label="This month" value={data.this_month} note={`${data.this_month === 1 ? "campaign" : "campaigns"} approved · one a month works best`} />
            <Stat label="Your WhatsApp number" value={data.connected ? "Ready" : "Not yet"}
              note={data.template ? `Campaign template: ${data.template}` : "No campaign template yet"} />
          </div>
          {(!data.connected || !data.template) && (
            <div className="panel flat">
              <span className="label">Before it can send</span>
              <p className="small">Our team connects your WhatsApp number and gets your campaign template approved (through your WhatsApp tool). You can write and
                approve a campaign now; sending starts once both are ready.</p>
              {owner && <div><Link className="btn sm" to="/connections">See connections</Link></div>}
            </div>
          )}
          {adding
            ? <NewCampaign onSaved={() => { setAdding(false); reload(); }} onCancel={() => setAdding(false)} />
            : <div><button className="btn primary" onClick={() => setAdding(true)}>Plan a campaign</button></div>}
          {!data.campaigns.length && !adding && (
            <Empty>No campaigns yet. Start with an old-customer revival — people who bought once are the easiest to sell to again.</Empty>
          )}
          {data.campaigns.map((c) => <CampaignCard key={c.id} c={c} owner={owner} connected={data.connected} reload={reload} />)}
        </>
      )}
    </Sheet>
  );
}
