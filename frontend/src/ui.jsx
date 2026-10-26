import React, { createContext, useCallback, useContext, useEffect, useRef, useState } from "react";
import { Link, useLocation } from "react-router-dom";
import { allPages, findPage } from "./shell.jsx";
import { api, rupees } from "./api.js";

export const HubCtx = createContext(null);

/** The OwnerDesk logo (logo kit: Horizontal Dark Flat; the monogram alone with mark). Flat files, because it shows under 64px. */
export const Logo = ({ h = 36, mark = false, className = "" }) => (
  <img className={`logo ${mark ? "logo-mark" : ""} ${className}`} src={mark ? "/brand/ownerdesk-mark.svg" : "/brand/ownerdesk-logo.svg"} alt="OwnerDesk" height={h} />
);
export const useHub = () => useContext(HubCtx);

export function useLoad(path, deps = []) {
  const [data, setData] = useState(null);
  const [error, setError] = useState(null);
  // Only the newest request may update the screen: a slow answer for an older search must not overwrite a newer one.
  const latest = useRef(path);
  latest.current = path;
  const reload = useCallback(() => api(path)
    .then((d) => { if (latest.current === path) { setData(d); setError(null); } return d; })
    .catch((e) => { if (latest.current === path) setError(e); }), [path]);
  useEffect(() => { reload(); }, [reload, ...deps]);
  return [data, reload, error];
}

/** v2.3: the engine (menu section) and page name for the page on screen, from the server's menu. */
export function useEnginePlace() {
  const { me } = useHub() || {};
  const loc = useLocation();
  if (me?.ui?.menu !== "engines") return null;
  const path = loc.pathname;
  for (const g of me.menu || []) {
    for (const it of g.items) {
      if (path === it.path || path.startsWith(`${it.path}/`)) return { group: g.group, section: g.section, label: it.label };
    }
  }
  return null;
}

/** Every view: the kicker (the page's menu section — its engine, with the engines menu), one headline with one gold phrase,
    one line under it, its actions, and a footer rule with the program's names. Where it sits also shows in the top bar. */
export function Sheet({ kicker, title, sub, actions, children }) {
  const { me } = useHub() || {};
  const loc = useLocation();
  const place = useEnginePlace();
  const page = me && !place ? findPage(allPages(me), loc.pathname) : null;
  if (place) kicker = place.group;
  return (
    <div className="sheet">
      <div className="sheet-head">
        <div className="titles">
          {(page?.group || kicker) && <span className="kicker">{page ? page.group : kicker}</span>}
          <h1>{title}</h1>
          {sub && <p className="sub">{sub}</p>}
        </div>
        {actions && <div className="actions">{actions}</div>}
      </div>
      {children}
      <footer className="sheet-foot">
        <span>{me?.program?.speakers || "Akshat Dani · Chirag J."}</span>
        <span>{me?.program?.name || "OwnerDesk"}</span>
      </footer>
    </div>
  );
}

/** A button that runs an async action once at a time. Errors show as a toast, or through `onError` (shown next to the form). */
export function Busy({ run, children, className = "btn", onError, ...p }) {
  const [busy, setBusy] = useState(false);
  const { toast } = useHub();
  return (
    <button {...p} className={busy ? `${className} is-busy` : className} disabled={busy || p.disabled}
      onClick={async () => {
        setBusy(true);
        try { await run(); if (onError) onError(""); }
        catch (e) { if (!e.handled) { if (onError) onError(e.message); else toast(e.message, "err"); } }
        finally { setBusy(false); }
      }}>
      {busy ? "Working…" : children}
    </button>
  );
}

export const FormError = ({ msg }) => (msg ? <p className="form-err" role="alert">{msg}</p> : null);

export const Field = ({ label, hint, children }) => (
  <label className="field"><span className="label">{label}</span>{children}{hint && <span className="hint">{hint}</span>}</label>
);

export function Modal({ onClose, children }) {
  useEffect(() => { const k = (e) => e.key === "Escape" && onClose(); window.addEventListener("keydown", k); return () => window.removeEventListener("keydown", k); }, [onClose]);
  return <><div className="scrim" onClick={onClose} /><div className="modal" role="dialog" aria-modal="true">{children}</div></>;
}

export function Copy({ text, label = "Copy", className = "btn sm" }) {
  const { toast } = useHub();
  const copy = async () => {
    try { await navigator.clipboard.writeText(text); toast("Copied"); }
    catch { window.prompt("Copy this:", text); }  // older phones / blocked clipboard
  };
  return <button className={className} onClick={copy}>{label}</button>;
}

/** A locked feature shows the tier that unlocks it. Never pretends to work. */
export function Locked({ feature, what }) {
  const { me, upgrade } = useHub();
  if (!me || me.features?.[feature]) return null;
  const tier = me.feature_tiers?.[feature] || "lite";
  const t = me.tiers?.[tier] || { name: "Membership" };
  return (
    <div className="row between panel flat" style={{ padding: "12px 16px" }}>
      <span className="small muted">{what} — unlocks with <b style={{ color: "var(--paper)" }}>{t.name}</b></span>
      {me.workspace?.role === "owner" && <button className="btn sm money" onClick={() => upgrade(tier, feature)}>See {t.name}</button>}
    </div>
  );
}

