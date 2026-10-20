import React, { Suspense, lazy, useCallback, useEffect, useState } from "react";
import { Navigate, NavLink, Route, Routes, useLocation, useNavigate } from "react-router-dom";
import { api, rupees, setLimitHandler, setLockedHandler, localPhone } from "./api.js";
import { Copy, Gate, HubCtx, Modal } from "./ui.jsx";
import { Finder, Icon, allPages, findPage, rememberPage } from "./shell.jsx";
import "./app-theme.css";
import Landing from "./pages/Landing.jsx";
import Login from "./pages/Login.jsx";
import Onboarding from "./pages/Onboarding.jsx";
import Home from "./pages/Home.jsx";
const Brand = lazy(() => import("./pages/Brand.jsx"));
const Website = lazy(() => import("./pages/Website.jsx"));
const Leads = lazy(() => import("./pages/Leads.jsx"));
const Writer = lazy(() => import("./pages/Writer.jsx"));
const Outputs = lazy(() => import("./pages/Outputs.jsx"));
const Invite = lazy(() => import("./pages/Invite.jsx"));
const Plans = lazy(() => import("./pages/Plans.jsx"));
const Admin = lazy(() => import("./pages/Admin.jsx"));
const AdminFeatures = lazy(() => import("./pages/AdminFeatures.jsx"));
const AdminOwner = lazy(() => import("./pages/AdminOwner.jsx"));
const AdminCalls = lazy(() => import("./pages/AdminCalls.jsx"));
const Goals = lazy(() => import("./pages/Goals.jsx"));
const Insights = lazy(() => import("./pages/Insights.jsx"));
const Team = lazy(() => import("./pages/Team.jsx"));
const Tasks = lazy(() => import("./pages/Tasks.jsx"));
const Meetings = lazy(() => import("./pages/Meetings.jsx"));
const Approvals = lazy(() => import("./pages/Approvals.jsx"));
const Agents = lazy(() => import("./pages/Agents.jsx"));
const Office = lazy(() => import("./pages/Office.jsx"));
const Money = lazy(() => import("./pages/Money.jsx"));
const Recordings = lazy(() => import("./pages/Recordings.jsx"));
const AdminRecordings = lazy(() => import("./pages/AdminRecordings.jsx"));
const Score = lazy(() => import("./pages/Score.jsx"));
const LaunchKit = lazy(() => import("./pages/LaunchKit.jsx"));
const Week = lazy(() => import("./pages/Week.jsx"));
const Calendar = lazy(() => import("./pages/Calendar.jsx"));
const MemberCall = lazy(() => import("./pages/MemberCall.jsx"));
const Board = lazy(() => import("./pages/Board.jsx"));
const Tools = lazy(() => import("./pages/Tools.jsx"));
const Magic = lazy(() => import("./pages/Magic.jsx"));
const Gaps = lazy(() => import("./pages/Gaps.jsx"));
const LeadMagnet = lazy(() => import("./pages/LeadMagnet.jsx"));
const Offers = lazy(() => import("./pages/Offers.jsx"));
const Levers = lazy(() => import("./pages/Levers.jsx"));
const Portfolio = lazy(() => import("./pages/Portfolio.jsx"));
const Pipeline = lazy(() => import("./pages/Pipeline.jsx"));
const Leaks = lazy(() => import("./pages/Leaks.jsx"));
const EndGoals = lazy(() => import("./pages/EndGoals.jsx"));
const Crm = lazy(() => import("./pages/Crm.jsx"));
const Customers = lazy(() => import("./pages/Customers.jsx"));
const Quotes = lazy(() => import("./pages/Quotes.jsx"));
const Setup = lazy(() => import("./pages/Setup.jsx"));
const Dashboard = lazy(() => import("./pages/Dashboard.jsx"));
const Knowledge = lazy(() => import("./pages/Knowledge.jsx"));
const Compliance = lazy(() => import("./pages/Compliance.jsx"));
const CashFlow = lazy(() => import("./pages/CashFlow.jsx"));
const BrandKit = lazy(() => import("./pages/BrandKit.jsx"));
const AdminCompliance = lazy(() => import("./pages/AdminCompliance.jsx"));
const AdminEmail = lazy(() => import("./pages/AdminEmail.jsx"));
const Inquiries = lazy(() => import("./pages/Inquiries.jsx"));
const Staff = lazy(() => import("./pages/Staff.jsx"));
const Chats = lazy(() => import("./pages/Chats.jsx"));
const Connections = lazy(() => import("./pages/Connections.jsx"));
const CompanyBrain = lazy(() => import("./pages/CompanyBrain.jsx"));
const Campaigns = lazy(() => import("./pages/Campaigns.jsx"));
const Gym = lazy(() => import("./pages/Gym.jsx"));
const Results = lazy(() => import("./pages/Results.jsx"));
const Workforce = lazy(() => import("./pages/Workforce.jsx"));
const Day1 = lazy(() => import("./pages/Day1.jsx"));
const Event = lazy(() => import("./pages/Event.jsx"));
const AdminEvent = lazy(() => import("./pages/AdminEvent.jsx"));
const AdminDay1 = lazy(() => import("./pages/AdminDay1.jsx"));
const kit = (name) => lazy(() => import("./pages/Kits.jsx").then((m) => ({ default: m[name] })));
const [Competence, Culture, Decisions, Hiring, Reviews, Roles, Sops] = ["Competence", "Culture", "Decisions", "Hiring", "Reviews", "Roles", "Sops"].map(kit);

