import React, { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { Link, useSearchParams } from "react-router-dom";
import { ago, api, localPhone, rs, waLink } from "../api.js";
import { Busy, Field, FormError, Modal, Sheet, useHub } from "../ui.jsx";
import { Loading, Tabs } from "../v2.jsx";
import "./crm.css";
import "./pipeline.css";

/* v2.3 Pipeline & Opportunities — one page, one pipeline per business, laid out like GoHighLevel's Opportunities:
   an Opportunities tab (toolbar, board or list, cards with quick actions, an "Edit opportunity" dialog) and a Pipeline tab
   (the stages in order). The 4 questions build the stages; each stage sits on one of the 5 Football Field steps, so every
   report keeps working. Cards move by drag and drop (mouse, or a long press on a phone) or from their ⋯ menu. */

const shortDay = (d) => (d ? new Date(`${d}T00:00:00`).toLocaleDateString("en-IN", { day: "numeric", month: "short" }) : "");
const sumOf = (list) => list.reduce((t, d) => t + (Number(d.value) || 0), 0);
/** Short rupees for the summary line: ₹13.45 L, ₹1.2 Cr (exact figures show everywhere else). */
const short = (n) => {
  const v = Number(n || 0);
  const trim = (x) => x.toFixed(2).replace(/\.?0+$/, "");
  return v >= 1e7 ? `₹${trim(v / 1e7)} Cr` : v >= 1e5 ? `₹${trim(v / 1e5)} L` : rs(v);
};

/* ───────────── the 4 questions ───────────── */
function Questions({ setup, onBuilt, onCancel }) {
  const { toast } = useHub();
  const start = setup.pipeline?.answers?.sell || setup.pipeline?.answers?.customers ? setup.pipeline.answers : setup.prefill;
  const [a, setA] = useState({ sell: "", customers: "", sources: [], who: "owner", after_lead: "", path: [], before_pay: "", reasons: [], ...start });
  const [reason, setReason] = useState("");
  const [err, setErr] = useState("");
  const o = setup.options;
  const where = setup.pipeline ? {} : setup.where || {};
  const set = (k, v) => setA((x) => ({ ...x, [k]: v }));
  const toggle = (k, v) => set(k, a[k].includes(v) ? a[k].filter((x) => x !== v) : [...a[k], v]);
  const addReason = () => { const r = reason.trim(); if (r && !a.reasons.some((x) => x.toLowerCase() === r.toLowerCase())) set("reasons", [...a.reasons, r]); setReason(""); };
  const build = async (useAi) => {
    const body = { answers: { sell: a.sell, customers: a.customers, sources: a.sources, who: a.who, after_lead: a.after_lead, path: a.path,
      before_pay: a.before_pay, reasons: a.reasons }, use_ai: useAi };
    const r = await api("/crm/pipeline/build", { method: "POST", body });
    toast(useAi ? "Your pipeline is built. Check the stages below." : "Your pipeline is built from your answers.");
    onBuilt(r);
  };
  return (
    <div className="crm-q">
      <div className="crm-q-card">
        <h3><span className="n">1</span> What do you sell, and who buys it?</h3>
        {where.sell && <span className="crm-from">{where.sell} — check it and change anything that's off.</span>}
        <Field label="What you sell"><textarea className="textarea" rows={2} value={a.sell} maxLength={400} onChange={(e) => set("sell", e.target.value)}
          placeholder="Modular kitchens and full home interiors" /></Field>
        <Field label="Who buys it"><input className="input" value={a.customers} maxLength={300} onChange={(e) => set("customers", e.target.value)}
          placeholder="Families who just got the keys to a new flat" /></Field>
      </div>
      <div className="crm-q-card">
        <h3><span className="n">2</span> Where do your leads come from?</h3>
        {where.sources && <span className="crm-from">{where.sources}</span>}
        <div className="crm-chips" role="group" aria-label="Lead sources">
          {o.sources.map(([k, l]) => <button type="button" key={k} className={`crm-chip ${a.sources.includes(k) ? "on" : ""}`} aria-pressed={a.sources.includes(k)}
            onClick={() => toggle("sources", k)}>{l}</button>)}
        </div>
      </div>
      <div className="crm-q-card">
        <h3><span className="n">3</span> How does a sale happen?</h3>
        <span className="small muted">When a new lead comes in, who handles it?</span>
        <div className="crm-seg" role="group" aria-label="Who handles new leads">
          {o.who.map(([k, l]) => <button type="button" key={k} className={a.who === k ? "on" : ""} aria-pressed={a.who === k} onClick={() => set("who", k)}>{l}</button>)}
        </div>
        <input className="input" value={a.after_lead} maxLength={300} onChange={(e) => set("after_lead", e.target.value)}
          placeholder="What happens next? e.g. I call them the same day and ask for the floor plan" />
        <span className="small muted">Before they pay, there's usually… <b>tap in the order it happens</b>.</span>
        {where.path && <span className="crm-from">{where.path}</span>}
        <div className="crm-chips" role="group" aria-label="Steps before they pay">
          {o.paths.map(([k, l]) => {
            const i = a.path.indexOf(k);
            return <button type="button" key={k} className={`crm-chip ${i >= 0 ? "on" : ""}`} aria-pressed={i >= 0} onClick={() => toggle("path", k)}>
              {i >= 0 && <span className="ord">{i + 1}</span>}{l}</button>;
          })}
        </div>
        <input className="input" value={a.before_pay} maxLength={300} onChange={(e) => set("before_pay", e.target.value)}
          placeholder="Anything else before they pay? e.g. 50% advance before work starts" />
      </div>
      <div className="crm-q-card">
        <h3><span className="n">4</span> Why do people not buy?</h3>
        {where.reasons && <span className="crm-from">{where.reasons}</span>}
        <div className="crm-chips">
          {a.reasons.map((r) => <span key={r} className="crm-chip on x">{r}<button type="button" aria-label={`Remove ${r}`}
            onClick={() => set("reasons", a.reasons.filter((x) => x !== r))}>×</button></span>)}
        </div>
        <div className="row">
          <input className="input" style={{ flex: "1 1 200px", width: "auto" }} value={reason} maxLength={80} onChange={(e) => setReason(e.target.value)}
            onKeyDown={(e) => { if (e.key === "Enter") { e.preventDefault(); addReason(); } }} placeholder="Add a reason in your words" />
          <button type="button" className="btn sm ghost" onClick={addReason}>Add</button>
        </div>
        <span className="small muted">These become the reasons you pick when you mark a deal Lost — so you see what costs you sales.</span>
      </div>
      <FormError msg={err} />
      <div className="row">
        <Busy className="btn primary ai" onError={setErr} run={() => build(true)}>Build my pipeline · 1 AI token</Busy>
        <Busy className="btn ghost" onError={setErr} run={() => build(false)}>Build without AI (free)</Busy>
        {onCancel && <button className="btn ghost" onClick={onCancel}>Cancel</button>}
      </div>
    </div>
  );
}


/* ───────────── drag and drop (mouse, pen and touch) ───────────── */
function useBoardDrag(boardRef, onDrop) {
  const st = useRef(null);
  const suppressClick = useRef(false);
  const [over, setOver] = useState(null);

  const colAt = (x, y) => document.elementFromPoint(x, y)?.closest?.("[data-col]")?.getAttribute("data-col") || null;
  const finish = useRef(null);
  const handlers = useRef({});

  const begin = (s) => {
    s.started = true;
    const r = s.card.getBoundingClientRect();
    const g = s.card.cloneNode(true);
    g.classList.add("crm-ghost");
    Object.assign(g.style, { width: `${r.width}px`, left: `${r.left}px`, top: `${r.top}px` });
    document.body.appendChild(g);
    s.ghost = g;
    s.card.classList.add("lifted");
    boardRef.current?.classList.add("is-dragging");   // column snapping pauses, so the board can scroll under the card
    s.col = s.deal.pstage;
    setOver(s.col);
    if (s.type === "touch" && navigator.vibrate) navigator.vibrate(12);
    const loop = () => {
      if (!st.current?.started) return;
      const b = boardRef.current;
      if (b) {
        const br = b.getBoundingClientRect();
        if (s.lx < br.left + 44) b.scrollLeft -= 14;
        else if (s.lx > br.right - 44) b.scrollLeft += 14;
      }
      s.raf = requestAnimationFrame(loop);
    };
    s.raf = requestAnimationFrame(loop);
  };

  const moveTo = (s, x, y) => {
    s.lx = x; s.ly = y;
    if (s.ghost) s.ghost.style.transform = `translate(${x - s.x}px, ${y - s.y}px)`;
    const c = colAt(x, y);
    if (c !== s.col) { s.col = c; setOver(c); }
  };

  finish.current = (drop) => {
    const s = st.current;
    if (!s) return;
    st.current = null;
    clearTimeout(s.timer);
    cancelAnimationFrame(s.raf);
    window.removeEventListener("pointermove", handlers.current.move);
    window.removeEventListener("pointerup", handlers.current.up);
    window.removeEventListener("pointercancel", handlers.current.cancel);
    window.removeEventListener("touchmove", handlers.current.touch);
    if (s.ghost) s.ghost.remove();
    s.card.classList.remove("lifted");
    boardRef.current?.classList.remove("is-dragging");
    setOver(null);
    if (s.started) {
      suppressClick.current = true;
      setTimeout(() => { suppressClick.current = false; }, 50);
      if (drop && s.col && s.col !== s.deal.pstage) onDrop(s.deal, s.col);
    }
  };

  handlers.current.move = (e) => {
    const s = st.current;
    if (!s) return;
    if (!s.started) {
      const dist = Math.hypot(e.clientX - s.x, e.clientY - s.y);
      if (s.type === "touch") { if (dist > 10) finish.current(false); return; }   // a swipe: let the board scroll
      if (dist < 6) return;
      begin(s);
    }
    moveTo(s, e.clientX, e.clientY);
  };
  handlers.current.up = (e) => { const s = st.current; if (s?.started) moveTo(s, e.clientX, e.clientY); finish.current(true); };
  handlers.current.cancel = () => finish.current(false);
  handlers.current.touch = (e) => { if (st.current?.started) e.preventDefault(); };   // while a card is lifted, the page doesn't scroll

  useEffect(() => () => finish.current?.(false), []);

  const onPointerDown = (e, deal) => {
    if ((e.button ?? 0) !== 0 || e.target.closest("a, button, input, select, textarea")) return;
    finish.current(false);
    const s = { deal, card: e.currentTarget, x: e.clientX, y: e.clientY, lx: e.clientX, ly: e.clientY, type: e.pointerType, started: false };
    st.current = s;
    if (e.pointerType === "touch") s.timer = setTimeout(() => { if (st.current === s && !s.started) begin(s); }, 280);   // long press lifts the card
    window.addEventListener("pointermove", handlers.current.move);
    window.addEventListener("pointerup", handlers.current.up);
    window.addEventListener("pointercancel", handlers.current.cancel);
    window.addEventListener("touchmove", handlers.current.touch, { passive: false });
  };
  return { over, onPointerDown, suppressClick };
}


/* ───────────── colours and small pieces ───────────── */
const STAGE_COLOURS = ["#C9A84C", "#2ED3B7", "#6EA8FE", "#B48CF2", "#F29D52", "#7BD389", "#E06C9F", "#5FC4E8", "#E8C547", "#9AA5B1", "#D9774B", "#8FD14F"];
const AVATAR_COLOURS = ["#2ED3B7", "#6EA8FE", "#B48CF2", "#F29D52", "#7BD389", "#E06C9F", "#C9A84C", "#5FC4E8"];
const initials = (name) => String(name || "?").trim().split(/\s+/).slice(0, 2).map((w) => w[0]).join("").toUpperCase() || "?";
const hue = (name) => AVATAR_COLOURS[[...String(name || "")].reduce((t, c) => t + c.charCodeAt(0), 0) % AVATAR_COLOURS.length];
const isOpen = (d) => d.status === "new" || d.status === "contacted";

function Avatar({ name, small }) {
  return <span className={`pp-av ${small ? "sm" : ""}`} style={{ background: hue(name) }} aria-hidden="true">{initials(name)}</span>;
}

const Icon = {
  call: <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M6.6 10.8a15.1 15.1 0 0 0 6.6 6.6l2.2-2.2c.3-.3.7-.4 1-.2 1.1.4 2.3.6 3.6.6.6 0 1 .4 1 1V20c0 .6-.4 1-1 1A17 17 0 0 1 3 4c0-.6.4-1 1-1h3.5c.6 0 1 .4 1 1 0 1.3.2 2.5.6 3.6.1.3 0 .7-.2 1z" /></svg>,
  wa: <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 2a10 10 0 0 0-8.6 15.1L2 22l5-1.3A10 10 0 1 0 12 2zm5.3 14.1c-.2.6-1.3 1.2-1.8 1.2-.5.1-1 .1-3.3-.8-2.8-1.1-4.5-3.9-4.7-4.1-.1-.2-1.1-1.5-1.1-2.9s.7-2.1 1-2.4c.3-.3.6-.3.8-.3h.6c.2 0 .4 0 .6.5l.9 2.1c.1.2.1.4 0 .5l-.3.5-.4.4c-.1.1-.3.3-.1.6.1.3.6 1.1 1.4 1.8 1 .9 1.8 1.1 2.1 1.3.3.1.4.1.6-.1l.8-1c.2-.3.4-.2.6-.1l2 1c.3.1.5.2.5.3.1.2.1.7-.2 1.3z" /></svg>,
  note: <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M5 3h10l4 4v14H5zm9 1.5V8h3.5zM8 12h8v1.5H8zm0 3.5h8V17H8zm0-7h4V10H8z" /></svg>,
  date: <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M7 2v2H5a2 2 0 0 0-2 2v13a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2V6a2 2 0 0 0-2-2h-2V2h-2v2H9V2zm-2 7h14v10H5z" /></svg>,
  doc: <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M6 2h9l5 5v15H6zm8 1.5V8h4.5zM9 12h8v1.5H9zm0 3.5h8V17H9z" /></svg>,
  board: <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M3 4h5v16H3zm6.5 0h5v11h-5zM16 4h5v7h-5z" /></svg>,
  list: <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M3 5h18v2H3zm0 6h18v2H3zm0 6h18v2H3z" /></svg>,
  more: <svg viewBox="0 0 24 24" aria-hidden="true"><circle cx="5" cy="12" r="2" /><circle cx="12" cy="12" r="2" /><circle cx="19" cy="12" r="2" /></svg>,
  grip: <svg viewBox="0 0 24 24" aria-hidden="true"><circle cx="9" cy="6" r="1.6" /><circle cx="15" cy="6" r="1.6" /><circle cx="9" cy="12" r="1.6" /><circle cx="15" cy="12" r="1.6" /><circle cx="9" cy="18" r="1.6" /><circle cx="15" cy="18" r="1.6" /></svg>,
};

/* ───────────── a card's ⋯ menu: move, won, lost, open ───────────── */
function CardMenu({ deal, stages, anchor, onMove, onOpen, onClose, onWon }) {
  const ref = useRef(null);
  const [pos, setPos] = useState(null);
  useEffect(() => {
    // a floating layer next to the ⋯ button, so a column's edge never cuts it off; it flips up near the bottom of the screen
    const r = anchor?.getBoundingClientRect();
    const h = ref.current?.offsetHeight || 260;
    const w = ref.current?.offsetWidth || 220;
    if (!r) return;
    const top = r.bottom + 6 + h > window.innerHeight - 8 ? Math.max(8, r.top - 6 - h) : r.bottom + 6;
    setPos({ top, left: Math.min(Math.max(8, r.right - w), window.innerWidth - w - 8) });
  }, [anchor]);
  useEffect(() => {
    const away = (e) => { if (ref.current && !ref.current.contains(e.target) && !anchor?.contains(e.target)) onClose(); };
    const esc = (e) => { if (e.key === "Escape") { onClose(); anchor?.focus(); } };
    const gone = () => onClose();
    window.addEventListener("pointerdown", away);
    window.addEventListener("keydown", esc);
    window.addEventListener("scroll", gone, true);
    window.addEventListener("resize", gone);
    ref.current?.querySelector("button")?.focus();
    return () => {
      window.removeEventListener("pointerdown", away); window.removeEventListener("keydown", esc);
      window.removeEventListener("scroll", gone, true); window.removeEventListener("resize", gone);
    };
  }, [onClose, anchor]);
  return createPortal(
    <div className="pp-menu" ref={ref} role="menu" aria-label={`Actions for ${deal.name}`} style={pos ? { top: pos.top, left: pos.left } : { visibility: "hidden", top: 0, left: 0 }}
      onPointerDown={(e) => e.stopPropagation()} onClick={(e) => e.stopPropagation()}>
      <button role="menuitem" onClick={() => { onClose(); onOpen(deal.id); }}>{deal.readonly ? "View" : "Open"}</button>
      {deal.readonly && deal.open_url && <a role="menuitem" className="pp-menu-link" href={deal.open_url} target="_blank" rel="noopener noreferrer" onClick={onClose}>Open in Employz.ai</a>}
      {isOpen(deal) && !deal.readonly && <>
        <span className="pp-menu-label">Move to</span>
        {stages.filter((s) => s.id !== deal.pstage).map((s) => (
          <button key={s.id} role="menuitem" onClick={() => { onClose(); onMove(deal, s.id); }}><i style={{ background: s.colour }} />{s.name}</button>
        ))}
        <hr />
        <button role="menuitem" className="won" onClick={() => { onClose(); onWon(deal); }}>Mark won</button>
        <button role="menuitem" onClick={() => { onClose(); onOpen(deal.id, "lost"); }}>Mark lost…</button>
      </>}
    </div>,
    document.body,
  );
}

/* ───────────── one card ───────────── */
function Card({ d, stages, today, onPointerDown, suppressClick, onOpen, onMove, onWon, menuFor, setMenuFor }) {
  const due = d.follow_up_on;
  const notes = (d.counts?.note || 0) + (d.counts?.call || 0) + (d.counts?.meeting || 0);
  const dots = useRef(null);
  const closeMenu = useCallback(() => setMenuFor(null), [setMenuFor]);
  return (
    <div className={`pp-card ${isOpen(d) ? "" : `st-${d.status}`}`} role="button" tabIndex={0}
      aria-label={`${d.name}, ${d.value ? rs(d.value) : "no value yet"}. Open details`}
      onPointerDown={(e) => isOpen(d) && !d.readonly && onPointerDown(e, d)} onClick={() => { if (!suppressClick.current) onOpen(d.id); }}
      onKeyDown={(e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); onOpen(d.id); } }}>
      <div className="pp-card-top">
        <Avatar name={d.name} />
        <div className="pp-card-name"><b>{d.name}</b>{d.company && <span>{d.company}</span>}</div>
        <button type="button" ref={dots} className="pp-dots" aria-label={`More for ${d.name}`} aria-haspopup="menu" aria-expanded={menuFor === d.id}
          onClick={(e) => { e.stopPropagation(); setMenuFor(menuFor === d.id ? null : d.id); }}>{Icon.more}</button>
        {menuFor === d.id && <CardMenu deal={d} stages={stages} anchor={dots.current} onMove={onMove} onOpen={onOpen} onWon={onWon} onClose={closeMenu} />}
      </div>
      <dl className="pp-card-fields">
        <div><dt>Value</dt><dd className={d.value ? "money" : "muted"}>{d.value ? rs(d.value) : "Not set"}</dd></div>
        <div><dt>Source</dt><dd>{d.source_label || "—"}</dd></div>
        {d.assignee_name && <div><dt>Owner</dt><dd>{d.assignee_name}</dd></div>}
      </dl>
      {!isOpen(d) && <span className={`pp-status ${d.status}`}>{d.status === "won" ? "Won" : "Lost"}{d.status === "lost" && d.lost_reason ? ` · ${d.lost_reason}` : ""}</span>}
      <div className="pp-card-foot">
        {d.phone ? <a className="pp-ic" href={`tel:${d.phone}`} title={`Call ${localPhone(d.phone)}`} aria-label={`Call ${d.name}`} onClick={(e) => e.stopPropagation()}>{Icon.call}</a> : <span className="pp-ic off">{Icon.call}</span>}
        {d.phone ? <a className="pp-ic" href={waLink("", d.phone)} target="_blank" rel="noopener noreferrer" title="Open in your WhatsApp" aria-label={`WhatsApp ${d.name}`} onClick={(e) => e.stopPropagation()}>{Icon.wa}</a> : <span className="pp-ic off">{Icon.wa}</span>}
        <button type="button" className="pp-ic" title={`${notes} notes and calls`} aria-label={`${notes} notes and calls`} onClick={(e) => { e.stopPropagation(); onOpen(d.id, "notes"); }}>{Icon.note}{notes ? <small>{notes}</small> : null}</button>
        {d.quote && <span className="pp-ic" title={`Quotation ${d.quote.number}`}>{Icon.doc}<small>{d.quote.number}</small></span>}
        <span className="grow" />
        {due && <span className={`pp-due ${due < today ? "over" : due === today ? "today" : ""}`} title="Follow-up date">{Icon.date}{due === today ? "Today" : shortDay(due)}</span>}
        {d.assignee_name && <Avatar name={d.assignee_name} small />}
      </div>
    </div>
  );
}

