import React, { createContext, useCallback, useContext, useEffect, useRef, useState } from "react";
import { Link, useLocation } from "react-router-dom";
import { allPages, findPage } from "./shell.jsx";
import { api } from "./api.js";

export const HubCtx = createContext(null);
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

/** Every view: one title, one line under it, its actions. Where it sits shows in the top bar ("Systems / Website");
    the kicker only shows when the page isn't in the menu, or on a phone, where there's no top bar. */
export function Sheet({ kicker, title, sub, actions, children }) {
  const { me } = useHub() || {};
  const loc = useLocation();
  const page = me ? findPage(allPages(me), loc.pathname) : null;
  return (
    <div className="sheet">
      <div className="sheet-head">
        <div className="titles">
          {page ? <span className="kicker phone-only">{page.group}</span> : kicker && <span className="kicker">{kicker}</span>}
          <h1>{title}</h1>
          {sub && <p className="sub">{sub}</p>}
        </div>
        {actions && <div className="actions">{actions}</div>}
      </div>
      {children}
    </div>
  );
}

/** A button that runs an async action once at a time. Errors show as a toast, or through `onError` (shown next to the form). */
export function Busy({ run, children, className = "btn", onError, ...p }) {
  const [busy, setBusy] = useState(false);
  const { toast } = useHub();
  return (
    <button {...p} className={className} disabled={busy || p.disabled}
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
export function Gate({ feature, role = "staff", children, title, what, kicker = "Locked", id = "L-01" }) {
  const { me, upgrade } = useHub();
  if (!me) return null;
  if (!canAct(me, role)) {
    return <Sheet kicker={kicker} id={id} title={<>Ask the owner for <em>access</em>.</>} sub="This part of the hub is for the business owner or a manager." />;
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
