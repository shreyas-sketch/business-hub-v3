import React from "react";
import { Link, Navigate, useParams } from "react-router-dom";
import { rs } from "../api.js";
import { Sheet, useHub, useLoad } from "../ui.jsx";

/* v2.3: one engine on a page — what it does for the business, its features (open, opening on a later event day, or part of
   Membership), and the owner's Engine Audit result for this engine when they have one. */

function AuditLine({ engine }) {
  const { me } = useHub();
  return me.features?.engine_audit && me.workspace?.role === "owner" ? <AuditResult engine={engine} /> : null;
}

function AuditResult({ engine }) {
  const [a] = useLoad("/audit");
  if (!a) return null;
  if (!a.latest) return <p className="small muted"><Link to="/audit">Take the Engine Audit</Link> to see what this engine is costing you today.</p>;
  const e = (a.latest.engines || []).find((x) => x.key === engine);
  if (!e) return null;
  return (
    <div className="panel">
      <span className="label">Your Engine Audit</span>
      <p>
        <b>{e.in_place ?? 0} of {e.total ?? 0}</b> in place.
        {e.cost_month ? <> Gaps cost about <span className="money">{rs(e.cost_month)}</span> a month</> : null}
        {e.hours_week ? <>{e.cost_month ? " and" : " Gaps take"} about <b>{Math.round(e.hours_week)} hours</b> a week</> : null}
        {e.cost_month || e.hours_week ? "." : ""}
      </p>
      <span className="small muted">{e.cost_month ? "Estimated from your own numbers." : "Typical hours for a small business."} <Link to="/audit">Open the audit</Link></span>
    </div>
  );
}

export default function Engine() {
  const { key } = useParams();
  const { me, nav } = useHub();
  const engines = me.engines || [];
  const eng = engines.find((e) => e.key === key);
  const section = (me.menu || []).find((g) => g.section === key);
  if (!eng) return <Navigate to={me.landing || "/dashboard"} replace />;
  const items = section?.items || [];
  return (
    <Sheet kicker={`${eng.label} engine`} id={`EN-0${engines.indexOf(eng) + 1}`} pillar={`${eng.label} engine`}
      title={<>The {eng.label.toLowerCase()} <em>engine</em>.</>} sub={eng.blurb}>
      <div className="engine-strip" aria-label="The 6 engines">
        {engines.map((e) => (
          <Link key={e.key} to={`/engine/${e.key}`} className={`${e.key === key ? "on" : ""} ${e.open ? "" : "shut"}`}>
            <b>{e.label}</b><span className="small muted">{e.open ? "Open" : `Day ${e.day}`}</span>
          </Link>
        ))}
      </div>
      {!eng.open && (
        <div className="panel active">
          <span className="label">Opens on Day {eng.day}</span>
          <p>At the event, the engines open one day at a time. This one opens on Day {eng.day}. Members have every engine open from the start.</p>
        </div>
      )}
      <AuditLine engine={key} />
      <div className="engine-cards">
        {items.map((it) => (
          <div key={it.key} className="engine-card">
            <h3>{it.label}</h3>
            <p className="muted">{me.feature_info?.[it.key]?.what}</p>
            <div className="row">
              {!it.locked ? <button className="btn primary sm" onClick={() => nav(it.path)}>Open</button>
                : it.lock === "day" ? <span className="small muted">Opens on Day {it.opens_day}</span>
                : <button className="btn money sm" onClick={() => nav("/plans")}>Part of Membership</button>}
            </div>
          </div>
        ))}
        {!items.length && <p className="muted">Nothing in this engine is switched on right now.</p>}
      </div>
    </Sheet>
  );
}