/* ───────────── the board ───────────── */
function Board({ stages, deals, today, sort, onMove, onOpen, onWon, onAddIn, readonly }) {
  const boardRef = useRef(null);
  const { over, onPointerDown, suppressClick } = useBoardDrag(boardRef, onMove);
  const [menuFor, setMenuFor] = useState(null);
  return (
    <div className="pp-board" ref={boardRef}>
      {stages.map((s) => {
        const col = sortDeals(deals.filter((d) => d.pstage === s.id), sort);
        return (
          <section key={s.id} className={`pp-col ${over === s.id ? "over" : ""}`} data-col={s.id} aria-label={s.name} style={{ "--stage": s.colour }}>
            <header className="pp-col-head">
              <div className="row between" style={{ gap: 6, flexWrap: "nowrap" }}>
                <b title={s.ff_label}>{s.name}</b>
                {!readonly && <button type="button" className="pp-add" aria-label={`Add an opportunity in ${s.name}`} onClick={() => onAddIn(s.id)}>+</button>}
              </div>
              <span className="pp-col-sum"><span>{col.length} {col.length === 1 ? "opportunity" : "opportunities"}</span><span className="money">{rs(sumOf(col))}</span></span>
            </header>
            <div className="pp-col-body">
              {col.map((d) => <Card key={d.id} d={d} stages={stages} today={today} onPointerDown={onPointerDown} suppressClick={suppressClick}
                onOpen={onOpen} onMove={onMove} onWon={onWon} menuFor={menuFor} setMenuFor={setMenuFor} />)}
              {!col.length && <div className="pp-empty">{readonly ? "None in this stage" : "Drop an opportunity here"}</div>}
            </div>
          </section>
        );
      })}
    </div>
  );
}