/* Every page: [path, component, the feature that unlocks it (null = every plan), lowest role that may open it].
   The menu itself comes from the server (me.menu), so the admin's order, names and switches show at once. */
const PAGES = [
  ["/home", Home, null, "staff"], ["/recordings", Recordings, "recordings", "staff"], ["/recordings/:id", Recordings, "recordings", "staff"],
  ["/brand", Brand, "brand_message", "owner"], ["/score", Score, "business_score", "owner"], ["/website", Website, "website", "owner"],
  ["/leads", Leads, "leads", "staff"], ["/writer", Writer, "ai_writer", "staff"], ["/outputs", Outputs, "outputs", "staff"],
  ["/launch-kit", LaunchKit, "launch_kit", "owner"], ["/invite", Invite, "invite", "owner"], ["/plans", Plans, null, "owner"],
  ["/week", Week, "week", "manager"], ["/calendar", Calendar, "content_calendar", "staff"], ["/member-call", MemberCall, "member_call", "owner"],
  ["/board", Board, "cohort_board", "owner"], ["/tools", Tools, "ai_tools", "staff"], ["/sops", Sops, "sop_library", "staff"],
  ["/magic", Magic, "magic_number", "manager"], ["/gaps", Gaps, "gaps_scan", "owner"], ["/insights", Insights, "funnel", "manager"],
  ["/approvals", Approvals, "approvals", "manager"], ["/agents", Agents, "automations", "manager"], ["/lead-magnet", LeadMagnet, "lead_magnet", "owner"],
  ["/offers", Offers, "offer_ladder", "owner"], ["/goals", Goals, "goals", "manager"], ["/levers", Levers, "revenue_levers", "manager"],
  ["/portfolio", Portfolio, "customer_portfolio", "manager"], ["/pipeline", Pipeline, "pipeline", "staff"], ["/leaks", Leaks, "funnel_leaks", "manager"],
  ["/end-goals", EndGoals, "end_goals", "owner"], ["/decisions", Decisions, "decision_log", "owner"], ["/hiring", Hiring, "hiring", "manager"],
  ["/crm", Crm, "crm_sync", "owner"], ["/customers", Customers, "customers", "manager"], ["/money", Money, "money_owed", "manager"],
  ["/quotes", Quotes, "quotations", "manager"], ["/setup", Setup, "setup_tracker", "owner"], ["/dashboard", Dashboard, "smart_dashboard", "manager"],
  ["/knowledge", Knowledge, "business_brain", "owner"], ["/compliance", Compliance, "compliance_calendar", "manager"], ["/cash-flow", CashFlow, "cash_flow", "manager"],
  ["/brand-kit", BrandKit, "brand_kit", "owner"], ["/inquiries", Inquiries, "inquiry_desk", "staff"], ["/inquiries/new", Inquiries, "inquiry_desk", "staff"],
  ["/staff", Staff, "ai_staff", "manager"], ["/chats", Chats, "whatsapp_ai", "staff"], ["/connections", Connections, "connections", "owner"],
  ["/company-brain", CompanyBrain, "company_brain", "manager"], ["/campaigns", Campaigns, "campaigns", "manager"], ["/gym", Gym, "training_gym", "staff"],
  ["/results", Results, "results_report", "owner"], ["/team", Team, "team_logins", "owner"], ["/roles", Roles, "role_clarity", "staff"],
  ["/culture", Culture, "culture_plan", "staff"], ["/competence", Competence, "competence_plan", "staff"], ["/meetings", Meetings, "meetings", "staff"],
  ["/tasks", Tasks, "tasks", "staff"], ["/reviews", Reviews, "monthly_review", "manager"], ["/workforce", Workforce, "workforce", "owner"],
  ["/office", Office, "agentic_office", "manager"],
];

