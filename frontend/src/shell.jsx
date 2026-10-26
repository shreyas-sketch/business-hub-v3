import React, { useEffect, useMemo, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";

/* Line icons, one per menu section, plus the few the shell needs. 24-unit grid, drawn with the text colour. */
const PATHS = {
  Start: "M3.5 10.5 12 4l8.5 6.5V20a1 1 0 0 1-1 1H15v-6H9v6H4.5a1 1 0 0 1-1-1z",
  Learn: "M3 5h18v12H3zM10 8.5l5 2.5-5 2.5zM8 21h8",
  Structure: "M12 3.5 21 8.5l-9 5-9-5zM3 13l9 5 9-5M3 17.5l9 5 9-5",
  Systems: "M4 7h9M17 7h3M4 17h3M11 17h9M15 5v4M9 15v4",
  Scale: "M12 3v5M12 16v5M3 12h5M16 12h5M6.3 6.3l2.8 2.8M14.9 14.9l2.8 2.8M6.3 17.7l2.8-2.8M14.9 9.1l2.8-2.8",
  "AI team": "M9 11a3.5 3.5 0 1 0 0-7 3.5 3.5 0 0 0 0 7zM2.5 20c.8-3.6 3.3-5.5 6.5-5.5s5.7 1.9 6.5 5.5M16 4.3a3.5 3.5 0 0 1 0 6.4M18 14.8c1.9.8 3 2.6 3.5 5.2",
  Grow: "M3 17l6-6 4 4 8-8M15 7h6v6",
  Admin: "M12 3l8 3v6c0 4.8-3.4 8-8 9-4.6-1-8-4.2-8-9V6z",
  // v2.3: the 6 engines, More and Account
  "Lead engine": "M3 5h18l-7 8v6l-4 2v-8z",
  "Sales engine": "M3 17l6-6 4 4 8-8M15 7h6v6",
  "Conversion engine": "M4 4h16l-6 7v5l-4 4v-9z",
  "Operations engine": "M12 8.5a3.5 3.5 0 1 0 0 7 3.5 3.5 0 0 0 0-7zM12 2.5v3M12 18.5v3M2.5 12h3M18.5 12h3M5.3 5.3l2.1 2.1M16.6 16.6l2.1 2.1M5.3 18.7l2.1-2.1M16.6 7.4l2.1-2.1",
  "Brand engine": "M12 3l2.6 5.6 6 .7-4.5 4.1 1.2 6L12 16.4 6.7 19.4l1.2-6L3.4 9.3l6-.7z",
  "Data engine": "M12 3c4.4 0 8 1.3 8 3s-3.6 3-8 3-8-1.3-8-3 3.6-3 8-3zM4 6v6c0 1.7 3.6 3 8 3s8-1.3 8-3V6M4 12v6c0 1.7 3.6 3 8 3s8-1.3 8-3v-6",
  More: "M5 12h.01M12 12h.01M19 12h.01",
  Account: "M12 12a4 4 0 1 0 0-8 4 4 0 0 0 0 8zM4 21c.9-4 4-6 8-6s7.1 2 8 6",
  search: "M10.5 18a7.5 7.5 0 1 0 0-15 7.5 7.5 0 0 0 0 15zM21 21l-5.2-5.2",
  inbox: "M3 13.5h5l1.5 2.5h5l1.5-2.5h5M5.2 5h13.6L21 13.5V19a1 1 0 0 1-1 1H4a1 1 0 0 1-1-1v-5.5z",
  menu: "M4 7h16M4 12h16M4 17h16",
  send: "M21 3 10 14M21 3l-7 18-4-7-7-4z",
  logout: "M14 4h4a2 2 0 0 1 2 2v12a2 2 0 0 1-2 2h-4M9 16l-4-4 4-4M5 12h10",
};
export function Icon({ name, ...p }) {
  return (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true" {...p}>
      <path d={PATHS[name] || PATHS.Structure} />
    </svg>
  );
}

const ADMIN_ITEMS = [
  ["Pulse", "/admin", "Sign-ups, revenue and who needs a call"], ["Features & plans", "/admin/features", "Which plan each feature is on"],
  ["Live event", "/admin/event", "The event link, its QR and the room's numbers"], ["Day-1 reports", "/admin/day1", "The ₹99 webinar report"],
  ["Manage member calls", "/admin/calls", "Schedule member calls"], ["Manage recordings", "/admin/recordings", "Add and edit recordings"],
  ["Compliance rules", "/admin/compliance", "The rules behind every compliance date"], ["Email", "/admin/email", "Sending, bounces and the email set-up"],
  ["Onboarding", "/admin/onboarding", "Mark paid, setup checklists, calls and links members see"], ["Engine Audit", "/admin/audit", "Everyone's Engine Audit"],
];

/** Every page this person can see, flat: [{label, path, group, what, locked, tier_name}]. Admin pages first for admins. */
export function allPages(me) {
  const admin = me.user.role === "admin" ? ADMIN_ITEMS.map(([label, path, what]) => ({ key: `a-${path}`, label, path, what, group: "Admin" })) : [];
  return [...admin, ...(me.menu || []).flatMap(({ group, items }) => items.map((it) => ({ ...it, group })))];
}

/** The page the address belongs to: an exact path first, then the longest one it sits under ("/inquiries/new" → Inquiries). */
export function findPage(pages, pathname) {
  let best = null;
  for (const p of pages) {
    if (pathname === p.path) return p;
    if (pathname.startsWith(p.path + "/") && (!best || p.path.length > best.path.length)) best = p;
  }
  return best;
}

// Words owners use for a page that its name doesn't say
const ALSO = {
  gst: "compliance tax", tax: "compliance", tds: "compliance", invoice: "quotations invoices", bill: "expenses quotations money owed", quote: "quotations",
  payment: "money owed razorpay", pay: "money owed", due: "money owed", owed: "money", udhaar: "money owed", cash: "cash flow",
  poster: "launch kit brand kit status", logo: "brand kit", colour: "brand kit", color: "brand kit", post: "writer content calendar",
  instagram: "content calendar writer", staff: "team ai staff", employee: "team roles hiring", hire: "hiring", sales: "pipeline crm",
  customer: "customers crm", enquiry: "inquiries leads", inquiry: "inquiries leads", site: "website", web: "website", whatsapp: "chats leads",
  score: "business score", sop: "sop library", process: "sop library", call: "member call recordings", video: "recordings",
  expense: "expenses", kharcha: "expenses", spend: "expenses", receipt: "expenses", bank: "bank statement", passbook: "bank statement",
  statement: "bank statement", tally: "customer risk getting paid", excel: "customer risk getting paid", rival: "competitor watch",
  review: "reviews", rating: "reviews", google: "reviews", lost: "win-back", inactive: "win-back", research: "content & company research",
};

function score(p, q) {
  const label = p.label.toLowerCase();
  const hay = `${label} ${(p.sub || "").toLowerCase()} ${(p.what || "").toLowerCase()} ${p.group.toLowerCase()}`;
  if (label === q) return 1000;
  if (label.startsWith(q)) return 800;
  if (label.includes(q)) return 600;
  const words = q.split(/\s+/).filter(Boolean);
  let s = 0;
  for (const w of words) {
    const extra = ALSO[w] || Object.entries(ALSO).find(([k]) => k.startsWith(w) && w.length >= 3)?.[1];
    if (label.split(/\W+/).some((x) => x.startsWith(w))) s += 120;
    else if (hay.includes(w)) s += 50;
    else if (extra && extra.split(" ").some((x) => label.includes(x))) s += 90;
    else return 0;
  }
  return s;
}

const RECENT = "ah_recent";
export function rememberPage(path) {
  try {
    const cur = JSON.parse(localStorage.getItem(RECENT) || "[]").filter((x) => x !== path);
    localStorage.setItem(RECENT, JSON.stringify([path, ...cur].slice(0, 6)));
  } catch { /* private mode: no recent list */ }
}
const recent = () => { try { return JSON.parse(localStorage.getItem(RECENT) || "[]"); } catch { return []; } };

/** Go to: type a page's name or what you want to do, arrows to move, Enter to open. */
export function Finder({ me, onClose }) {
  const [q, setQ] = useState("");
  const [at, setAt] = useState(0);
  const nav = useNavigate();
  const input = useRef(null);
  const list = useRef(null);
  const pages = useMemo(() => allPages(me), [me]);

  const sections = useMemo(() => {
    const s = q.trim().toLowerCase();
    if (s) {
      const hits = pages.map((p) => [score(p, s), p]).filter(([n]) => n > 0)
        .sort((a, b) => b[0] - a[0] || Number(a[1].locked) - Number(b[1].locked)).slice(0, 12).map(([, p]) => p);
      return hits.length ? [["Best matches", hits]] : [];
    }
    const seen = recent().map((path) => pages.find((p) => p.path === path && !p.locked)).filter(Boolean);
    const open = pages.filter((p) => !p.locked && !seen.includes(p));
    return [...(seen.length ? [["Recently opened", seen]] : []), ["All pages", open]];
  }, [q, pages]);
  const flat = sections.flatMap(([, items]) => items);

  useEffect(() => { input.current?.focus(); }, []);
  useEffect(() => { setAt(0); }, [q]);
  useEffect(() => { list.current?.querySelector(".finder-item.on")?.scrollIntoView({ block: "nearest" }); }, [at]);

  const go = (p) => { if (!p) return; onClose(); nav(p.path); };
  const key = (e) => {
    if (e.key === "ArrowDown") { e.preventDefault(); setAt((i) => Math.min(i + 1, flat.length - 1)); }
    else if (e.key === "ArrowUp") { e.preventDefault(); setAt((i) => Math.max(i - 1, 0)); }
    else if (e.key === "Enter") { e.preventDefault(); go(flat[at]); }
    else if (e.key === "Escape") { e.preventDefault(); onClose(); }
  };
  let n = -1;
  return (
    <>
      <div className="finder-scrim" onClick={onClose} />
      <div className="finder" role="dialog" aria-modal="true" aria-label="Go to a page">
        <div className="finder-in">
          <Icon name="search" />
          <input ref={input} value={q} onChange={(e) => setQ(e.target.value)} onKeyDown={key} placeholder="Go to… try “GST”, “money owed” or “poster”"
            aria-label="Search pages" role="combobox" aria-expanded="true" aria-controls="finder-list" aria-activedescendant={flat[at] ? `fi-${at}` : undefined} />
          <kbd>Esc</kbd>
        </div>
        <div className="finder-list" id="finder-list" role="listbox" ref={list}>
          {flat.length === 0 && <p className="finder-empty">No page matches “{q}”. Try a shorter word, like “lead” or “quote”.</p>}
          {sections.map(([title, items]) => (
            <React.Fragment key={title}>
              <div className="finder-sec">{title}</div>
              {items.map((p) => {
                n += 1; const i = n;
                return (
                  <a key={p.key || p.path} id={`fi-${i}`} role="option" aria-selected={i === at} href={p.path} className={`finder-item ${i === at ? "on" : ""}`}
                    onMouseMove={() => setAt(i)} onClick={(e) => { e.preventDefault(); go(p); }}>
                    <b>{p.label}</b>
                    {p.locked ? <span className="where lock">On {p.tier_name}</span> : <span className="where">{p.group}</span>}
                    {p.what && <span className="what">{p.what}</span>}
                  </a>
                );
              })}
            </React.Fragment>
          ))}
        </div>
        <div className="finder-foot"><span><kbd>↑</kbd><kbd>↓</kbd>to move</span><span><kbd>Enter</kbd>to open</span><span><kbd>/</kbd>opens this from any page</span></div>
      </div>
    </>
  );
}