function sortDeals(list, sort) {
  const by = {
    newest: (a, b) => String(b.created_at).localeCompare(String(a.created_at)),
    oldest: (a, b) => String(a.created_at).localeCompare(String(b.created_at)),
    value: (a, b) => (Number(b.value) || 0) - (Number(a.value) || 0),
    followup: (a, b) => String(a.follow_up_on || "9999").localeCompare(String(b.follow_up_on || "9999")),
    updated: (a, b) => String(b.updated_at || b.stage_at).localeCompare(String(a.updated_at || a.stage_at)),
  }[sort] || (() => 0);
  return [...list].sort(by);
}

/* ───────────── the list (table on a computer, cards on a phone) ───────────── */
function List({ deals, stageOf, today, sort, onOpen }) {
  if (!deals.length) return <div className="empty"><p className="muted">No opportunities match these filters.</p></div>;
  return (
    <div className="pp-table" role="table" aria-label="Opportunities">
      <div className="pp-tr head" role="row">
        <span role="columnheader">Opportunity</span><span role="columnheader">Stage</span><span role="columnheader">Status</span>
        <span role="columnheader">Value</span><span role="columnheader">Source</span><span role="columnheader">Follow-up</span><span role="columnheader">Owner</span>
      </div>
      {sortDeals(deals, sort).map((d) => {
        const st = stageOf[d.pstage];
        return (
          <button key={d.id} className="pp-tr" role="row" onClick={() => onOpen(d.id)}>
            <span className="who" role="cell"><Avatar name={d.name} small /><span><b>{d.name}</b><small>{[d.company, d.phone && localPhone(d.phone)].filter(Boolean).join(" · ")}</small></span></span>
            <span role="cell" data-l="Stage"><i className="dot" style={{ background: st?.colour }} />{st?.name || "—"}</span>
            <span role="cell" data-l="Status"><span className={`pp-status ${isOpen(d) ? "open" : d.status}`}>{isOpen(d) ? "Open" : d.status === "won" ? "Won" : "Lost"}</span></span>
            <span role="cell" data-l="Value" className={d.value ? "money" : "muted"}>{d.value ? rs(d.value) : "—"}</span>
            <span role="cell" data-l="Source">{d.source_label || "—"}</span>
            <span role="cell" data-l="Follow-up" className={d.follow_up_on ? `pp-due ${d.follow_up_on < today ? "over" : d.follow_up_on === today ? "today" : ""}` : "muted"}>{d.follow_up_on ? shortDay(d.follow_up_on) : "—"}</span>
            <span role="cell" data-l="Owner">{d.assignee_name || "—"}</span>
          </button>
        );
      })}
    </div>
  );
}

