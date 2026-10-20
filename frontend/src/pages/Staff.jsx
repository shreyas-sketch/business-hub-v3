import React, { useState } from "react";
import { Link } from "react-router-dom";
import { ago, api, localPhone } from "../api.js";
import { Sheet, Switch, canAct, useHub, useLoad } from "../ui.jsx";
import { Empty, Loading, Pill } from "../v2.jsx";
import "./running.css";

const NEED = { whatsapp: "your WhatsApp number", voice: "the AI Telecaller (ElevenLabs)", employz: "Employz.ai" };
const CONN = [["whatsapp", "WhatsApp number"], ["voice", "AI Telecaller"], ["employz", "Employz.ai"]];
const PAGE = {
  "/chats": ["Chats", "whatsapp_ai"], "/pipeline": ["Pipeline", "pipeline"], "/calendar": ["Content calendar", "content_calendar"],
  "/quotes": ["Quotations", "quotations"], "/money": ["Money owed", "money_owed"], "/customers": ["Customers", "customers"],
  "/dashboard": ["Dashboard", "smart_dashboard"], "/reviews": ["Monthly reviews", "monthly_review"],
};
const CALL = { calling: ["Calling", "on"], done: ["Done", "on"], failed: ["Didn't connect", "off"] };
const mins = (s) => (s ? `${Math.floor(s / 60)}:${String(Math.round(s % 60)).padStart(2, "0")}` : "—");

function StaffCard({ s, conns, reload }) {
  const { me, toast } = useHub();
  const [on, setOn] = useState(s.on);
  const owner = canAct(me, "owner");
  const flip = async (v) => {
    setOn(v);
    try {
      await api(`/staff/${s.key}`, { method: "PATCH", body: { on: v } });
      toast(`${s.title} switched ${v ? "on" : "off"}`);
      reload();
    } catch (e) {
      setOn(!v);
      if (!e.handled) toast(e.message, "err");
    }
  };
  const missing = s.needs && !conns[s.needs];
  const links = (s.pages || []).filter((p) => PAGE[p] && me.features?.[PAGE[p][1]]);
  return (
    <div className={`panel ${s.live ? "active" : ""}`}>
      <div className="row between">
        <h3>{s.title}</h3>
        <Pill kind={s.live ? "on" : "off"}>{s.live ? "Working" : !s.on ? "Switched off" : "Not live yet"}</Pill>
      </div>
      <p className="small">{s.what}</p>
      {!s.live && s.on && (
        <p className="small muted">
          {missing ? <>Starts once {NEED[s.needs]} is connected — the team does this in month {s.month} of your setup.</>
            : <>Goes live in month {s.month} of your setup, when the team finishes that step.</>}
          {owner && missing && me.features?.connections && <> <Link to="/connections">Connections</Link></>}
          {owner && !missing && me.features?.setup_tracker && <> <Link to="/setup">Your setup</Link></>}
        </p>
      )}
      <div className="row between">
        <Switch checked={on} disabled={!owner} onChange={flip} label={on ? "On" : "Off"} />
        <span className="small muted">{s.actions_week} {s.actions_week === 1 ? "action" : "actions"} this week</span>
      </div>
      {!owner && <span className="hint">Only the business owner can switch AI staff on or off.</span>}
      {s.recent?.length > 0 ? (
        <ul className="rb-log" aria-label={`${s.title}: recent activity`}>
          {s.recent.map((e, i) => <li key={i} className={e.status}><span>{e.text} <span className="muted">· {ago(e.at)}</span></span></li>)}
        </ul>
      ) : <span className="small muted">No activity yet.</span>}
      {links.length > 0 && <div className="row small" style={{ gap: 14 }}>{links.map((p) => <Link key={p} to={p}>{PAGE[p][0]}</Link>)}</div>}
    </div>
  );
}

function Calls({ calls }) {
  if (!calls.length) return <Empty>No calls yet. Once the AI Telecaller is connected, it calls every new lead within minutes, during your calling hours.</Empty>;
  return (
    <table className="t cards">
      <thead><tr><th>Lead</th><th>Status</th><th>Length</th><th>Score</th><th>What happened</th></tr></thead>
      <tbody>{calls.map((c) => {
        const [label, kind] = CALL[c.status] || [c.status || "—", ""];
        return (
          <tr key={c.id}>
            <td><b>{c.name || "Lead"}</b>{c.to && <div className="small muted mono">{localPhone(c.to)}</div>}<div className="small muted">{ago(c.started_at)}{c.kind === "recall" ? " · re-call" : ""}</div></td>
            <td data-l="Status"><Pill kind={kind}>{label}</Pill></td>
            <td data-l="Length" className="small">{mins(c.duration)}</td>
            <td data-l="Score" className="small">{c.score?.overall != null ? <span><b style={{ color: "var(--aqua)" }}>{c.score.overall}</b><span className="muted">/10</span></span> : "—"}</td>
            <td className="small" style={{ maxWidth: 380 }}>{c.summary || (c.error ? <span className="muted">{c.error}</span> : <span className="muted">The summary arrives when the call ends.</span>)}</td>
          </tr>
        );
      })}</tbody>
    </table>
  );
}

export default function Staff() {
  const { me } = useHub();
  const [d, reload] = useLoad("/staff");
  const live = (d?.staff || []).filter((s) => s.live).length;
  const title = !d ? <>Your AI staff, at <em>work</em>.</> : <>{live} of {d.staff.length} AI staff <em>working</em>.</>;
  return (
    <Sheet kicker="AI team · AI staff" id="GM-03" pillar="AI team" title={title}
      sub="Six AI staff who reply, call, post, chase payments, look after customers and keep you briefed. Each goes live during your six-month setup, and each has one switch.">
      {!d ? <Loading /> : (
        <>
          <div className="row" style={{ gap: 10 }}>
            {CONN.map(([k, l]) => <Pill key={k} kind={d.connections?.[k] ? "on" : "off"}>{l} · {d.connections?.[k] ? "connected" : "not connected"}</Pill>)}
            {canAct(me, "owner") && me.features?.connections && <Link className="small" to="/connections">Manage connections</Link>}
          </div>
          <div className="rb-staff">{d.staff.map((s) => <StaffCard key={`${s.key}:${s.on}`} s={s} conns={d.connections || {}} reload={reload} />)}</div>
          <div className="stack">
            <span className="label">AI Telecaller · recent calls</span>
            <Calls calls={d.calls || []} />
          </div>
        </>
      )}
    </Sheet>
  );
}