const NAV_KEY = "ah_nav_open";
const readNav = () => { try { return JSON.parse(localStorage.getItem(NAV_KEY) || "{}"); } catch { return {}; } };

/** The menu: groups fold, usable pages first, locked ones tucked under "N more", and the current page's group always open. */
function Nav({ me, pathname }) {
  const [saved, setSaved] = useState(readNav);
  const toggle = (key, value) => setSaved((cur) => {
    const next = { ...cur, [key]: value };
    try { localStorage.setItem(NAV_KEY, JSON.stringify(next)); } catch { /* private mode: just this visit */ }
    return next;
  });
  const isOn = (path) => (path === "/admin" ? pathname === "/admin" : pathname === path || pathname.startsWith(path + "/"));
  const admin = me.user.role === "admin" ? [{ group: "Admin", items: [
    { key: "a-pulse", label: "Pulse", path: "/admin" }, { key: "a-features", label: "Features & plans", path: "/admin/features" },
    { key: "a-event", label: "Live event", path: "/admin/event" }, { key: "a-day1", label: "Day-1 reports", path: "/admin/day1" },
    { key: "a-calls", label: "Manage member calls", path: "/admin/calls" }, { key: "a-rec", label: "Manage recordings", path: "/admin/recordings" },
    { key: "a-compliance", label: "Compliance rules", path: "/admin/compliance" }, { key: "a-email", label: "Email", path: "/admin/email" }] }] : [];
  const groups = [...admin, ...(me.menu || [])];
  const usable = groups.reduce((n, g) => n + g.items.filter((it) => !it.locked).length, 0);
  const crowded = usable > 24; // with most of the menu unlocked, start with only the current group and Start open

  return (
    <nav className="nav" aria-label="Main">
      {groups.map(({ group, items }, gi) => {
        const open_ = items.filter((it) => !it.locked);
        const locked = items.filter((it) => it.locked);
        const here = items.some((it) => isOn(it.path));
        const byDefault = group === "Admin" || (crowded ? here || group === "Start" : open_.length > 0 || here);
        const expanded = here || (saved[group] ?? byDefault);
        const showLocked = saved[`${group}:locked`] ?? false;
        const link = (it) => (
          <NavLink key={it.key} to={it.path} end={it.path === "/admin"} className={({ isActive }) => `${isActive ? "on" : ""} ${it.locked ? "locked" : ""}`}>
            <span>{it.label}{it.sub && <small className="nav-sub">{it.sub}</small>}</span>
            {it.locked ? <span className="tier" title={`Unlocks with ${it.tier_name}`}>{it.tier_short || it.tier_name.split(" ")[0]}</span>
              : it.path === "/approvals" && me.approvals_waiting ? <span className="count" aria-label={`${me.approvals_waiting} waiting`}>{me.approvals_waiting}</span>
              : it.path === "/tasks" && me.my_open_tasks ? <span className="count" aria-label={`${me.my_open_tasks} open`}>{me.my_open_tasks}</span>
              : it.path === "/leads" && me.progress.first_lead ? <span className="dot" aria-hidden="true" /> : null}
          </NavLink>
        );
        return (
          <div key={group} className={`nav-group ${expanded ? "open" : ""}`}>
            <button type="button" className="group" aria-expanded={expanded} onClick={() => !here && toggle(group, !expanded)} disabled={here}>
              <span><Icon name={group} />{group}</span>
              <span className="meta">{!expanded && <span className="n">{open_.length || items.length}</span>}{!here && <i className="chev" aria-hidden="true" />}</span>
            </button>
            {expanded && (
              <div className="items">
                {open_.map(link)}
                {locked.length > 0 && (open_.length === 0 || showLocked || locked.some((it) => isOn(it.path))
                  ? <>{locked.map(link)}{open_.length > 0 && <button type="button" className="more" onClick={() => toggle(`${group}:locked`, false)}>Hide locked</button>}</>
                  : <button type="button" className="more" onClick={() => toggle(`${group}:locked`, true)}>+ {locked.length} more on bigger plans</button>)}
              </div>
            )}
            {gi === admin.length - 1 && admin.length > 0 && <hr className="nav-rule" />}
          </div>
        );
      })}
    </nav>
  );
}