/* ───────────── edit an opportunity (the GHL-style dialog) ───────────── */
function EditDeal({ id, focus, board, stages, onClose, onChanged }) {
  const { me, toast } = useHub();
  const [d, setD] = useState(null);
  const [f, setF] = useState(null);
  const [err, setErr] = useState("");
  const [tab, setTab] = useState("notes");
  const [note, setNote] = useState("");
  const [kind, setKind] = useState("note");
  const [other, setOther] = useState("");
  const noteRef = useRef(null);
  useEffect(() => { const k = (e) => e.key === "Escape" && onClose(); window.addEventListener("keydown", k); return () => window.removeEventListener("keydown", k); }, [onClose]);
  const load = useCallback(() => api(`/crm/deals/${id}`).then((x) => {
    setD(x);
    const known = !x.lost_reason || (board.lost_reasons || []).includes(x.lost_reason);
    if (!known) setOther(x.lost_reason);
    setF({ name: x.name, company: x.company || "", value: x.value ?? "", pstage: x.pstage, status: isOpen(x) ? "open" : x.status,
      lost_reason: known ? x.lost_reason || "" : "__other",
      expected_close: x.expected_close || "", follow_up_on: x.follow_up_on || "", follow_up_note: x.follow_up_note || "", assignee_id: x.assignee_id || "" });
  }).catch((e) => { if (!e.handled) toast(e.message, "err"); onClose(); }), [id]);  // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => { load(); }, [load]);
  useEffect(() => { if (focus === "lost" && f && f.status !== "lost") setF((x) => ({ ...x, status: "lost" })); }, [focus, d]);  // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => { if (focus === "notes" && d) noteRef.current?.focus(); }, [focus, d]);
  if (!d || !f) {
    return (
      <div className="pp-modal-wrap" role="dialog" aria-modal="true" aria-label="Opening the opportunity">
        <div className="pp-scrim" onClick={onClose} /><div className="pp-modal pp-modal-loading"><Loading /></div>
      </div>
    );
  }
  const set = (k) => (e) => setF({ ...f, [k]: e.target.value });
  const reasons = board.lost_reasons || [];
  const reason = f.lost_reason === "__other" ? other.trim() : f.lost_reason;
  const update = async () => {
    const name = f.name.trim();
    if (name.length < 2) throw new Error("Give the opportunity a name.");
    if (f.status === "lost" && !reason) throw new Error("Pick why they didn't buy.");
    if (f.value !== "" && !(Number(f.value) >= 0)) throw new Error("The value must be a number, or empty.");
    const was = isOpen(d) ? "open" : d.status;
    let x = d;
    if (f.pstage !== d.pstage && isOpen(d)) x = await api(`/crm/deals/${id}/move`, { method: "POST", body: { pstage: f.pstage } });
    // only what changed is sent, so "Recently updated" and the history stay true
    const body = {};
    const value = f.value === "" ? null : Number(f.value);
    if (name !== d.name) body.name = name;
    if (f.company.trim() !== (d.company || "")) body.company = f.company.trim();
    if (value !== (d.value ?? null)) body.value = value;
    for (const k of ["expected_close", "follow_up_on", "follow_up_note"]) if (f[k] !== (d[k] || "")) body[k] = f[k];
    if ((f.assignee_id || null) !== (d.assignee_id || null)) body.assignee_id = f.assignee_id || null;
    if (f.status !== was) Object.assign(body, f.status === "open" ? { status: "contacted" } : { status: f.status, ...(f.status === "lost" ? { lost_reason: reason } : {}) });
    else if (f.status === "lost" && reason !== (d.lost_reason || "")) body.lost_reason = reason;
    if (Object.keys(body).length) x = await api(`/crm/deals/${id}`, { method: "PATCH", body });
    if (x === d) { onClose(); return; }
    toast(f.status === "won" && was !== "won" ? `${x.name} — won${x.value ? `: ${rs(x.value)}` : ""}` : "Opportunity updated");
    onChanged(x);
    onClose();
  };
  const logIt = async () => {
    if (kind !== "note" && d.contact && me.features?.crm) {
      await api(`/crm/contacts/${d.contact.id}/activities`, { method: "POST", body: { kind, text: note.trim(), lead_id: d.id } });
    } else {
      await api(`/crm/deals/${id}`, { method: "PATCH", body: { note: note.trim() } });
    }
    setNote("");
    toast(kind === "call" ? "Call logged" : kind === "meeting" ? "Meeting logged" : "Note added");
    const x = await api(`/crm/deals/${id}`);   // the history only: what's typed on the left stays as it is until Update
    setD((old) => ({ ...old, activities: x.activities, contact: x.contact }));
    onChanged(null);
  };
  const team = board.team || [];
  return (
    <div className="pp-modal-wrap" role="dialog" aria-modal="true" aria-label={`Edit opportunity: ${d.name}`}>
      <div className="pp-scrim" onClick={onClose} />
      <div className="pp-modal">
        <header className="pp-modal-head">
          <div className="row" style={{ gap: 12, flexWrap: "nowrap", minWidth: 0 }}>
            <Avatar name={d.name} />
            <div className="stack" style={{ gap: 2, minWidth: 0 }}>
              <span className="label">Edit opportunity</span>
              <h2>{d.name}</h2>
            </div>
          </div>
          <div className="row" style={{ gap: 6 }}>
            {d.phone && <a className="btn sm" href={waLink("", d.phone)} target="_blank" rel="noopener noreferrer">WhatsApp</a>}
            {d.phone && <a className="btn sm ghost" href={`tel:${d.phone}`}>Call</a>}
            {d.email && <a className="btn sm ghost" href={`mailto:${d.email}`}>Email</a>}
            <button className="btn sm ghost" onClick={onClose} aria-label="Close">✕</button>
          </div>
        </header>
        <div className="pp-modal-body">
          <div className="pp-modal-main">
            <span className="label">Contact</span>
            <div className="pp-contact">
              <span><b>{d.contact?.name || d.name}</b>{(d.phone || d.email) && <small>{[d.phone && localPhone(d.phone), d.email].filter(Boolean).join(" · ")}</small>}</span>
              {d.contact && me.features?.crm && <Link className="btn sm ghost" to={`/contacts/${d.contact.id}`}>Open in CRM</Link>}
            </div>
            {d.message && <p className="small muted pp-msg">“{d.message}”</p>}
            <span className="label">Opportunity details</span>
            <Field label="Opportunity name"><input className="input" value={f.name} maxLength={80} onChange={set("name")} /></Field>
            <div className="crm-two">
              <Field label="Pipeline"><input className="input" value={board.pipeline_name || "Sales pipeline"} disabled /></Field>
              <Field label="Stage"><select className="select" value={f.pstage} disabled={!isOpen(d)} onChange={set("pstage")}>
                {stages.map((s) => <option key={s.id} value={s.id}>{s.name}</option>)}</select></Field>
            </div>
            <div className="crm-two">
              <Field label="Status"><select className="select" value={f.status} onChange={set("status")}>
                <option value="open">Open</option><option value="won">Won</option><option value="lost">Lost</option></select></Field>
              <Field label="Opportunity value (₹)"><input className="input money" type="number" min="0" step="any" inputMode="decimal" value={f.value}
                onChange={set("value")} placeholder="e.g. 185000" /></Field>
            </div>
            {f.status === "lost" && (
              <div className="stack" style={{ gap: 8 }}>
                <span className="small">Why didn't they buy?</span>
                <div className="crm-chips">
                  {reasons.map((r) => <button type="button" key={r} className={`crm-chip ${f.lost_reason === r ? "on" : ""}`} onClick={() => setF({ ...f, lost_reason: r })}>{r}</button>)}
                  <button type="button" className={`crm-chip ${f.lost_reason === "__other" ? "on" : ""}`} onClick={() => setF({ ...f, lost_reason: "__other" })}>Something else</button>
                </div>
                {f.lost_reason === "__other" && <input className="input" value={other} maxLength={80} onChange={(e) => setOther(e.target.value)} placeholder="In a few words" />}
              </div>
            )}
            <div className="crm-two">
              <Field label="Business name"><input className="input" value={f.company} maxLength={120} onChange={set("company")} /></Field>
              {team.length > 1 ? (
                <Field label="Owner"><select className="select" value={f.assignee_id} onChange={set("assignee_id")}>
                  <option value="">Not set</option>{team.map((t) => <option key={t.id} value={t.id}>{t.name}</option>)}</select></Field>
              ) : <Field label="Source"><input className="input" value={d.source_label || "—"} disabled /></Field>}
            </div>
            <div className="crm-two">
              <Field label="Expected to close"><input className="input" type="date" value={f.expected_close} onChange={set("expected_close")} /></Field>
              <Field label="Follow up on"><input className="input" type="date" value={f.follow_up_on} onChange={set("follow_up_on")} /></Field>
            </div>
            {f.follow_up_on && <Field label="What to follow up about" hint="A reminder shows in your hub at 10am that day.">
              <input className="input" value={f.follow_up_note} maxLength={300} onChange={set("follow_up_note")} placeholder="Send the revised quotation" /></Field>}
          </div>
          <aside className="pp-modal-side">
            <Tabs value={tab} onChange={setTab} tabs={[["notes", "Notes"], ["docs", "Quotation & invoice"]]} />
            {tab === "notes" ? (
              <div className="stack">
                {d.contact && me.features?.crm && (
                  <div className="crm-seg" role="group" aria-label="What happened">
                    {[["note", "Note"], ["call", "Call"], ["meeting", "Meeting"]].map(([k, l]) => <button type="button" key={k} className={kind === k ? "on" : ""} onClick={() => setKind(k)}>{l}</button>)}
                  </div>
                )}
                <textarea ref={noteRef} className="textarea" rows={3} value={note} maxLength={1000} aria-label="Add a note" onChange={(e) => setNote(e.target.value)}
                  placeholder={kind === "call" ? "What did you talk about?" : kind === "meeting" ? "What was decided?" : "Add a note…"} />
                <div><Busy className="btn sm" disabled={note.trim().length < 2} run={logIt}>Add</Busy></div>
                <div className="crm-timeline">
                  {(d.activities || []).map((a) => (
                    <div key={a.id} className={`crm-ev ${a.kind}`}><i />
                      <div><p className="small">{a.text}</p><span className="small muted">{ago(a.at)}{a.by_name ? ` · ${a.by_name}` : ""}</span></div>
                    </div>
                  ))}
                  {!(d.activities || []).length && <span className="small muted">Nothing yet.</span>}
                </div>
              </div>
            ) : (
              <div className="stack">
                {(d.documents || []).length ? (d.documents || []).map((q) => (
                  <Link key={q.id} to="/quotes" className="pp-doc">
                    <span>{q.kind === "invoice" ? "Invoice" : "Quotation"} {q.number}<small>{q.status}</small></span>
                    {q.total != null && <span className="money">{rs(q.total)}</span>}
                  </Link>
                )) : <span className="small muted">No quotation yet.</span>}
                {me.features?.quotations && !(d.documents || []).some((q) => q.kind === "quote") && (
                  <Link className="btn sm ghost crm-fit" to={`/quotes?inquiry=${d.id}`}>Make a quotation</Link>)}
              </div>
            )}
          </aside>
        </div>
        <footer className="pp-modal-foot">
          <FormError msg={err} />
          <button className="btn ghost" onClick={onClose}>Cancel</button>
          <Busy className="btn primary" onError={setErr} run={update}>Update</Busy>
        </footer>
      </div>
    </div>
  );
}

