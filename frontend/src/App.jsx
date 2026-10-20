import React, { Suspense, lazy, useCallback, useEffect, useState } from "react";
import { Navigate, NavLink, Route, Routes, useLocation, useNavigate } from "react-router-dom";
import { api, rupees, setLimitHandler, setLockedHandler, localPhone } from "./api.js";
import { Copy, Gate, HubCtx, Modal } from "./ui.jsx";
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

function Shell({ me, children }) {
  const [open, setOpen] = useState(false);
  const loc = useLocation();
  useEffect(() => setOpen(false), [loc.pathname]);
  // keep the current page visible in a long menu
  useEffect(() => { document.querySelector(".nav a.on")?.scrollIntoView({ block: "nearest" }); }, [loc.pathname]);
  const logout = async () => { await api("/auth/logout", { method: "POST" }); window.location.href = "/"; };
  return (
    <div className="shell">
      <div className="topbar">
        <div className="mark"><b>{me.business_name || "Action Hub"}</b><span>{me.workspace.is_team ? me.workspace.role : me.user.plan_name}</span></div>
        <button className="btn sm ghost" onClick={() => setOpen(true)}>Menu</button>
      </div>
      <aside className={`rail ${open ? "open" : ""}`}>
        <div className="row between">
          <div className="mark"><b>{me.business_name || "Your business"}</b><span>{me.workspace.is_team ? `${me.user.name || "Team"} · ${me.workspace.role}` : me.program.name}</span></div>
          {open && <button className="btn sm ghost" onClick={() => setOpen(false)}>Close</button>}
        </div>
        <nav className="nav" aria-label="Main">
          {me.user.role === "admin" && <><div className="group">Admin</div><NavLink to="/admin" end>Pulse</NavLink><NavLink to="/admin/features">Features &amp; plans</NavLink>
            <NavLink to="/admin/event">Live event</NavLink><NavLink to="/admin/day1">Day-1 reports</NavLink><NavLink to="/admin/calls">Member calls</NavLink><NavLink to="/admin/recordings">Recordings</NavLink>
            <NavLink to="/admin/compliance">Compliance rules</NavLink><NavLink to="/admin/email">Email</NavLink></>}
          {(me.menu || []).map(({ group, items }) => (
            <React.Fragment key={group}>
              <div className="group">{group}</div>
              {items.map((it) => (
                <NavLink key={it.key} to={it.path} className={({ isActive }) => `${isActive ? "on" : ""} ${it.locked ? "locked" : ""}`}>
                  <span>{it.label}{it.sub && <small className="nav-sub">{it.sub}</small>}</span>
                  {it.locked ? <span className="tier" title={`Unlocks with ${it.tier_name}`}>{it.tier_short || it.tier_name.split(" ")[0]}</span>
                    : it.path === "/approvals" && me.approvals_waiting ? <span className="count" aria-label={`${me.approvals_waiting} waiting`}>{me.approvals_waiting}</span>
                    : it.path === "/tasks" && me.my_open_tasks ? <span className="count" aria-label={`${me.my_open_tasks} open`}>{me.my_open_tasks}</span>
                    : it.path === "/leads" && me.progress.first_lead ? <span className="muted">●</span> : null}
                </NavLink>
              ))}
            </React.Fragment>
          ))}
        </nav>
        <div className="foot">
          <div className="stack" style={{ gap: 6 }}>
            <span className="label">AI runs this month</span>
            <div className="meter"><i style={{ width: `${Math.min(100, (me.runs.left / Math.max(me.runs.allowance + me.runs.bonus, 1)) * 100)}%` }} /></div>
            <span className="small muted">{me.runs.left} left · {me.user.plan_name}</span>
          </div>
          <button className="btn sm ghost" onClick={logout}>Log out</button>
        </div>
      </aside>
      <main className="main">{children}</main>
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