function Shell({ me, children }) {
  const [open, setOpen] = useState(false);
  const [finding, setFinding] = useState(false);
  const [account, setAccount] = useState(false);
  const loc = useLocation();
  const pages = allPages(me);
  const here = findPage(pages, loc.pathname);
  useEffect(() => { setOpen(false); setAccount(false); if (here && !here.locked) rememberPage(here.path); }, [loc.pathname]);  // eslint-disable-line react-hooks/exhaustive-deps
  // keep the current page visible in a long menu
  useEffect(() => { document.querySelector(".nav a.on")?.scrollIntoView({ block: "nearest" }); }, [loc.pathname]);
  // the workspace look is for the logged-in hub only; public pages keep the blueprint look
  useEffect(() => { document.body.classList.add("app"); return () => document.body.classList.remove("app"); }, []);
  // "/" or Ctrl/⌘ K opens Go to, unless someone is typing
  useEffect(() => {
    const k = (e) => {
      const typing = /^(INPUT|TEXTAREA|SELECT)$/.test(e.target.tagName) || e.target.isContentEditable;
      if ((e.key === "k" && (e.metaKey || e.ctrlKey)) || (e.key === "/" && !typing)) { e.preventDefault(); setFinding(true); }
    };
    window.addEventListener("keydown", k);
    return () => window.removeEventListener("keydown", k);
  }, []);
  // the account menu closes on a click anywhere else, or Esc
  useEffect(() => {
    if (!account) return undefined;
    const click = (e) => { if (!e.target.closest(".me-menu, .me-btn")) setAccount(false); };
    const esc = (e) => { if (e.key === "Escape") setAccount(false); };
    document.addEventListener("mousedown", click); window.addEventListener("keydown", esc);
    return () => { document.removeEventListener("mousedown", click); window.removeEventListener("keydown", esc); };
  }, [account]);
  const logout = async () => { await api("/auth/logout", { method: "POST" }); window.location.href = "/"; };
  const who = me.workspace.is_team ? `${me.user.name || "Team"} · ${me.workspace.role}` : me.program.name;
  const name = me.business_name || "Your business";
  const initials = (me.user.name || name).trim().split(/\s+/).slice(0, 2).map((w) => w[0]).join("").toUpperCase() || "AH";
  const runsPct = Math.min(100, (me.runs.left / Math.max(me.runs.allowance + me.runs.bonus, 1)) * 100);
  const can = (key) => pages.some((p) => p.key === key && !p.locked);
  const leadsTab = can("inquiry_desk") ? ["/inquiries", "Inquiries"] : ["/leads", "Leads"];
  const isOn = (path) => loc.pathname === path || loc.pathname.startsWith(path + "/");
  return (
    <div className="shell">
      <div className="topbar">
        <div className="mark"><b>{me.business_name || "Action Hub"}</b><span>{me.workspace.is_team ? me.workspace.role : me.user.plan_name}</span></div>
        <button className="btn sm ghost" onClick={() => setOpen(true)}>Menu</button>
      </div>
      <aside className={`rail ${open ? "open" : ""}`} aria-label="Menu">
        <div className="row between" style={{ flexWrap: "nowrap" }}>
          <div className="mark"><span className="seal" aria-hidden="true">{name.trim()[0]?.toUpperCase() || "A"}</span><b>{name}</b><span>{who}</span></div>
          {open && <button className="btn sm ghost" onClick={() => setOpen(false)}>Close</button>}
        </div>
        <button type="button" className="find-btn" onClick={() => { setOpen(false); setFinding(true); }}><Icon name="search" />Go to…<kbd>/</kbd></button>
        <Nav me={me} pathname={loc.pathname} />
        <div className="foot">
          <div className="stack" style={{ gap: 6 }}>
            <span className="label">AI runs this month</span>
            <div className="meter"><i style={{ width: `${runsPct}%` }} /></div>
            <span className="small muted">{me.runs.left} left · {me.user.plan_name}</span>
          </div>
          <button className="btn sm ghost" onClick={logout}>Log out</button>
        </div>
      </aside>
      <main className="main">
        <header className="appbar">
          <nav className="crumbs" aria-label="You are here">
            {here ? <><span>{here.group}</span><span className="sep" aria-hidden="true">/</span><b>{here.label}</b></> : <b>{name}</b>}
          </nav>
          <button type="button" className="find" onClick={() => setFinding(true)}><Icon name="search" />Go to a page<kbd>/</kbd></button>
          {me.approvals_waiting > 0 && (
            <NavLink to="/approvals" className="waiting" title="Messages waiting for you to send"><b>{me.approvals_waiting}</b>Send list</NavLink>
          )}
          <button type="button" className="me-btn" aria-haspopup="true" aria-expanded={account} aria-label="Your account" onClick={() => setAccount((a) => !a)}>{initials}</button>
          {account && (
            <div className="me-menu" role="menu">
              <div className="who"><b>{me.user.name || name}</b><span className="small muted">{me.user.plan_name}{me.workspace.is_team ? ` · ${me.workspace.role}` : ""}</span></div>
              <div className="stack" style={{ gap: 6 }}>
                <span className="small muted">AI runs this month: {me.runs.left} left</span>
                <div className="meter"><i style={{ width: `${runsPct}%` }} /></div>
              </div>
              <div>
                {me.workspace.role === "owner" && <NavLink to="/plans" role="menuitem">Plans & billing</NavLink>}
                {can("business_brain") && <NavLink to="/knowledge" role="menuitem">Knowledge Hub</NavLink>}
                <button type="button" className="linkish" role="menuitem" onClick={logout}>Log out</button>
              </div>
            </div>
          )}
        </header>
        <div className="desk">{children}</div>
      </main>
      <nav className="tabbar" aria-label="Shortcuts">
        <NavLink to={me.landing || "/home"} className={isOn(me.landing || "/home") ? "on" : ""}><Icon name="Start" />Home</NavLink>
        <NavLink to={leadsTab[0]} className={isOn(leadsTab[0]) ? "on" : ""}><Icon name="inbox" />{leadsTab[1]}</NavLink>
        {can("approvals")
          ? <NavLink to="/approvals" className={isOn("/approvals") ? "on" : ""}><Icon name="send" />Send list{me.approvals_waiting > 0 && <span className="badge">{me.approvals_waiting}</span>}</NavLink>
          : <NavLink to="/website" className={isOn("/website") ? "on" : ""}><Icon name="Systems" />Website</NavLink>}
        <button type="button" onClick={() => setFinding(true)}><Icon name="search" />Go to</button>
      </nav>
      {finding && <Finder me={me} onClose={() => setFinding(false)} />}
    </div>
  );
}