const ROLE_RANK = { staff: 0, manager: 1, owner: 2 };
export const canAct = (me, role = "staff") => ROLE_RANK[me?.workspace?.role || "owner"] >= ROLE_RANK[role];

/** Wraps a whole page: shows what the feature does and which plan unlocks it, or a role message for team members. */
const ENGINE_OF_DAY = { 1: "Lead and Sales", 2: "Conversion and Operations", 3: "Brand and Data" };

/** v2.3: how to join OwnerDesk Membership — both prices and the way to Plans & billing. */
export function JoinPanel({ note }) {
  const { me, nav } = useHub();
  const m = me?.membership || {};
  return (
    <div className="panel active">
      <span className="label">{m.name || "OwnerDesk Membership"}</span>
      <div className="row between" style={{ flexWrap: "wrap" }}>
        <span style={{ fontFamily: "var(--head)", fontSize: 24 }} className="money">
          {rupees(m.price_minor)}<span className="small">/month</span>
          {m.price_year_minor ? <span className="small muted"> · or {rupees(m.price_year_minor)}/year</span> : null}
        </span>
        {me.workspace?.role === "owner" ? <button className="btn money" onClick={() => nav("/plans")}>How to join</button>
          : <span className="small muted">Ask the business owner.</span>}
      </div>
      {note && <p className="small muted">{note}</p>}
    </div>
  );
}

export function Gate({ feature, role = "staff", children, title, what, kicker = "Locked", id = "L-01" }) {
  const { me, upgrade } = useHub();
  if (!me) return null;
  if (!canAct(me, role)) {
    return <Sheet kicker={kicker} id={id} title={<>Ask the owner for <em>access</em>.</>} sub="This part of the hub is for the business owner or a manager." />;
  }
  const why = feature && !me.features?.[feature] ? me.feature_locks?.[feature] : null;
  if (why?.lock === "day") {   // v2.3: a later day of the live event
    return (
      <Sheet kicker={kicker} id={id} title={<>This opens on <em>Day {why.opens_day}</em>.</>}
        sub={`At the event, the engines open one day at a time. Day ${why.opens_day} opens the ${ENGINE_OF_DAY[why.opens_day] || "next"} engines.`}>
        {what && <p className="muted">{what}</p>}
        <p className="small muted">Members of OwnerDesk have every engine open from the start.</p>
      </Sheet>
    );
  }
  if (feature && !me.features?.[feature] && (why?.lock === "paid" || (me.ui?.menu === "engines" && me.feature_info?.[feature]?.on !== false))) {   // v2.3: one paid plan
    return (
      <Sheet kicker={kicker} id={id} money title={title || <>Part of <em>OwnerDesk</em> Membership.</>} sub={what}>
        <JoinPanel note={why?.lock === "paid" ? "This opens once you join — it isn't part of the event access." : undefined} />
      </Sheet>
    );
  }
  if (feature && !me.features?.[feature]) {
    const tier = me.feature_tiers?.[feature] || "lite";
    const t = me.tiers?.[tier] || {};
    return (
      <Sheet kicker={kicker} id={id} money title={title || <>Unlock this with <em>{t.name}</em>.</>} sub={what}>
        <div className="panel active">
          <span className="label">Included in {t.name}</span>
          <div className="row between">
            <span style={{ fontFamily: "var(--head)", fontSize: 26 }} className="money">{t.price_minor ? `₹${new Intl.NumberFormat("en-IN").format(t.price_minor / 100)}` : ""}{t.period ? <span className="small">/{t.period}</span> : ""}</span>
            {me.workspace?.role === "owner" ? <button className="btn money" onClick={() => upgrade(tier, feature)}>See {t.name}</button>
              : <span className="small muted">Ask the business owner to upgrade.</span>}
          </div>
        </div>
      </Sheet>
    );
  }
  return children;
}

export function Switch({ checked, onChange, label, disabled }) {
  return (
    <label className="switch"><input type="checkbox" checked={!!checked} disabled={disabled} onChange={(e) => onChange(e.target.checked)} />{label}</label>
  );
}

export const Stat = ({ label, value, note, money }) => (
  <div className="stat"><span className="label">{label}</span><span className={`num ${money ? "money" : ""}`}>{value}</span>{note && <span className="small muted">{note}</span>}</div>
);

/** The quiet referral line under each output (asks only here and at limits). */
export function InviteLine() {
  const { me } = useHub();
  if (!me) return null;
  return (
    <p className="small muted">Know an owner who'd use this? <Link to="/invite">Share your invite link</Link> — you get extra AI runs when their website goes live.</p>
  );
}