/* ───────────── an Employz.ai opportunity (view only: changes are made in Employz.ai) ───────────── */
function ViewDeal({ deal: d, board, stageOf, onClose }) {
  const { me } = useHub();
  useEffect(() => { const k = (e) => e.key === "Escape" && onClose(); window.addEventListener("keydown", k); return () => window.removeEventListener("keydown", k); }, [onClose]);
  const fields = Object.entries(d.fields || {});
  const status = { open: "Open", won: "Won", lost: "Lost", abandoned: "Abandoned" }[d.crm_status] || (isOpen(d) ? "Open" : d.status);
  const rows = [["Pipeline", board.pipeline_name], ["Stage", stageOf[d.pstage]?.name || "—"], ["Status", status],
    ["Value", d.value ? rs(d.value) : "Not set"], ["Source", d.source_label || "—"], ["Owner", d.assignee_name || "—"],
    ["Opportunity name", d.title || "—"], ["Updated", d.updated_at ? ago(d.updated_at) : "—"]];
  return (
    <div className="pp-modal-wrap" role="dialog" aria-modal="true" aria-label={`Opportunity: ${d.name}`}>
      <div className="pp-scrim" onClick={onClose} />
      <div className="pp-modal">
        <header className="pp-modal-head">
          <div className="row" style={{ gap: 12, flexWrap: "nowrap", minWidth: 0 }}>
            <Avatar name={d.name} />
            <div className="stack" style={{ gap: 2, minWidth: 0 }}>
              <span className="label">From Employz.ai · view only</span>
              <h2>{d.name}</h2>
            </div>
          </div>
          <div className="row" style={{ gap: 6 }}>
            {d.phone && <a className="btn sm" href={waLink("", d.phone)} target="_blank" rel="noopener noreferrer">WhatsApp</a>}
            {d.phone && <a className="btn sm ghost" href={`tel:${d.phone}`}>Call</a>}
            {d.email && <a className="btn sm ghost" href={`mailto:${d.email}`}>Email</a>}
            <button className="btn sm ghost" onClick={onClose} aria-label="Close">✕</button>
          </div>
        </header>
        <div className="pp-modal-body pp-modal-one">
          <div className="pp-modal-main">
            <span className="label">Contact</span>
            <div className="pp-contact">
              <span><b>{d.name}</b>{(d.phone || d.email || d.company) && <small>{[d.company, d.phone && localPhone(d.phone), d.email].filter(Boolean).join(" · ")}</small>}</span>
              {d.contact_id && me.features?.crm && <Link className="btn sm ghost" to={`/contacts/${d.contact_id}`}>Open in CRM</Link>}
            </div>
            <span className="label">Opportunity details</span>
            <dl className="pp-ro">{rows.map(([k, v]) => <div key={k}><dt>{k}</dt><dd>{v}</dd></div>)}</dl>
            {fields.length > 0 && <>
              <span className="label">Custom fields</span>
              <dl className="pp-ro">{fields.map(([k, v]) => <div key={k}><dt>{k}</dt><dd>{Array.isArray(v) ? v.join(", ") : String(v ?? "—")}</dd></div>)}</dl>
            </>}
            {d.hub_lead_id && <p className="small muted">This opportunity started as an inquiry in the hub, and was sent to Employz.ai.</p>}
          </div>
        </div>
        <footer className="pp-modal-foot">
          <span className="small muted grow">Changes to this opportunity are made in Employz.ai, and show here within {board.sync?.every_minutes || 15} minutes.</span>
          <button className="btn ghost" onClick={onClose}>Close</button>
          {d.open_url && <a className="btn primary" href={d.open_url} target="_blank" rel="noopener noreferrer">Open in Employz.ai</a>}
        </footer>
      </div>
    </div>
  );
}

