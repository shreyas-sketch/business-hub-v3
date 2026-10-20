import React, { useCallback, useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { ago, api, day, inr, localPhone, rupees } from "../api.js";
import { Busy, Field, FormError, Modal, Sheet, Switch, useHub } from "../ui.jsx";
import { Empty, Loading, Num, Pill, Tabs } from "../v2.jsx";
import "./admin.css";
import "./admin-widgets.css";

const ROLE_NAMES = { staff: "everyone in the team", manager: "managers and the owner", owner: "only the owner" };
const FIELD_NAMES = { tier: "plan", label: "name", what: "description", on: "on/off", teaser: "show when locked", group: "menu group", order: "menu position" };
const PLAN_FIELDS = { name: "name", short: "short name", price_minor: "price", runs: "AI runs", team_size: "team size", access_days: "days of access", pitch: "pitch",
  promise: "promise", razorpay_plan_id: "Razorpay plan id" };
const BILLING = { free: "Free", subscription: "Monthly subscription", one_time: "Paid once" };

const isoDay = (d) => d.toLocaleDateString("en-CA"); // YYYY-MM-DD in local time
const inDays = (n) => isoDay(new Date(Date.now() + n * 86400000));

export const LockIcon = () => (
  <svg className="fcard-lock" width="12" height="14" viewBox="0 0 12 14" role="img" aria-label="Part of every plan">
    <rect x="1" y="6" width="10" height="7" fill="none" stroke="currentColor" />
    <path d="M3.5 6V4a2.5 2.5 0 0 1 5 0v2" fill="none" stroke="currentColor" />
  </svg>
);

/** Give one owner one feature until a date, without changing their plan. Used here (Grants) and on the owner's page. */
export function GrantAdd({ userId, features, tierNames, onDone }) {
  const { toast } = useHub();
  const [owner, setOwner] = useState(null);
  const [q, setQ] = useState("");
  const [found, setFound] = useState(null);
  const [f, setF] = useState({ feature: "", until: inDays(30), note: "" });
  const [err, setErr] = useState("");
  useEffect(() => {
    if (userId || q.trim().length < 2) { setFound(null); return undefined; }
    const t = setTimeout(() => api(`/admin/users?q=${encodeURIComponent(q.trim())}&limit=8`)
      .then((list) => setFound(list.filter((u) => !u.team))).catch((e) => setErr(e.message)), 300);
    return () => clearTimeout(t);
  }, [q, userId]);
  const days = f.until ? Math.ceil((new Date(`${f.until}T23:59:59`) - Date.now()) / 86400000) : 0;
  const byTier = Object.keys(tierNames).map((t) => [t, features.filter((x) => x.tier === t)]).filter(([, l]) => l.length);
  const who = userId || owner?.id;
  return (
    <div className="panel active">
      <span className="label">Give a feature to one owner</span>
      {!userId && (owner ? (
        <div className="picked">
          <span><b>{owner.business || "No business name yet"}</b> <span className="small muted mono">{localPhone(owner.phone)} · {tierNames[owner.effective_plan] || owner.effective_plan}</span></span>
          <button className="btn sm ghost" onClick={() => { setOwner(null); setQ(""); }}>Change owner</button>
        </div>
      ) : (
        <Field label="Owner" hint="Search by phone number, business name or workshop cohort code.">
          <input className="input" value={q} onChange={(e) => setQ(e.target.value)} placeholder="98200 12345 or Sharma Kitchens" />
          {found && (found.length ? (
            <div className="listbox">{found.map((u) => (
              <button key={u.id} type="button" onClick={() => { setOwner(u); setFound(null); }}>
                <b>{u.business || "No business name yet"}</b><span className="small mono">{localPhone(u.phone)} · {tierNames[u.effective_plan] || u.effective_plan}</span>
              </button>))}
            </div>
          ) : <span className="hint">No owner matches that.</span>)}
        </Field>
      ))}
      <div className="grid3 fields-top">
        <Field label="Feature">
          <select className="select" value={f.feature} onChange={(e) => setF({ ...f, feature: e.target.value })}>
            <option value="">Choose a feature…</option>
            {byTier.map(([t, list]) => (
              <optgroup key={t} label={tierNames[t]}>{list.map((x) => <option key={x.key} value={x.key}>{x.label}</option>)}</optgroup>
            ))}
          </select>
        </Field>
        <Field label="Until" hint={days > 0 ? `${days} ${days === 1 ? "day" : "days"} from today` : "Pick a date after today"}>
          <input className="input" type="date" min={inDays(1)} value={f.until} onChange={(e) => setF({ ...f, until: e.target.value })} />
        </Field>
        <Field label="Note (optional)"><input className="input" maxLength={200} value={f.note} onChange={(e) => setF({ ...f, note: e.target.value })} placeholder="Workshop winner" /></Field>
      </div>
      <FormError msg={err} />
      <div>
        <Busy className="btn primary" disabled={!who || !f.feature || days < 1 || days > 3650} onError={setErr} run={async () => {
          const r = await api("/admin/config/grants", { method: "POST", body: { user_id: who, feature: f.feature, days, note: f.note.trim() } });
          toast(`Given until ${day(r.until)}. The owner gets a notice in their hub.`);
          setF({ feature: "", until: inDays(30), note: "" });
          onDone?.();
        }}>Give this feature</Busy>
      </div>
    </div>
  );
}

/* ═════════════ Plan board ═════════════ */
function FeatureCard({ f, tiers, dragging, onDrag, onPatch, onMove, onEdit }) {
  const diff = f.changed.filter((k) => k !== "order");
  return (
    <article className={`fcard ${f.on ? "" : "off"} ${f.core ? "core" : ""} ${dragging ? "dragging" : ""}`} draggable={!f.core}
      onDragStart={(e) => { e.dataTransfer.setData("text/plain", f.key); e.dataTransfer.effectAllowed = "move"; onDrag(f.key); }}
      onDragEnd={() => onDrag(null)}>
      <div className="fcard-head">
        {f.core ? <LockIcon /> : <span className="fcard-grip" aria-hidden="true">⠿</span>}
        <b>{f.label}</b>
      </div>
      <span className="fcard-meta">{f.group} · {f.path ? f.path : "inside a page"}{f.role !== "staff" ? ` · ${f.role}` : ""}</span>
      {f.core && <span className="fcard-meta">In every plan. Can't be moved or switched off.</span>}
      {diff.length > 0 && <span className="fcard-changed">Changed: {diff.map((k) => FIELD_NAMES[k] || k).join(", ")}</span>}
      <div className="fcard-switches">
        <Switch checked={f.on} disabled={f.core} label={f.on ? "On" : "Off for everyone"}
          onChange={(on) => onPatch(f, { on }, on ? `${f.label} is on again.` : `${f.label} is off for everyone.`)} />
        {f.path && f.tier !== "free" && (
          <Switch checked={f.teaser} label={f.teaser ? "Shown locked" : "Hidden when locked"}
            onChange={(teaser) => onPatch(f, { teaser }, teaser ? `${f.label} shows locked in lower plans' menus.` : `${f.label} is hidden from lower plans' menus.`)} />
        )}
      </div>
      <div className="fcard-actions">
        {!f.core && (
          <select className="select fcard-move" aria-label={`Move ${f.label} to another plan`} value={f.tier} onChange={(e) => onMove(f, e.target.value)}>
            {tiers.map((t) => <option key={t.tier} value={t.tier}>{t.tier === f.tier ? `In ${t.name}` : `Move to ${t.name}`}</option>)}
          </select>
        )}
        <button className="btn sm ghost" onClick={() => onEdit(f)} aria-label={`Edit ${f.label}`}>Edit</button>
      </div>
    </article>
  );
}

function EditFeature({ f, cfg, names, onClose, changed }) {
  const { toast } = useHub();
  const [v, setV] = useState({ label: f.label, what: f.what, group: f.group, tier: f.tier });
  const [err, setErr] = useState("");
  const body = {};
  if (v.label.trim() !== f.label) body.label = v.label.trim();
  if (v.what.trim() !== f.what) body.what = v.what.trim();
  if (v.group !== f.group) body.group = v.group;
  if (v.tier !== f.tier) body.tier = v.tier;
  return (
    <Modal onClose={onClose}>
      <span className="kicker">Edit feature</span>
      <h2>Edit <em>{f.label}</em></h2>
      <Field label="Name" hint={f.changed.includes("label") ? `Default name: ${f.default_label}` : "Shown in the menu, on Plans & billing and on locked pages."}>
        <input className="input" maxLength={60} value={v.label} onChange={(e) => setV({ ...v, label: e.target.value })} />
      </Field>
      <Field label="What it does" hint="One line in plain words. Owners see it under the page title and on Plans & billing.">
        <textarea className="textarea" rows={3} maxLength={240} value={v.what} onChange={(e) => setV({ ...v, what: e.target.value })} />
      </Field>
      <div className="grid2 fields-top">
        <Field label="Menu group">
          <select className="select" value={v.group} onChange={(e) => setV({ ...v, group: e.target.value })}>
            {cfg.groups.map((g) => <option key={g} value={g}>{g}</option>)}
          </select>
        </Field>
        <Field label="Plan" hint={f.core ? "Part of every plan." : f.tier !== f.default_tier ? `Default plan: ${names[f.default_tier]}` : undefined}>
          <select className="select" value={v.tier} disabled={f.core} onChange={(e) => setV({ ...v, tier: e.target.value })}>
            {cfg.tiers.map((t) => <option key={t.tier} value={t.tier}>{t.name}</option>)}
          </select>
        </Field>
      </div>
      <p className="small muted">
        Who can open it: {ROLE_NAMES[f.role] || f.role}. {f.path ? <>Page address: <span className="mono">{f.path}</span>.</> : "It works inside another page, so it has no menu item."}
      </p>
      <FormError msg={err} />
      <div className="row between">
        {f.changed.length ? (
          <Busy className="btn sm ghost" onError={setErr} run={async () => {
            await api(`/admin/config/features/${f.key}/reset`, { method: "POST" });
            toast(`${f.default_label} is back to its default.`);
            await changed();
            onClose();
          }}>Reset to default</Busy>
        ) : <span />}
        <div className="row">
          <button className="btn sm ghost" onClick={onClose}>Cancel</button>
          <Busy className="btn sm primary" disabled={!Object.keys(body).length} onError={setErr} run={async () => {
            await api(`/admin/config/features/${f.key}`, { method: "PATCH", body });
            toast("Saved. Owners see it at once.");
            await changed();
            onClose();
          }}>Save</Busy>
        </div>
      </div>
    </Modal>
  );
}

function Board({ cfg, setCfg, changed, names }) {
  const { toast } = useHub();
  const [q, setQ] = useState("");
  const [drag, setDrag] = useState(null);
  const [over, setOver] = useState(null);
  const [edit, setEdit] = useState(null);
  const patch = async (f, body, msg) => {
    setCfg((c) => ({ ...c, features: c.features.map((x) => (x.key === f.key ? { ...x, ...body } : x)) }));
    try {
      await api(`/admin/config/features/${f.key}`, { method: "PATCH", body });
      toast(msg);
    } catch (e) {
      if (!e.handled) toast(e.message, "err");
    }
    await changed();
  };
  const move = (f, tier) => {
    if (!f || tier === f.tier) return;
    if (f.core) { toast(`${f.label} is part of every plan and can't be moved.`, "err"); return; }
    patch(f, { tier }, `${f.label} moved to ${names[tier]}. Owners on ${names[tier]} and above have it now.`);
  };
  const needle = q.trim().toLowerCase();
  const order = (a, b) => cfg.groups.indexOf(a.group) - cfg.groups.indexOf(b.group) || a.order - b.order;
  const shown = cfg.features.filter((f) => !needle || `${f.label} ${f.what} ${f.group} ${f.path || ""}`.toLowerCase().includes(needle)).sort(order);
  const changedCount = cfg.features.filter((f) => f.changed.some((k) => k !== "order")).length;
  const offCount = cfg.features.filter((f) => !f.on).length;
  return (
    <div className="stack">
      <div className="row between">
        <p className="small muted" style={{ maxWidth: "62ch" }}>
          Drag a card to another plan to move it; owners on that plan and above get it at once. A dashed card is part of every plan.
          {" "}{changedCount} changed from default{offCount ? ` · ${offCount} switched off` : ""}.
        </p>
        <input className="input" style={{ maxWidth: 260 }} aria-label="Find a feature" placeholder="Find a feature" value={q} onChange={(e) => setQ(e.target.value)} />
      </div>
      <div className="plan-board">
        {cfg.tiers.map((t) => {
          const items = shown.filter((f) => f.tier === t.tier);
          const total = cfg.features.filter((f) => f.tier === t.tier).length;
          return (
            <section key={t.tier} aria-label={t.name} className={`plan-col ${over === t.tier ? "over" : ""}`}
              onDragOver={(e) => { if (drag) { e.preventDefault(); e.dataTransfer.dropEffect = "move"; if (over !== t.tier) setOver(t.tier); } }}
              onDragLeave={(e) => { if (!e.currentTarget.contains(e.relatedTarget)) setOver(null); }}
              onDrop={(e) => {
                e.preventDefault();
                const key = drag || e.dataTransfer.getData("text/plain");
                setOver(null); setDrag(null);
                move(cfg.features.find((x) => x.key === key), t.tier);
              }}>
              <div className="plan-col-head">
                <b>{t.name}</b>
                {t.price_minor ? <span className="small money">{rupees(t.price_minor)}{t.billing === "subscription" ? " a month" : ""}</span> : <span className="small muted">Given at the workshop</span>}
                <span className="small muted">{total} {total === 1 ? "feature" : "features"}{needle ? ` · ${items.length} match` : ""}</span>
              </div>
              {items.map((f) => (
                <FeatureCard key={f.key} f={f} tiers={cfg.tiers} dragging={drag === f.key} onDrag={setDrag} onPatch={patch} onMove={move} onEdit={setEdit} />
              ))}
              {!items.length && <div className="plan-col-drop">{needle ? "Nothing here matches." : "Drop a feature here."}</div>}
            </section>
          );
        })}
      </div>
      {edit && <EditFeature f={cfg.features.find((x) => x.key === edit.key) || edit} cfg={cfg} names={names} changed={changed} onClose={() => setEdit(null)} />}
    </div>
  );
}

/* ═════════════ Menu order ═════════════ */
function MenuOrder({ cfg, changed }) {
  const { toast } = useHub();
  const byKey = useMemo(() => Object.fromEntries(cfg.features.map((f) => [f.key, f])), [cfg]);
  const build = useCallback(() => Object.fromEntries(cfg.groups.map((g) => [g,
    cfg.features.filter((f) => f.path && f.group === g).sort((a, b) => a.order - b.order).map((f) => f.key)])), [cfg]);
  const [draft, setDraft] = useState(build);
  const [dirty, setDirty] = useState([]);
  const [drag, setDrag] = useState(null);
  const [over, setOver] = useState(null); // { group, index }
  useEffect(() => { setDraft(build()); setDirty([]); }, [build]);
  const short = Object.fromEntries(cfg.tiers.map((t) => [t.tier, t.short || t.name]));

  // Put `key` into `group` before position `index` of that group's current list.
  const place = (key, group, index) => {
    const from = cfg.groups.find((g) => draft[g].includes(key));
    if (!from) return;
    let at = index;
    if (from === group && draft[from].indexOf(key) < index) at -= 1;
    const next = { ...draft, [from]: draft[from].filter((k) => k !== key) };
    const list = [...next[group]];
    list.splice(Math.max(0, Math.min(at, list.length)), 0, key);
    if (from === group && list.join() === draft[group].join()) return; // dropped where it already was
    next[group] = list;
    setDraft(next);
    setDirty((d) => [...new Set([...d, from, group])]);
  };
  const step = (key, group, dir) => {
    const i = draft[group].indexOf(key);
    const gi = cfg.groups.indexOf(group);
    if (dir < 0) {
      if (i > 0) place(key, group, i - 1);
      else if (gi > 0) { const g = cfg.groups[gi - 1]; place(key, g, draft[g].length); }
    } else if (i < draft[group].length - 1) place(key, group, i + 2);
    else if (gi < cfg.groups.length - 1) place(key, cfg.groups[gi + 1], 0);
  };
  const first = cfg.groups.find((g) => draft[g].length);
  const last = [...cfg.groups].reverse().find((g) => draft[g].length);

  return (
    <div className="stack">
      <p className="small muted" style={{ maxWidth: "62ch" }}>
        The menu every owner sees. Drag a page to reorder it or move it to another group; on a phone, use the arrows.
        Pages switched off for everyone don't show in owners' menus.
      </p>
      <div className="menu-groups">
        {cfg.groups.map((g) => (
          <div key={g} className="menu-group">
            <div className="row between"><span className="label">{g}</span><span className="small muted">{draft[g].length}</span></div>
            <div className={`dropzone ${over?.group === g && over.index === draft[g].length ? "over" : ""}`}
              onDragOver={(e) => { if (drag) { e.preventDefault(); if (e.target === e.currentTarget) setOver({ group: g, index: draft[g].length }); } }}
              onDrop={(e) => { e.preventDefault(); if (drag && over) place(drag, over.group, over.index); setDrag(null); setOver(null); }}>
              {draft[g].map((key, i) => {
                const f = byKey[key];
                const pos = over?.group === g && (over.index === i ? "before" : over.index === i + 1 && i === draft[g].length - 1 ? "after" : "");
                return (
                  <div key={key} className={`mrow ${f.on ? "" : "off"} ${drag === key ? "dragging" : ""} ${pos || ""}`} draggable
                    onDragStart={(e) => { e.dataTransfer.setData("text/plain", key); e.dataTransfer.effectAllowed = "move"; setDrag(key); }}
                    onDragEnd={() => { setDrag(null); setOver(null); }}
                    onDragOver={(e) => {
                      if (!drag) return;
                      e.preventDefault();
                      const r = e.currentTarget.getBoundingClientRect();
                      const index = e.clientY > r.top + r.height / 2 ? i + 1 : i;
                      if (over?.group !== g || over.index !== index) setOver({ group: g, index });
                    }}>
                    <span className="fcard-grip" aria-hidden="true">⠿</span>
                    <span className="mrow-label">
                      <b>{f.label}</b>
                      <span>{f.tier === "free" ? "Every plan" : `From ${short[f.tier]}`}{f.on ? "" : " · off for everyone"}{f.teaser || f.tier === "free" ? "" : " · hidden when locked"}</span>
                    </span>
                    <span className="updown">
                      <button className="iconbtn" aria-label={`Move ${f.label} up`} disabled={g === first && i === 0} onClick={() => step(key, g, -1)}>↑</button>
                      <button className="iconbtn" aria-label={`Move ${f.label} down`} disabled={g === last && i === draft[g].length - 1} onClick={() => step(key, g, 1)}>↓</button>
                    </span>
                  </div>
                );
              })}
              {!draft[g].length && <span className="small muted" style={{ padding: 8 }}>Drop a page here.</span>}
            </div>
          </div>
        ))}
      </div>
      {dirty.length > 0 && (
        <div className="savebar">
          <span className="small">Menu changed in {dirty.join(", ")}. Owners see it after you save.</span>
          <div className="row">
            <button className="btn sm ghost" onClick={() => { setDraft(build()); setDirty([]); }}>Discard</button>
            <Busy className="btn sm primary" run={async () => {
              const r = await api("/admin/config/menu", { method: "PUT", body: { groups: Object.fromEntries(dirty.map((g) => [g, draft[g]])) } });
              toast(`Menu saved · ${r.moved} pages in place.`);
              await changed();
            }}>Save menu order</Busy>
          </div>
        </div>
      )}
    </div>
  );
}

/* ═════════════ Plans ═════════════ */
function PlanForm({ t, changed }) {
  const { toast } = useHub();
  const init = useCallback(() => ({ name: t.name, short: t.short || "", price: t.price_minor / 100, runs: t.runs, team_size: t.team_size, access_days: t.access_days ?? "",
    pitch: t.pitch || "", promise: t.promise || "", razorpay_plan_id: t.razorpay_plan_id || "" }), [t]);
  const [v, setV] = useState(init);
  const [err, setErr] = useState("");
  useEffect(() => { setV(init()); setErr(""); }, [init]);
  const set = (k) => (x) => setV({ ...v, [k]: x && x.target ? x.target.value : x });
  const int = (x) => Math.round(Number(x));
  const body = {};
  if (v.name.trim() !== t.name) body.name = v.name.trim();
  if (v.short.trim() && v.short.trim() !== (t.short || "")) body.short = v.short.trim();
  if (t.tier !== "free" && v.price !== "" && Math.round(Number(v.price) * 100) !== t.price_minor) body.price_minor = Math.round(Number(v.price) * 100);
  if (v.runs !== "" && int(v.runs) !== t.runs) body.runs = int(v.runs);
  if (v.team_size !== "" && int(v.team_size) !== t.team_size) body.team_size = int(v.team_size);
  if (t.billing === "one_time" && v.access_days !== "" && int(v.access_days) !== t.access_days) body.access_days = int(v.access_days);
  if (v.pitch.trim() !== (t.pitch || "")) body.pitch = v.pitch.trim();
  if (v.promise.trim() !== (t.promise || "")) body.promise = v.promise.trim();
  if (t.tier === "lite" && v.razorpay_plan_id.trim() !== (t.razorpay_plan_id || "")) body.razorpay_plan_id = v.razorpay_plan_id.trim();
  const dirty = Object.keys(body).length > 0;
  return (
    <div className={`panel ${dirty ? "active" : ""}`}>
      <div className="row between">
        <div className="stack" style={{ gap: 4 }}>
          <h3>{t.name}</h3>
          <span className="small muted">
            {[t.billing !== "free" && (BILLING[t.billing] || t.billing), t.period, t.short !== t.name && `short name “${t.short}”`, `${t.features} features`].filter(Boolean).join(" · ")}
          </span>
        </div>
        <span className={t.price_minor ? "money" : "muted"} style={{ fontFamily: "var(--head)", fontSize: 22 }}>{rupees(t.price_minor)}{t.billing === "subscription" ? <span className="small">/month</span> : null}</span>
      </div>
      {t.changed.length > 0 && <span className="small" style={{ color: "var(--aqua)" }}>Changed from default: {t.changed.map((k) => PLAN_FIELDS[k] || k).join(", ")}</span>}
      <div className="grid3 fields-top">
        <Field label="Name"><input className="input" maxLength={40} value={v.name} onChange={set("name")} /></Field>
        <Field label="Short name" hint="Shown on locked menu items."><input className="input" maxLength={24} value={v.short} onChange={set("short")} /></Field>
        <Field label="Price (₹)" hint={t.tier === "free" ? "The free plan stays free." : t.tier === "lite" ? "A new price needs a new Razorpay plan id below." : "New prices apply to new purchases."}>
          <Num min="0" step="1" value={v.price} disabled={t.tier === "free"} onChange={set("price")} />
        </Field>
        <Field label="AI runs a month"><Num min="0" step="1" value={v.runs} onChange={set("runs")} /></Field>
        <Field label="Team logins" hint="How many team members the owner can add."><Num min="0" max="500" step="1" value={v.team_size} onChange={set("team_size")} /></Field>
        {t.billing === "one_time" && (
          <Field label="Days of access" hint="How long one payment lasts."><Num min="1" max="3650" step="1" value={v.access_days} onChange={set("access_days")} /></Field>
        )}
        {t.tier === "lite" && (
          <Field label="Razorpay plan id" hint="Create the plan in Razorpay at the new price, then paste its id (plan_…).">
            <input className="input mono" maxLength={60} value={v.razorpay_plan_id} onChange={set("razorpay_plan_id")} placeholder="plan_XXXXXXXXXX" />
          </Field>
        )}
      </div>
      <div className="grid2 fields-top">
        <Field label="Pitch" hint="One line under the plan's name."><input className="input" maxLength={160} value={v.pitch} onChange={set("pitch")} /></Field>
        <Field label="Promise" hint="What the owner gets, in a few words."><input className="input" maxLength={160} value={v.promise} onChange={set("promise")} /></Field>
      </div>
      <FormError msg={err} />
      <div className="row between">
        {t.changed.length > 0 ? (
          <Busy className="btn sm ghost" onError={setErr} run={async () => {
            if (!window.confirm(`Put ${t.name} back to its default name, price and limits?`)) return;
            await api(`/admin/config/plans/${t.tier}/reset`, { method: "POST" });
            toast("Back to default.");
            await changed();
          }}>Reset to default</Busy>
        ) : <span />}
        <div className="row">
          {dirty && <button className="btn sm ghost" onClick={() => { setV(init()); setErr(""); }}>Discard</button>}
          <Busy className="btn sm primary" disabled={!dirty} onError={setErr} run={async () => {
            await api(`/admin/config/plans/${t.tier}`, { method: "PATCH", body });
            toast(`${body.name || t.name} saved.${body.price_minor !== undefined ? " The new price applies to new purchases." : ""}`);
            await changed();
          }}>Save changes</Busy>
        </div>
      </div>
    </div>
  );
}

/* ═════════════ Grants ═════════════ */
function Grants({ cfg, names, changed }) {
  const { toast } = useHub();
  const [list, setList] = useState(null);
  const [err, setErr] = useState("");
  const load = useCallback(() => api("/admin/config/grants").then((d) => { setList(d); setErr(""); }).catch((e) => setErr(e.message)), []);
  useEffect(() => { load(); }, [load]);
  const features = cfg.features.filter((f) => f.on && f.tier !== "free");
  return (
    <div className="stack">
      <p className="small muted" style={{ maxWidth: "62ch" }}>
        A grant gives one owner one feature until a date, without changing their plan. A feature switched off for everyone stays off, even with a grant.
      </p>
      <GrantAdd features={features} tierNames={names} onDone={async () => { await load(); await changed(); }} />
      <FormError msg={err} />
      {!list ? <Loading /> : !list.length ? <Empty>No grants yet. Give a feature to an owner above.</Empty> : (
        <table className="t cards">
          <thead><tr><th>Owner</th><th>Feature</th><th>Until</th><th>Status</th><th /></tr></thead>
          <tbody>{list.map((g) => (
            <tr key={`${g.user_id}:${g.feature}`}>
              <td><Link to={`/admin/owners/${g.user_id}`}><b>{g.business || "No business name yet"}</b></Link><div className="small muted mono">{localPhone(g.phone)}</div></td>
              <td data-l="Feature">{g.label}</td>
              <td data-l="Until">{day(g.until)}</td>
              <td data-l="Status">{g.active ? <Pill kind="on">Active</Pill> : <Pill kind="off">Ended</Pill>}</td>
              <td>
                <Busy className="btn sm ghost" run={async () => {
                  if (!window.confirm(`Take ${g.label} back from ${g.business || localPhone(g.phone)}?`)) return;
                  await api(`/admin/config/grants/${g.user_id}/${g.feature}`, { method: "DELETE" });
                  toast("Taken back.");
                  await load();
                }}>{g.active ? "Take back" : "Remove"}</Busy>
              </td>
            </tr>
          ))}</tbody>
        </table>
      )}
    </div>
  );
}

/* ═════════════ Change log ═════════════ */
function describe(c, names) {
  if (c.kind === "menu") return c.note === "Undo" ? "Menu order put back" : `Menu order saved${c.note ? ` (${c.note})` : ""}`;
  if (c.kind === "grant") return c.after ? `Given until ${day(c.after)}` : "Taken back";
  const b = c.before || {};
  const a = c.after || {};
  const keys = [...new Set([...Object.keys(b), ...Object.keys(a)])].filter((k) => JSON.stringify(b[k]) !== JSON.stringify(a[k]));
  if (!keys.length) return c.note === "Back to default" ? "Back to default (no changes to undo)" : "No change";
  if (!Object.keys(a).length && c.note === "Back to default") return "Back to default";
  return keys.map((k) => {
    const v = a[k];
    if (c.kind === "plan") {
      const n = PLAN_FIELDS[k] || k;
      if (v === undefined) return `${n} back to default`;
      return k === "price_minor" ? `${n} ${inr(v)}` : typeof v === "string" ? `${n} “${v}”` : `${n} ${v}`;
    }
    if (k === "on") return v === false ? "switched off" : "switched on";
    if (k === "teaser") return v === false ? "hidden when locked" : "shown when locked";
    if (v === undefined) return `${FIELD_NAMES[k] || k} back to default`;
    if (k === "tier") return `moved to ${names[v] || v}`;
    if (k === "label") return `renamed “${v}”`;
    if (k === "what") return "description changed";
    if (k === "group") return `menu group ${v}`;
    if (k === "order") return "menu position changed";
    return FIELD_NAMES[k] || k;
  }).join(" · ");
}

const KIND = { feature: "Feature", plan: "Plan", menu: "Menu", grant: "Grant" };

function ChangeLog({ names, changed }) {
  const { toast } = useHub();
  const [list, setList] = useState(null);
  const [err, setErr] = useState("");
  const load = useCallback(() => api("/admin/config/changes").then((d) => { setList(d); setErr(""); }).catch((e) => setErr(e.message)), []);
  useEffect(() => { load(); }, [load]);
  if (err) return <FormError msg={err} />;
  if (!list) return <Loading />;
  if (!list.length) return <Empty>No changes yet. Every move, switch, rename, price change and grant shows here, and each one can be undone.</Empty>;
  return (
    <div className="stack">
      <p className="small muted">The last {list.length} changes, newest first. Undo puts back what was there just before that change.</p>
      <table className="t cards">
        <thead><tr><th>When</th><th>What</th><th>Change</th><th>By</th><th /></tr></thead>
        <tbody>{list.map((c) => (
          <tr key={c.id} style={c.undone_at ? { opacity: 0.6 } : undefined}>
            <td data-l="When"><span title={new Date(c.at).toLocaleString("en-IN")}>{ago(c.at)}</span></td>
            <td><span className="small muted">{KIND[c.kind] || c.kind} · </span><b>{c.kind === "menu" ? "Menu order" : c.target_label}</b></td>
            <td data-l="Change">
              <span className="small">{describe(c, names)}</span>
              {c.note && !["Back to default", "Undo"].includes(c.note) && c.kind !== "menu" && <div className="small muted">Note: {c.note}</div>}
              {c.note === "Undo" && <div className="small muted">This was an undo.</div>}
            </td>
            <td data-l="By" className="small mono">{localPhone(c.by_phone) || "—"}</td>
            <td>
              {c.undone_at ? <span className="small muted">Undone {ago(c.undone_at)}</span> : (
                <Busy className="btn sm ghost" run={async () => {
                  await api(`/admin/config/changes/${c.id}/undo`, { method: "POST" });
                  toast("Undone. Owners see the earlier version again.");
                  await Promise.all([load(), changed()]);
                }}>Undo</Busy>
              )}
            </td>
          </tr>
        ))}</tbody>
      </table>
    </div>
  );
}

/* ═════════════ Dashboard widgets ═════════════ */
/** Each Smart Dashboard tile on or off for everyone, and its default order. Owners can still reorder or hide tiles for themselves. */
function DashboardWidgets({ names }) {
  const { toast } = useHub();
  const [rows, setRows] = useState(null);
  const [saved, setSaved] = useState(null);
  const [err, setErr] = useState("");
  const load = useCallback(() => api("/admin/dashboard-widgets").then((d) => {
    const list = d.widgets.slice().sort((a, b) => a.order - b.order);
    setRows(list); setSaved(JSON.stringify(list)); setErr("");
  }).catch((e) => setErr(e.message)), []);
  useEffect(() => { load(); }, [load]);
  if (!rows) return err ? <FormError msg={err} /> : <Loading />;
  const move = (i, by) => {
    const j = i + by;
    if (j < 0 || j >= rows.length) return;
    const next = rows.slice();
    [next[i], next[j]] = [next[j], next[i]];
    setRows(next);
  };
  const dirty = JSON.stringify(rows) !== saved;
  const save = async () => {
    const d = await api("/admin/dashboard-widgets", { method: "PUT", body: { widgets: rows.map((w, i) => ({ key: w.key, on: w.on, order: i * 10 })) } });
    const list = d.widgets.slice().sort((a, b) => a.order - b.order);
    setRows(list); setSaved(JSON.stringify(list));
    toast("Saved. Owners see it within a minute.");
  };
  return (
    <div className="stack">
      <p className="small muted" style={{ maxWidth: "62ch" }}>
        The tiles on every owner's dashboard. A tile shows only when the owner's plan has its feature; otherwise it shows locked, if that feature is shown when locked.
        Switching a tile off here hides it for everyone. Owners can still reorder and hide tiles for themselves.
      </p>
      <ol className="db-admin">
        {rows.map((w, i) => (
          <li key={w.key} className={w.on ? "" : "off"}>
            <div className="stack" style={{ gap: 4, minWidth: 0 }}>
              <b>{w.title}</b>
              <span className="small muted">{w.what}</span>
              <span className="small muted">{w.features.length ? w.features.map((f) => `${f.label} · ${names[f.tier] || f.tier}`).join(" or ") : "Every plan"}</span>
            </div>
            <div className="row" style={{ gap: 6, flexWrap: "nowrap" }}>
              <button className="btn sm ghost" disabled={i === 0} onClick={() => move(i, -1)} aria-label={`Move ${w.title} up`}>↑</button>
              <button className="btn sm ghost" disabled={i === rows.length - 1} onClick={() => move(i, 1)} aria-label={`Move ${w.title} down`}>↓</button>
              <Switch checked={w.on} disabled={w.key === "today"} label={w.on ? "On" : "Off"}
                onChange={(on) => setRows(rows.map((x) => (x.key === w.key ? { ...x, on } : x)))} />
            </div>
          </li>
        ))}
      </ol>
      <FormError msg={err} />
      <div className="row">
        <Busy className="btn primary" disabled={!dirty} onError={setErr} run={save}>Save widgets</Busy>
        <button className="btn ghost" disabled={!dirty} onClick={() => setRows(JSON.parse(saved))}>Undo changes</button>
        <button className="btn sm ghost" onClick={() => setRows(rows.slice().sort((a, b) => a.default_order - b.default_order).map((w) => ({ ...w, on: true })))}>Standard order, all on</button>
      </div>
    </div>
  );
}

/* ═════════════ Page ═════════════ */
const TABS = [["board", "Plan board"], ["menu", "Menu order"], ["plans", "Plans"], ["widgets", "Dashboard widgets"], ["grants", "Grants"], ["log", "Change log"]];

export default function AdminFeatures() {
  const { refresh } = useHub();
  const [cfg, setCfg] = useState(null);
  const [err, setErr] = useState("");
  const [tab, setTab] = useState("board");
  const load = useCallback(() => api("/admin/config").then((d) => { setCfg(d); setErr(""); return d; }).catch((e) => setErr(e.message)), []);
  useEffect(() => { load(); }, [load]);
  // After any change: reload the config here, and the admin's own menu (it is built from the same settings).
  const changed = useCallback(async () => { await Promise.all([load(), refresh()]); }, [load, refresh]);
  const names = useMemo(() => Object.fromEntries((cfg?.tiers || []).map((t) => [t.tier, t.name])), [cfg]);

  return (
    <Sheet kicker="Admin · Features & plans" id="A-03" pillar="Admin" title={<>Every feature, in the right <em>plan</em>.</>}
      sub="Move features between plans, switch them on or off for everyone, rename them, reorder the menu and set each plan's price and limits. Owners see every change at once.">
      <Tabs tabs={TABS} value={tab} onChange={setTab} />
      <FormError msg={err} />
      {!cfg ? (!err && <Loading />)
        : tab === "board" ? <Board cfg={cfg} setCfg={setCfg} changed={changed} names={names} />
        : tab === "menu" ? <MenuOrder cfg={cfg} changed={changed} />
        : tab === "plans" ? (
          <div className="stack">
            <p className="small muted" style={{ maxWidth: "62ch" }}>New prices apply to new purchases; owners who already paid keep what they bought. The short name shows on locked menu items.</p>
            {cfg.tiers.map((t) => <PlanForm key={t.tier} t={t} changed={changed} />)}
          </div>
        )
        : tab === "widgets" ? <DashboardWidgets names={names} />
        : tab === "grants" ? <Grants cfg={cfg} names={names} changed={changed} />
        : <ChangeLog names={names} changed={changed} />}
    </Sheet>
  );
}
