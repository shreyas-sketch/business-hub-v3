import React, { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { ago, api } from "../api.js";
import { Busy, Copy, Field, FormError, Modal, Sheet, Switch, useHub, useLoad } from "../ui.jsx";
import { Empty, Loading, Pill } from "../v2.jsx";
import "./admin.css";
import "./event.css";

/* Admin → Live event: open the link, set its caps and how long Membership features stay open, show the QR on the projector,
   and the speaker's live numbers — how many packs are built and saved, and how many people have done each demo. */

const AREAS = [["lead", "Lead"], ["sales", "Sales"], ["brand", "Brand"], ["operations", "Operations"], ["conversion", "Conversion"], ["data", "Data"]];
const STATUS = { queued: ["Waiting", "off"], rules: ["Reading", "warn"], ai_queued: ["Waiting for AI", "off"], ai: ["Writing", "warn"], done: ["Ready", "on"], failed: ["Failed", "warn"] };

function Preset() {
  const { toast } = useHub();
  const [s, reload] = useLoad("/admin/config/presets/membership-v22");
  if (!s) return null;
  return (
    <div className={`panel ${s.applied ? "" : "active"}`}>
      <div className="row between">
        <div className="stack" style={{ gap: 4 }}>
          <span className="label">Before the event · features in Membership</span>
          <span className="small">{s.applied ? "Done. Members get every feature in the guide, the funnel leak finder and the follow-ups." : "The ₹1,999 Membership promises these; today they sit in higher plans:"}</span>
          {!s.applied && <span className="small muted">{s.features.filter((f) => f.above_membership).map((f) => f.label).join(", ")}</span>}
        </div>
        {s.applied ? <Pill kind="on">Applied</Pill> : (
          <Busy className="btn primary" run={async () => { const r = await api("/admin/config/presets/membership-v22", { method: "POST" }); toast(`Moved ${r.moved.length} into Membership`); reload(); }}>
            Move them into Membership
          </Busy>
        )}
      </div>
      {s.applied && <p className="small muted">Undo one at a time from <Link to="/admin/features">Features &amp; plans</Link> → Reset to default.</p>}
    </div>
  );
}

/** For the event only: the agents above Membership, in one tap, and back again afterwards. */
function Extras() {
  const { toast, me } = useHub();
  const [s, reload] = useLoad("/admin/config/presets/event-extras");
  if (!s) return null;
  const tier = (t) => me?.tiers?.[t]?.name || t;
  const above = s.features.filter((f) => f.above_membership);
  return (
    <div className="panel">
      <div className="row between">
        <div className="stack" style={{ gap: 4 }}>
          <span className="label">For the event · extras in Membership</span>
          <span className="small">{s.applied
            ? "Every Membership account has Win-Back, Content & company research, WhatsApp chats and Lead Response — paying members too, until you move them back."
            : "Give every pack made in the room these too. They sit in higher plans today, so paying members on Membership get them as well while they're moved:"}</span>
          {!s.applied && <span className="small muted">{above.map((f) => `${f.label} (${tier(f.tier)})`).join(", ")}</span>}
          <span className="small muted">On Membership, WhatsApp AI replies and the instant reply stay off until each owner switches them on. Each owner still connects their own WhatsApp number.</span>
        </div>
        {s.applied
          ? <Busy className="btn sm ghost" run={async () => { const r = await api("/admin/config/presets/event-extras/undo", { method: "POST" }); toast(`Moved ${r.moved_back.length} back to their plans`); reload(); }}>Move them back</Busy>
          : <Busy className="btn primary" run={async () => { const r = await api("/admin/config/presets/event-extras", { method: "POST" }); toast(`Moved ${r.moved.length} into Membership`); reload(); }}>Move them into Membership</Busy>}
      </div>
    </div>
  );
}

function Settings({ e, reload }) {
  const { toast } = useHub();
  const [f, setF] = useState({ open: e.open, ai: e.ai, pack_cap: e.pack_cap, ai_cap: e.ai_cap, trial_until: e.trial_until || "" });
  const [err, setErr] = useState("");
  return (
    <div className="stack">
      <div className="row" style={{ gap: 24 }}>
        <Switch checked={f.open} label="Link open" onChange={(v) => setF({ ...f, open: v })} />
        <Switch checked={f.ai} label="AI before login" onChange={(v) => setF({ ...f, ai: v })} />
      </div>
      <div className="ev-settings">
        <Field label="Packs, at most"><input className="input" type="number" min={1} max={20000} value={f.pack_cap} onChange={(x) => setF({ ...f, pack_cap: x.target.value })} /></Field>
        <Field label="Packs with AI, at most"><input className="input" type="number" min={0} max={20000} value={f.ai_cap} onChange={(x) => setF({ ...f, ai_cap: x.target.value })} /></Field>
        <Field label="Membership features open until" hint="The last day of the event. Empty: none.">
          <input className="input" type="date" value={f.trial_until} onChange={(x) => setF({ ...f, trial_until: x.target.value })} />
        </Field>
      </div>
      <FormError msg={err} />
      <div className="row">
        <Busy className="btn primary" onError={setErr} run={async () => {
          await api(`/admin/event/${e.code}`, { method: "PUT", body: { open: f.open, ai: f.ai, pack_cap: Number(f.pack_cap), ai_cap: Number(f.ai_cap), trial_until: f.trial_until || null } });
          toast(f.open ? "Saved. The link is open." : "Saved. The link is closed."); reload();
        }}>Save</Busy>
        <span className="small muted">Built so far: {e.counters.packs} packs, {e.counters.ai} with AI.</span>
      </div>
    </div>
  );
}

function Live({ code }) {
  const [d, setD] = useState(null);
  const [err, setErr] = useState("");
  useEffect(() => {
    let stop = false;
    let t;
    const tick = async () => {
      try { const r = await api(`/admin/event/${code}/live`); if (!stop) { setD(r); setErr(""); } } catch (e) { if (!stop) setErr(e.message); }
      if (!stop) t = setTimeout(tick, 5000);
    };
    tick();
    return () => { stop = true; clearTimeout(t); };
  }, [code]);
  if (!d) return err ? <FormError msg={err} /> : <Loading />;
  const r = d.runs;
  return (
    <div className="stack ev-admin">
      <div className="ev-nums">
        <div className="ev-num"><span className="label">Packs started</span><b>{r.started}</b><span className="small muted">{r.waiting} waiting · {r.writing} being written</span></div>
        <div className="ev-num"><span className="label">Packs ready</span><b>{r.ready}</b><span className="small muted">{r.failed ? `${r.failed} failed` : "none failed"}</span></div>
        <div className="ev-num"><span className="label">Saved to a hub</span><b>{r.saved}</b><span className="small muted">{d.accounts} accounts in this cohort</span></div>
        <div className="ev-num"><span className="label">Paid members</span><b className="money">{d.members_paid}</b><span className="small muted">AI so far ₹{d.ai_cost_inr}</span></div>
      </div>
      <p className="small muted">
        First things in {d.seconds.first_things ?? "—"} s (middle pack) · everything in {d.seconds.everything_median ?? "—"} s, slowest {d.seconds.everything_slowest ?? "—"} s.
        {" "}Measured on this event's packs. Refreshes every 5 seconds.
      </p>
      <div className="ev-areas">
        {AREAS.map(([k, label]) => (
          <div key={k} className="ev-area"><span className="label">{label}</span>
            <ul>{Object.entries(d.demos[k] || {}).map(([name, n]) => <li key={name}><span>{name}</span><b>{n}</b></li>)}</ul>
          </div>
        ))}
      </div>
    </div>
  );
}

function Runs({ code }) {
  const { toast } = useHub();
  const [filter, setFilter] = useState("");
  const [rows, reload] = useLoad(`/admin/event/${code}/runs?status=${filter}`, [filter]);
  return (
    <div className="stack">
      <div className="row">
        {[["", "All"], ["failed", "Failed"], ["ai_queued", "Waiting for AI"], ["done", "Ready"]].map(([v, l]) => (
          <button key={v} className={`btn sm ${filter === v ? "primary" : "ghost"}`} onClick={() => setFilter(v)}>{l}</button>
        ))}
        <button className="btn sm ghost" onClick={reload}>Refresh</button>
      </div>
      {!rows ? <Loading /> : !rows.length ? <Empty>No packs yet.</Empty> : (
        <table className="t cards">
          <thead><tr><th>Business</th><th>Status</th><th>AI</th><th>Saved</th><th>Seconds</th><th>Started</th><th /></tr></thead>
          <tbody>{rows.slice(0, 200).map((x) => (
            <tr key={x.id}>
              <td><b>{x.business || "—"}</b><div className="small muted">{x.website || "no website"}</div>{x.error && <div className="small" style={{ color: "var(--amber)" }}>{x.error}</div>}</td>
              <td data-l="Status"><Pill kind={STATUS[x.status]?.[1]}>{STATUS[x.status]?.[0] || x.status}</Pill></td>
              <td data-l="AI">{x.ai ? `₹${x.ai_cost_inr}` : "No"}</td>
              <td data-l="Saved">{x.saved ? "Yes" : "—"}</td>
              <td data-l="Seconds">{x.seconds ?? "—"}</td>
              <td data-l="Started" className="small muted">{ago(x.created_at)}</td>
              <td>{x.status === "failed" && <Busy className="btn sm" run={async () => { await api(`/admin/event/runs/${x.id}/retry`, { method: "POST" }); toast("Back in the queue"); reload(); }}>Try again</Busy>}</td>
            </tr>))}
          </tbody>
        </table>
      )}
    </div>
  );
}

export default function AdminEvent() {
  const [list, reload] = useLoad("/admin/event");
  const [open, setOpen] = useState(null);
  const [tab, setTab] = useState("live");
  const [qr, setQr] = useState(null);
  const e = (list || []).find((x) => x.code === open) || (list || [])[0];
  return (
    <Sheet kicker="Admin · Live event" id="A-08" pillar="Admin" title={<>The room, <em>live</em>.</>}
      sub="Open the event link, put its QR on the projector, and watch packs being built and each demo being done.">
      <Preset />
      <Extras />
      {!list ? <Loading /> : !list.length ? <Empty>Create a cohort first in <Link to="/admin">Admin</Link> (code, name, date). Each cohort can be an event.</Empty> : (
        <>
          <div className="row">
            {list.map((x) => (
              <button key={x.code} className={`btn sm ${e?.code === x.code ? "primary" : "ghost"}`} onClick={() => setOpen(x.code)}>
                {x.code}{x.open ? " · open" : ""}
              </button>
            ))}
          </div>
          {e && (
            <div className="panel">
              <div className="row between">
                <div className="stack" style={{ gap: 4 }}><b>{e.name || e.code}</b><span className="small mono">{e.link}</span></div>
                <div className="row"><Copy text={e.link} label="Copy link" /><button className="btn sm" onClick={() => setQr(e)}>Show the QR</button></div>
              </div>
              <Settings key={e.code + String(e.open) + e.pack_cap + e.ai_cap + (e.trial_until || "")} e={e} reload={reload} />
            </div>
          )}
          {e && (
            <>
              <div className="tabs">{[["live", "Live numbers"], ["runs", "Packs"]].map(([k, l]) => <button key={k} className={tab === k ? "on" : ""} onClick={() => setTab(k)}>{l}</button>)}</div>
              {tab === "live" ? <Live code={e.code} /> : <Runs code={e.code} />}
            </>
          )}
        </>
      )}
      {qr && (
        <Modal onClose={() => setQr(null)}>
          <div className="ev-qr">
            <span className="kicker">Scan to build your pack</span>
            <img src={`/api/admin/event/${qr.code}/qr.png`} alt={`QR code for ${qr.link}`} />
            <span className="mono">{qr.link.replace(/^https?:\/\//, "")}</span>
            <button className="btn ghost" onClick={() => setQr(null)}>Close</button>
          </div>
        </Modal>
      )}
    </Sheet>
  );
}