/* ───────────── add an opportunity ───────────── */
function AddDeal({ board, stages, stage, onClose, onAdded }) {
  const [f, setF] = useState({ name: "", phone: "", email: "", company: "", value: "", pstage: stage || stages[0]?.id, source: "phone", follow_up_on: "", note: "" });
  const [err, setErr] = useState("");
  const set = (k) => (e) => setF({ ...f, [k]: e.target.value });
  return (
    <Modal onClose={onClose}>
      <span className="kicker">Opportunities</span>
      <h2>Add an <em>opportunity</em>.</h2>
      <div className="stack">
        <Field label="Contact name"><input className="input" value={f.name} maxLength={80} onChange={set("name")} placeholder="Customer or company name" autoFocus /></Field>
        <div className="crm-two">
          <Field label="Mobile"><input className="input" inputMode="tel" value={f.phone} maxLength={24} onChange={set("phone")} placeholder="98200 00000" /></Field>
          <Field label="Opportunity value (₹)"><input className="input money" type="number" min="0" inputMode="decimal" value={f.value} onChange={set("value")} /></Field>
        </div>
        <div className="crm-two">
          <Field label="Business name (optional)"><input className="input" value={f.company} maxLength={120} onChange={set("company")} /></Field>
          <Field label="Email (optional)"><input className="input" type="email" value={f.email} maxLength={254} onChange={set("email")} /></Field>
        </div>
        <div className="crm-two">
          <Field label="Stage"><select className="select" value={f.pstage} onChange={set("pstage")}>{stages.map((s) => <option key={s.id} value={s.id}>{s.name}</option>)}</select></Field>
          <Field label="Source"><select className="select" value={f.source} onChange={set("source")}>{(board.sources || []).map(([k, l]) => <option key={k} value={k}>{l}</option>)}</select></Field>
        </div>
        <Field label="Follow up on (optional)"><input className="input" type="date" value={f.follow_up_on} onChange={set("follow_up_on")} /></Field>
        <Field label="Note (optional)"><textarea className="textarea" rows={2} value={f.note} maxLength={1000} onChange={set("note")} placeholder="What do they need?" /></Field>
        <FormError msg={err} />
        <div className="row">
          <Busy className="btn primary" onError={setErr} disabled={f.name.trim().length < 2}
            run={async () => { const d = await api("/crm/deals", { method: "POST", body: { ...f, value: f.value === "" ? null : Number(f.value) } }); onAdded(d); }}>Add opportunity</Busy>
          <button className="btn ghost" onClick={onClose}>Cancel</button>
        </div>
      </div>
    </Modal>
  );
}

/* ───────────── Pipeline tab: stages in order (drag the handle, or the arrows on a phone) ───────────── */
function StageEditor({ stages, reasons, ffOptions, canEdit, onSaved, onAsk }) {
  const { toast } = useHub();
  const [list, setList] = useState(stages.map((s) => ({ ...s })));
  const [lost, setLost] = useState(reasons.join("\n"));
  const [err, setErr] = useState("");
  const [dragIdx, setDragIdx] = useState(null);
  useEffect(() => { setList(stages.map((s) => ({ ...s }))); setLost(reasons.join("\n")); }, [stages, reasons]);
  const moveRow = (from, to) => setList((l) => { if (to < 0 || to >= l.length) return l; const n = [...l]; const [x] = n.splice(from, 1); n.splice(to, 0, x); return n; });
  const edit = (i, k, v) => setList((l) => l.map((s, x) => (x === i ? { ...s, [k]: v } : s)));
  const dirty = JSON.stringify(list.map((s) => [s.id, s.name, s.ff])) !== JSON.stringify(stages.map((s) => [s.id, s.name, s.ff])) || lost !== reasons.join("\n");
  const save = async () => {
    if (list.some((s) => s.name.trim().length < 2)) throw new Error("Give every stage a name (2 letters or more).");
    const body = { stages: list.map((s) => ({ id: s.id || null, name: s.name.trim(), ff: s.ff, hint: s.hint || "" })),
      lost_reasons: lost.split("\n").map((x) => x.trim()).filter(Boolean) };
    const r = await api("/crm/pipeline/stages", { method: "PUT", body });
    toast("Pipeline saved");
    onSaved(r);
  };
  return (
    <div className="pp-pipe">
      <div className="pp-pipe-head">
        <div className="stack" style={{ gap: 2 }}>
          <span className="label">Pipeline</span>
          <h3>Sales pipeline</h3>
          <span className="small muted">{list.length} stages. Each one sits on a step of the sale, so your Dashboard and reports keep counting it right.</span>
        </div>
        {canEdit && <button className="btn sm ghost" onClick={onAsk}>Build it again from the 4 questions</button>}
      </div>
      <div className="pp-stages">
        {list.map((s, i) => (
          <div key={s.id || `new-${i}`} className={`pp-stage ${dragIdx === i ? "dragging" : ""}`} style={{ "--stage": STAGE_COLOURS[i % STAGE_COLOURS.length] }}
            onDragOver={(e) => { if (dragIdx === null) return; e.preventDefault(); if (dragIdx !== i) { moveRow(dragIdx, i); setDragIdx(i); } }}
            onDrop={(e) => e.preventDefault()}>
            {/* only the handle drags, so typing in the name box works as usual */}
            <span className={`pp-grip ${canEdit ? "" : "off"}`} aria-hidden="true" draggable={canEdit}
              onDragStart={(e) => { setDragIdx(i); e.dataTransfer.effectAllowed = "move"; e.dataTransfer.setData("text/plain", s.name || "stage");
                const row = e.currentTarget.closest(".pp-stage"); if (row) e.dataTransfer.setDragImage(row, 20, 20); }}
              onDragEnd={() => setDragIdx(null)}>{Icon.grip}</span>
            <span className="pp-stage-n">{i + 1}</span>
            <input className="input" value={s.name} maxLength={40} disabled={!canEdit} aria-label={`Stage ${i + 1} name`} onChange={(e) => edit(i, "name", e.target.value)} />
            <select className="select" value={s.ff} disabled={!canEdit} aria-label={`Step of the sale for ${s.name || "this stage"}`} onChange={(e) => edit(i, "ff", e.target.value)}>
              {ffOptions.map((f) => <option key={f.key} value={f.key}>{f.label}</option>)}
            </select>
            {canEdit && <div className="btns">
              <button type="button" className="crm-icon" aria-label="Move up" disabled={i === 0} onClick={() => moveRow(i, i - 1)}>↑</button>
              <button type="button" className="crm-icon" aria-label="Move down" disabled={i === list.length - 1} onClick={() => moveRow(i, i + 1)}>↓</button>
              <button type="button" className="crm-icon" aria-label={`Delete ${s.name}`} disabled={list.length <= 2} onClick={() => setList((l) => l.filter((_, x) => x !== i))}>✕</button>
            </div>}
          </div>
        ))}
        {canEdit && <button type="button" className="pp-stage-add" disabled={list.length >= 12}
          onClick={() => setList((l) => [...l, { id: null, name: "", ff: l.length ? l[l.length - 1].ff : "identification", hint: "" }])}>+ Add stage</button>}
      </div>
      <Field label="Lost reasons (one per line)" hint="You pick one when you mark an opportunity Lost — so you see what costs you sales.">
        <textarea className="textarea" rows={4} value={lost} disabled={!canEdit} onChange={(e) => setLost(e.target.value)} />
      </Field>
      <span className="small muted">Opportunities in a stage you delete move to another stage on the same step of the sale. Nothing is lost.</span>
      <FormError msg={err} />
      {canEdit && <div className="row">
        <Busy className="btn primary" disabled={!dirty} onError={setErr} run={save}>Save pipeline</Busy>
        {dirty && <button className="btn ghost" onClick={() => { setList(stages.map((s) => ({ ...s }))); setLost(reasons.join("\n")); }}>Undo changes</button>}
      </div>}
    </div>
  );
}

