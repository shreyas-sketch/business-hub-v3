import React, { useCallback, useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../api.js";
import { Busy, Locked, Sheet, canAct, useHub, useLoad } from "../ui.jsx";
import { Pill } from "../v2.jsx";
import "./free.css";

/** Loads only when `path` is set (a feature this person has); null otherwise. */
function useMaybe(path) {
  const [data, setData] = useState(null);
  const load = useCallback(() => (path ? api(path).then(setData).catch(() => {}) : Promise.resolve()), [path]);
  useEffect(() => { load(); }, [load]);
  return [data, load];
}

export default function Home() {
  const { me, refresh } = useHub();
  const [leads] = useLoad("/leads");
  const owner = me.workspace.role === "owner";
  const [week, reloadWeek] = useMaybe(me.features.week && canAct(me, "manager") ? "/week" : null);
  const [score] = useMaybe(owner && me.features.business_score ? "/score" : null);
  const latest = score?.latest;

  const steps = [
    ["profile", "Structure", "Business Brain", "/start", me.progress.profile],
    ["brand", "Structure", "Brand message", "/brand", me.progress.brand],
    ["site_live", "Systems", "Website live", "/website", me.progress.site_live],
    ["first_lead", "Systems", "First lead", "/leads", me.progress.first_lead],
    ...(owner && me.features.business_score ? [["score", "Structure", "Business Score", "/score", !!latest]] : []),
  ];
  const nextIdx = steps.findIndex((s) => !s[4]);
  const next = nextIdx === -1 ? null : {
    profile: { title: <>Set up your <em>Business Brain</em>.</>, text: "Read it from your website, say it in a voice note, or answer four short questions. Everything the AI writes comes from it.", to: "/start", cta: "Set up my business" },
    brand: { title: <>Choose your <em>brand message</em>.</>, text: "Three options are waiting. Pick one and edit it — it becomes your website headline.", to: "/brand", cta: "Choose brand message" },
    site_live: { title: <>Put your website <em>live</em>.</>, text: "The AI writes it from your Business Brain. Check it, then publish — inquiries go straight to your WhatsApp.", to: "/website", cta: "Build my website" },
    first_lead: me.features.launch_kit && owner
      ? { title: <>Get your first <em>lead</em>.</>, text: "Post your WhatsApp Status poster, send your visiting card and put the QR stand on your counter. Customers fill the form; you get an alert.", to: "/launch-kit", cta: "Open my launch kit" }
      : { title: <>Get your first <em>lead</em>.</>, text: "Share your website link on WhatsApp Status, your groups and your Google listing. Customers fill the form; you get an alert.", to: "/website", cta: "Share my website" },
    score: { title: <>Check your Business <em>Score</em>.</>, text: "10 quick questions. Get your score out of 100 and the 3 fixes that matter most.", to: "/score", cta: "Get my score" },
  }[steps[nextIdx][0]];
  const first = (me.user.name || "").trim().split(/\s+/)[0];  // the person's own name only, never a word of the business name
  const today = week?.today;
  const streak = week?.streak;

  const newLeads = leads?.filter((l) => l.status === "new").length || 0;
  const usable = new Set((me.menu || []).flatMap((g) => g.items.filter((it) => !it.locked).map((it) => it.key)));
  const groupOf = Object.fromEntries((me.menu || []).flatMap((g) => g.items.map((it) => [it.key, [g.group, it]])));
  // the pages owners open most, in this order, among the ones this person has
  const shortcuts = ["inquiry_desk", "leads", "website", "ai_writer", "quotations", "money_owed", "customers", "content_calendar", "brand_kit", "cash_flow",
    "compliance_calendar", "launch_kit"].filter((k) => usable.has(k)).slice(0, 6).map((k) => groupOf[k]);
  const waiting = [
    newLeads > 0 && [me.features.inquiry_desk ? "/inquiries" : "/leads", newLeads, `${newLeads === 1 ? "Inquiry" : "Inquiries"} to reply to`],
    me.approvals_waiting > 0 && ["/approvals", me.approvals_waiting, `${me.approvals_waiting === 1 ? "Message" : "Messages"} in your Send list`],
    me.my_open_tasks > 0 && ["/tasks", me.my_open_tasks, `${me.my_open_tasks === 1 ? "Task" : "Tasks"} for you`],
  ].filter(Boolean);

  return (
    <Sheet kicker="Today"
      title={nextIdx === -1 ? <>Your hub is <em>working</em>.</> : <>{first ? `${first}, here's` : "Here's"} your next <em>step</em>.</>}
      sub={nextIdx === -1 ? `${new Date().toLocaleDateString("en-IN", { weekday: "long", day: "numeric", month: "long" })}. Here's what needs you today.` : null}>
      {me.notices.length > 0 && (
        <div className="notice">
          <span>{me.notices[0].text}</span>
          <button className="btn sm ghost" onClick={() => api("/notices/read", { method: "POST", body: { ids: [me.notices[0].id] } }).then(refresh)}>Got it</button>
        </div>
      )}

      <div className="home-grid">
        <div className="home-col">
          {today && (
            <div className={`panel ${today.done || streak?.done_today ? "" : "active"}`}>
              <div className="row between">
                <span className="label">Today's 5-minute action</span>
                {streak && <span className="small muted">Streak <b style={{ color: "var(--paper)" }}>{streak.current} {streak.current === 1 ? "day" : "days"}</b>, best {streak.best}</span>}
              </div>
              <h3>{today.text}</h3>
              <div className="row">
                {today.done || streak?.done_today
                  ? <Pill kind="on">Done for today</Pill>
                  : <>
                      {today.link && <Link className="btn sm" to={today.link}>Do it now</Link>}
                      <Busy className="btn sm primary" run={async () => { await api("/week/today/done", { method: "POST" }); await reloadWeek(); }}>Mark done</Busy>
                    </>}
                <Link className="small" to="/week">This week's plan</Link>
              </div>
            </div>
          )}

          {next ? (
            <div className="panel active">
              <span className="label">Next step</span>
              <h2>{next.title}</h2>
              <p className="muted">{next.text}</p>
              <div><Link className="btn primary" to={next.to}>{next.cta}</Link></div>
            </div>
          ) : (
            <div className="panel">
              <span className="label">Keep it running</span>
              <h2>Post this week, reply fast, keep the site fresh.</h2>
              <div className="row"><Link className="btn" to="/writer">Write this week's posts</Link><Link className="btn ghost" to="/leads">Open leads</Link></div>
            </div>
          )}

          <div className={`progress ${steps.length === 5 ? "five" : ""}`} aria-label="Getting started">
            {steps.map(([k, pillar, label, to, done], i) => (
              <Link key={k} to={to} className={done ? "done" : i === nextIdx ? "now" : ""} style={{ color: "inherit", textDecoration: "none" }}>
                <span className="state">{done ? "Done" : i === nextIdx ? "Next" : pillar}</span>
                <span>{label}</span>
              </Link>
            ))}
          </div>

          {shortcuts.length > 0 && (
            <div className="stack" style={{ gap: 10 }}>
              <span className="label">Open</span>
              <div className="shortcuts">
                {shortcuts.map(([, it]) => <Link key={it.key} to={it.path}>{it.label}</Link>)}
              </div>
            </div>
          )}
        </div>

        <div className="home-col">
          <div className="panel">
            <span className="label">Waiting for you</span>
            {waiting.length ? (
              <div className="waitlist">
                {waiting.map(([to, n, text]) => <Link key={to} to={to}><span>{text}</span><span className="n">{n}</span></Link>)}
              </div>
            ) : <p className="small muted">Nothing waiting. New inquiries and messages to send show here.</p>}
            {me.features.agentic_office && canAct(me, "manager") && <Link className="btn sm ghost" to="/office">Give the office an instruction</Link>}
          </div>

          <div className="figures">
            <Link to="/leads"><span className="label">Leads</span><span className="num">{leads ? leads.length : "—"}</span>
              <span className="small muted">{newLeads} new</span></Link>
            <div><span className="label">AI tokens left</span><span className="num aqua">{me.runs.left}</span>
              <span className="small muted">{me.runs.used} of {me.runs.allowance} used{me.runs.bonus ? `, ${me.runs.bonus} bonus` : ""}</span></div>
            <Link to="/website" className="wide"><span className="label">Website</span>
              <span style={{ fontWeight: 600, fontSize: 18 }}>{me.site?.status === "live" ? "Live" : me.site ? "Draft" : "Not built yet"}</span>
              <span className="small" style={{ color: "var(--aqua)" }}>{me.site?.status === "live" ? me.site.url.replace(/^https?:\/\//, "") : "Open website"}</span></Link>
          </div>

          {score && (
            <div className="panel">
              {latest ? <>
                <div className="row between">
                  <span className="label">Business AI Score</span>
                  <span className="small muted">{latest.band}</span>
                </div>
                <div className="fr-score"><span className="bigscore" style={{ fontSize: 48 }}>{latest.score}</span><span className="of">/100</span></div>
                <span className="small muted">{latest.fixes?.[0] ? `Top fix: ${latest.fixes[0].about}` : "Nothing to fix right now."}</span>
                <div><Link className="btn sm" to="/score">See my score</Link></div>
              </> : <>
                <span className="label">Business AI Score</span>
                <span className="small muted">10 quick questions give your score out of 100 and the 3 fixes that matter most. Free.</span>
                <div><Link className="btn sm" to="/score">Get my score</Link></div>
              </>}
            </div>
          )}
        </div>
      </div>

      {!me.features.week && owner && <Locked feature="week" what="A 5-minute action every day, your streak and a weekly plan with 7 posts" />}
      {me.user.trial_until && (
        <p className="small muted">Membership features are on until {new Date(me.user.trial_until).toLocaleDateString("en-IN", { day: "numeric", month: "long" })}.</p>
      )}
    </Sheet>
  );
}