function LimitModal({ detail, me, onClose, upgrade }) {
  return (
    <Modal onClose={onClose}>
      <span className="kicker">AI runs</span>
      <h2>You've used this month's <em>runs</em>.</h2>
      <p className="sub">Two ways to keep going. Your runs refill on the 1st of next month either way.</p>
      <div className="panel">
        <span className="label">Invite another business owner</span>
        <p>{detail.invite.reward}. They start with a month of Membership features.</p>
        <div className="row"><input className="input mono" readOnly value={detail.invite.link} style={{ flex: 1, minWidth: 200 }} /><Copy text={detail.invite.link} /></div>
      </div>
      <div className="panel">
        <span className="label">Or upgrade</span>
        <p>{detail.upgrade.name}: <span className="money">{rupees(detail.upgrade.price_minor)}/{detail.upgrade.period}</span> — {detail.upgrade.runs} runs a month, your weekly plan, the content calendar and ready-made AI tools.</p>
        <button className="btn money" onClick={() => { onClose(); upgrade("lite", "runs"); }}>See Membership</button>
      </div>
      <button className="btn ghost" onClick={onClose}>Not now</button>
    </Modal>
  );
}

export default function App() {
  const [me, setMe] = useState(undefined);
  const [limit, setLimit] = useState(null);
  const [toastMsg, setToast] = useState(null);
  const [contact, setContact] = useState(null);
  const [locked, setLocked] = useState(null);
  const nav = useNavigate();
  const loc = useLocation();

  const refresh = useCallback(() => api("/me").then(setMe).catch(() => setMe(null)), []);
  useEffect(() => { refresh(); }, [refresh, loc.pathname]);
  useEffect(() => { setLimitHandler((d) => setLimit(d)); setLockedHandler((d) => setLocked(d)); }, []);
  const toast = useCallback((msg, kind = "ok") => { setToast({ msg, kind }); setTimeout(() => setToast(null), 4200); }, []);
  // Every upgrade button leads to Plans & billing, where the owner pays in-app (or asks us to call when payments aren't set up).
  const upgrade = useCallback((tier, feature) => {
    api("/upgrade-click", { method: "POST", body: { tier, from_feature: feature } }).catch(() => {});
    nav(`/plans?tier=${tier}`);
  }, [nav]);

  if (me === undefined) return <div className="main muted">Loading…</div>;
  const ctx = { me, refresh, toast, upgrade, nav, setContact };
  const path = loc.pathname;
  const publicPaths = ["/", "/join", "/login"];
  let page;
  if (path === "/report" || path.startsWith("/report/")) {   // the ₹99 webinar tool: no login, whoever is (or isn't) logged in
    page = <Suspense fallback={<div className="main muted">Loading…</div>}><Day1 /></Suspense>;
  } else if (path === "/event" || path.startsWith("/event/")) {   // the live event link: no login until they save their pack
    page = <Suspense fallback={<div className="main muted">Loading…</div>}><Event /></Suspense>;
  } else if (!me) {
    page = publicPaths.includes(path) ? (
      <Routes><Route path="/login" element={<Login />} /><Route path="*" element={<Landing />} /></Routes>
    ) : <Navigate to={`/login?next=${encodeURIComponent(path)}`} replace />;
  } else if (me.blocked) {
    page = (
      <div className="public"><div className="title-sheet"><span className="kicker">Team access</span><h1>Your access is <em>paused</em>.</h1>
        <p className="sub">{me.blocked}</p>
        <button className="btn" onClick={async () => { await api("/auth/logout", { method: "POST" }); window.location.href = "/"; }}>Log out</button></div></div>
    );
  } else if (!me.progress.profile && me.user.role !== "admin" && !me.workspace.is_team && path !== "/start" && path !== "/plans") {
    page = <Navigate to="/start" replace />;
  } else if (path === "/start" && !me.workspace.is_team) {
    page = <Onboarding />;
  } else if (publicPaths.includes(path)) {
    page = <Navigate to={me.user.role === "admin" && !me.progress.profile ? "/admin" : me.landing || "/home"} replace />;
  } else {
    page = (
      <Shell me={me}>
        <Suspense fallback={<div className="muted">Loading…</div>}>
        <Routes>
          {PAGES.map(([path, Page, feature, role]) => (
            <Route key={path} path={path} element={
              <Gate feature={feature} role={role} kicker={me.feature_info?.[feature]?.label || "Locked"} what={me.feature_info?.[feature]?.what}
                title={feature && me.feature_info?.[feature] && !me.feature_info[feature].on ? <>This part of the hub is <em>switched off</em>.</> : undefined}>
                <Page />
              </Gate>} />
          ))}
          <Route path="/studio" element={<Navigate to="/writer" replace />} />
          <Route path="/control" element={<Navigate to="/dashboard" replace />} />{/* the Control Room is the dashboard now */}
          {me.user.role === "admin" && <Route path="/admin" element={<Admin />} />}
          {me.user.role === "admin" && <Route path="/admin/features" element={<AdminFeatures />} />}
          {me.user.role === "admin" && <Route path="/admin/owners/:id" element={<AdminOwner />} />}
          {me.user.role === "admin" && <Route path="/admin/calls" element={<AdminCalls />} />}
          {me.user.role === "admin" && <Route path="/admin/day1" element={<AdminDay1 />} />}
          {me.user.role === "admin" && <Route path="/admin/event" element={<AdminEvent />} />}
          {me.user.role === "admin" && <Route path="/admin/recordings" element={<AdminRecordings />} />}
          {me.user.role === "admin" && <Route path="/admin/compliance" element={<AdminCompliance />} />}
          {me.user.role === "admin" && <Route path="/admin/email" element={<AdminEmail />} />}
          <Route path="*" element={<Navigate to={me.landing || "/home"} replace />} />
        </Routes>
        </Suspense>
      </Shell>
    );
  }
  return (
    <HubCtx.Provider value={ctx}>
      {page}
      {limit && me && <LimitModal detail={limit} me={me} upgrade={upgrade} onClose={() => setLimit(null)} />}
      {locked && me && (
        <Modal onClose={() => setLocked(null)}>
          <span className="kicker">{locked.tier_name}</span>
          <h2>This is part of <em>{locked.tier_name}</em>.</h2>
          <p className="sub">{locked.message}</p>
          <div className="row">
            {me.workspace?.role === "owner" && <button className="btn money" onClick={() => { setLocked(null); upgrade(locked.tier, locked.feature); }}>See {locked.tier_name}</button>}
            <button className="btn ghost" onClick={() => setLocked(null)}>Not now</button>
          </div>
        </Modal>
      )}
      {contact && (
        <Modal onClose={() => setContact(null)}>
          <span className="kicker">Upgrade</span>
          <h2>Our team will <em>call</em> you.</h2>
          <p className="sub">We've noted your interest. Someone from the Business AI team will reach you on {localPhone(me?.user.phone)} to set it up.</p>
          <button className="btn" onClick={() => setContact(null)}>Done</button>
        </Modal>
      )}
      {toastMsg && <div className={`toast ${toastMsg.kind === "err" ? "err" : ""}`} role="status">{toastMsg.msg}</div>}
    </HubCtx.Provider>
  );
}