/* ───────────── the page ───────────── */
export default function Pipeline() {
  const { me, toast } = useHub();
  const [params, setParams] = useSearchParams();
  const [setup, setSetup] = useState(null);
  const [board, setBoard] = useState(null);
  const [chosenTab, setTab] = useState(params.get("tab") === "pipeline" ? "pipeline" : params.get("tab") === "opportunities" ? "opportunities" : null);
  const [asking, setAsking] = useState(false);
  const [view, setView] = useState("board");
  const [adding, setAdding] = useState(null);         // null | stage id | "" (first stage)
  const [openId, setOpenId] = useState(params.get("deal"));
  const [pipe, setPipe] = useState(params.get("pipeline") || "hub");   // "hub", or "employz:<id>" (view only)
  const [focus, setFocus] = useState(null);
  const [flt, setFlt] = useState({ q: "", source: "", status: "open", due: false, min: "" });
  const [sort, setSort] = useState("newest");
  const [showFilters, setShowFilters] = useState(false);
  const [undo, setUndo] = useState(null);             // {deal, from, to} for a few seconds after a move
  const undoTimer = useRef(null);

  const loadBoard = useCallback(() => api(pipe === "hub" ? "/crm/deals" : `/crm/deals?pipeline=${encodeURIComponent(pipe)}`).then(setBoard)
    .catch((e) => { if (pipe !== "hub" && e.status === 404) { setPipe("hub"); return; } throw e; }), [pipe]);
  const loadSetup = useCallback(() => api("/crm/pipeline").then(setSetup), []);
  useEffect(() => { Promise.all([loadSetup(), loadBoard()]).catch((e) => { if (!e.handled) toast(e.message, "err"); }); }, [loadSetup, loadBoard]);  // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => () => clearTimeout(undoTimer.current), []);

  // first visit, before the stages exist: the 4 questions (Pipeline tab) come first for whoever can set them up
  const tab = chosenTab || (setup && !setup.pipeline && setup.can_edit ? "pipeline" : "opportunities");
  const where = (extra = {}) => ({ ...(pipe !== "hub" ? { pipeline: pipe } : {}), ...extra });
  const openDeal = (id, why = null) => { if (id) { clearTimeout(undoTimer.current); setUndo(null); } setOpenId(id); setFocus(why); setParams(id ? where({ deal: id }) : tab === "pipeline" ? { tab: "pipeline" } : where(), { replace: true }); };
  const pickPipe = (k) => { setOpenId(null); setBoard(null); setPipe(k); setParams(k !== "hub" ? { pipeline: k } : {}, { replace: true }); };
  const ffLabel = useMemo(() => Object.fromEntries((setup?.options?.ff || []).map((f) => [f.key, f.label])), [setup]);
  const stages = useMemo(() => (board?.stages || []).map((s, i) => ({ ...s, ff_label: board.readonly ? "" : ffLabel[s.ff] || s.ff, colour: STAGE_COLOURS[i % STAGE_COLOURS.length] })), [board, ffLabel]);
  const stageOf = useMemo(() => Object.fromEntries(stages.map((s) => [s.id, s])), [stages]);

  const head = { kicker: "Conversion · Pipeline & Opportunities", id: "CV-03", pillar: "Conversion" };
  if (!setup || !board) return <Sheet {...head} title={<>Every opportunity, on one <em>board</em>.</>}><Loading /></Sheet>;

  const deals = board.deals;
  const built = !!setup.pipeline;
  const today = board.today;
  const showQuestions = asking || (!built && setup.can_edit && tab === "pipeline");
  const ro = !!board.readonly;
  const pipelines = board.pipelines || [];
  const openedRo = ro && openId ? deals.find((d) => d.id === openId) : null;

  const merge = (x) => { if (x) setBoard((b) => ({ ...b, deals: b.deals.map((d) => (d.id === x.id ? { ...d, ...x } : d)) })); else loadBoard(); };
  const move = async (deal, pstage, quiet = false) => {
    const from = deal.pstage;
    setBoard((b) => ({ ...b, deals: b.deals.map((d) => (d.id === deal.id ? { ...d, pstage } : d)) }));
    try {
      const x = await api(`/crm/deals/${deal.id}/move`, { method: "POST", body: { pstage } });
      merge(x);
      if (!quiet) {
        clearTimeout(undoTimer.current);
        setUndo({ deal, from, to: pstage });
        undoTimer.current = setTimeout(() => setUndo(null), 6000);
      }
    } catch (e) {
      if (!e.handled) toast(e.message, "err");
      loadBoard();
    }
  };
  const won = async (deal) => {
    try {
      const x = await api(`/crm/deals/${deal.id}`, { method: "PATCH", body: { status: "won" } });
      merge(x);
      toast(`${deal.name} — won${x.value ? `: ${rs(x.value)}` : ""}`);
    } catch (e) { if (!e.handled) toast(e.message, "err"); }
  };
  const afterBuild = () => { setAsking(false); setTab("opportunities"); loadSetup(); loadBoard(); };

  const filtered = deals.filter((d) => {
    if (flt.status === "open" && !isOpen(d)) return false;
    if (flt.status === "won" && d.status !== "won") return false;
    if (flt.status === "lost" && d.status !== "lost") return false;
    if (flt.source && d.source !== flt.source && !(flt.source === "website" && ["guide", "lead_magnet"].includes(d.source))) return false;
    if (flt.due && !(d.follow_up_on && d.follow_up_on <= today)) return false;
    if (flt.min !== "" && (Number(d.value) || 0) < Number(flt.min)) return false;
    const q = flt.q.trim().toLowerCase();
    if (q && ![d.name, d.company, d.phone, d.email, d.message].some((x) => String(x || "").toLowerCase().includes(q))) return false;
    return true;
  });
  const t = board.totals;
  const openCount = deals.filter(isOpen).length;
  const title = openCount ? <>{openCount} open {openCount === 1 ? "opportunity" : "opportunities"}, <em>{rs(t.open_value)}</em>.</> : <>Every opportunity, on one <em>board</em>.</>;
  const filterCount = (flt.source ? 1 : 0) + (flt.due ? 1 : 0) + (flt.min !== "" ? 1 : 0);

  return (
    <Sheet {...head} title={title}
      sub={ro ? `Your ${board.pipeline_name || "Employz.ai"} pipeline from Employz.ai, to view. Click a card to see its details.`
        : "Drag an opportunity to its next stage as the talk moves forward. Click a card to edit it, add notes, or mark it Won or Lost."}>
      <div className="pp-subnav" role="tablist">
        {[["opportunities", "Opportunities"], ["pipeline", "Pipeline"]].map(([k, l]) => (
          <button key={k} role="tab" aria-selected={tab === k} className={tab === k ? "on" : ""}
            onClick={() => { setTab(k); setAsking(false); if (k === "pipeline" && ro) pickPipe("hub"); setParams(k === "pipeline" ? { tab: "pipeline" } : where(), { replace: true }); }}>{l}</button>
        ))}
      </div>

      {tab === "pipeline" ? (
        showQuestions ? (
          <div className="panel stack">
            <span className="label">{built ? "Build the pipeline again" : "Set up your pipeline · 4 questions"}</span>
            <Questions setup={setup} onBuilt={afterBuild} onCancel={built ? () => setAsking(false) : () => setTab("opportunities")} />
          </div>
        ) : (
          <StageEditor stages={board.stages} reasons={board.lost_reasons || []} ffOptions={setup.options.ff} canEdit={setup.can_edit}
            onSaved={() => { loadSetup(); loadBoard(); }} onAsk={() => setAsking(true)} />
        )
      ) : (
        <>
          {!built && setup.can_edit && !ro && (
            <div className="notice small pp-setup">
              <span>Your opportunities sit on the 5 steps of a sale until you set up your own stages. It takes 4 quick questions.</span>
              <button className="btn sm ai" onClick={() => { setTab("pipeline"); setParams({ tab: "pipeline" }, { replace: true }); }}>Set up the pipeline</button>
            </div>
          )}
          <div className="pp-toolbar">
            <div className="pp-tb-row">
            <div className="pp-tb-left">
              {pipelines.length > 1 ? (
                <label className="pp-pipe-name pp-pipe-pick">
                  <select aria-label="Pipeline" value={board.pipeline || "hub"} onChange={(e) => pickPipe(e.target.value)}>
                    {pipelines.map((p) => <option key={p.key} value={p.key}>{p.readonly ? `${p.name} · Employz.ai` : p.name}</option>)}
                  </select>
                  <small>{stages.length} stages</small>
                </label>
              ) : <span className="pp-pipe-name" title="One pipeline per business"><b>Sales pipeline</b><small>{stages.length} stages</small></span>}
              <div className="pp-viewtoggle" role="group" aria-label="View">
                <button type="button" className={view === "board" ? "on" : ""} aria-pressed={view === "board"} aria-label="Board" title="Board" onClick={() => setView("board")}>{Icon.board}</button>
                <button type="button" className={view === "list" ? "on" : ""} aria-pressed={view === "list"} aria-label="List" title="List" onClick={() => setView("list")}>{Icon.list}</button>
              </div>
            </div>
            {ro ? (board.open_url && <a className="btn sm pp-add-main" href={board.open_url} target="_blank" rel="noopener noreferrer">Open in Employz.ai</a>)
              : <button className="btn sm primary pp-add-main" onClick={() => setAdding("")}>+ Add opportunity</button>}
            </div>
            <div className="pp-tb-row">
            <input className="input pp-search" type="search" value={flt.q} placeholder="Search opportunities" aria-label="Search opportunities"
              onChange={(e) => setFlt({ ...flt, q: e.target.value })} />
            <div className="pp-tb-right">
              <div className="crm-seg" role="group" aria-label="Status">
                {[["open", "Open"], ["won", "Won"], ["lost", "Lost"], ["all", "All"]].map(([k, l]) => (
                  <button type="button" key={k} className={flt.status === k ? "on" : ""} aria-pressed={flt.status === k} onClick={() => setFlt({ ...flt, status: k })}>{l}</button>
                ))}
              </div>
              <div className="pp-pop-wrap">
                <button type="button" className={`btn sm ghost ${filterCount ? "pp-has" : ""}`} aria-expanded={showFilters} onClick={() => setShowFilters(!showFilters)}>
                  Filters{filterCount ? ` · ${filterCount}` : ""}</button>
                {showFilters && (
                  <div className="pp-pop">
                    <Field label="Source"><select className="select" value={flt.source} onChange={(e) => setFlt({ ...flt, source: e.target.value })}>
                      <option value="">Every source</option>{(board.sources || []).map(([k, l]) => <option key={k} value={k}>{l}</option>)}</select></Field>
                    <Field label="Value from (₹)"><input className="input" type="number" min="0" inputMode="decimal" value={flt.min} onChange={(e) => setFlt({ ...flt, min: e.target.value })} /></Field>
                    <button type="button" className={`crm-chip ${flt.due ? "on" : ""}`} aria-pressed={flt.due} onClick={() => setFlt({ ...flt, due: !flt.due })}>Follow-up due</button>
                    <div className="row between">
                      <button className="btn sm ghost" onClick={() => setFlt({ ...flt, source: "", due: false, min: "" })}>Clear</button>
                      <button className="btn sm" onClick={() => setShowFilters(false)}>Done</button>
                    </div>
                  </div>
                )}
              </div>
              <select className="select pp-sort" aria-label="Sort" value={sort} onChange={(e) => setSort(e.target.value)}>
                <option value="newest">Newest first</option><option value="updated">Recently updated</option><option value="oldest">Oldest first</option>
                <option value="value">Highest value</option><option value="followup">Follow-up date</option>
              </select>
            </div>
            </div>
          </div>

          {ro && (
            <div className="notice small pp-setup">
              <span><b>From Employz.ai · view only.</b> {board.sync?.last_ok_at ? `Last synced ${ago(board.sync.last_ok_at)}.` : "Not synced yet."}
                {" "}Changes are made in Employz.ai and show here within {board.sync?.every_minutes || 15} minutes.{board.sync?.error ? ` ${board.sync.error}` : ""}</span>
            </div>
          )}
          <div className="pp-summary">
            <span><b>{filtered.length}</b> {filtered.length === 1 ? "opportunity" : "opportunities"} · <span className="money">{rs(sumOf(filtered))}</span></span>
            <span className="muted">Won in the last 30 days: <span className="money">{short(t.won_30_value)}</span> · Follow-ups due: <b className={t.followups_due ? "pp-warn" : ""}>{t.followups_due}</b>
              {" "}· This week: {board.week.new} new, {board.week.won} won, {board.week.lost} lost</span>
          </div>

          {view === "board"
            ? (deals.length ? <Board stages={stages} deals={filtered} today={today} sort={sort} onMove={move} onOpen={openDeal} onWon={won} onAddIn={(sid) => setAdding(sid)} readonly={ro} />
              : ro ? <div className="empty"><h2>No opportunities in this pipeline yet.</h2><p className="muted">Opportunities added in Employz.ai show here after the next sync.</p></div>
              : <div className="empty"><h2>No opportunities yet.</h2>
                <p className="muted">Every inquiry from your website, your lead magnet, email or a pasted WhatsApp lands here by itself, in the first stage. You can also add one by hand.</p>
                <button className="btn" onClick={() => setAdding("")}>+ Add opportunity</button></div>)
            : <List deals={filtered} stageOf={stageOf} today={today} sort={sort} onOpen={openDeal} />}
          {view === "board" && deals.length > 0 && !ro && <p className="small muted">Drag a card to move it, or use its ⋯ menu. On a phone: press and hold a card, then drag; swipe sideways to see every stage.</p>}
          {me.features?.crm && <p className="small muted">Every opportunity's contact is in your <Link to="/contacts">CRM</Link>, with their history and follow-ups.</p>}
        </>
      )}

      {undo && (
        <div className="pp-undo" role="status">
          <span><b>{undo.deal.name}</b> moved to {stageOf[undo.to]?.name || "the stage"}</span>
          <button className="btn sm ghost" onClick={() => { clearTimeout(undoTimer.current); setUndo(null); move({ ...undo.deal, pstage: undo.to }, undo.from, true); }}>Undo</button>
        </div>
      )}
      {openedRo && <ViewDeal deal={openedRo} board={board} stageOf={stageOf} onClose={() => openDeal(null)} />}
      {openId && !ro && !String(openId).startsWith("x:") && <EditDeal id={openId} focus={focus} board={board} stages={stages} onClose={() => { openDeal(null); loadBoard(); }} onChanged={merge} />}
      {adding !== null && <AddDeal board={board} stages={stages} stage={adding || undefined} onClose={() => setAdding(null)}
        onAdded={(d) => { setAdding(null); toast(`${d.name} added`); loadBoard(); }} />}
    </Sheet>
  );
}
