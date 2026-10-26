import React, { useEffect, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { api, day, rupees } from "../api.js";
import { checkout, paise } from "../billing.js";
import { Busy, Gate, Sheet, Stat, useHub, useLoad } from "../ui.jsx";
import "./admin.css";

const TIERS = ["free", "lite", "program", "running", "growth", "office"];
const rank = (t) => Math.max(TIERS.indexOf(t), 0);
const LIVE = ["active", "pending", "authenticated", "paused"];
const STATUS = { paid: ["Paid", ""], refunded: ["Refunded", "grey"], created: ["Not completed", "grey"] };
const SUB_STATUS = {
  active: "Active", authenticated: "Set up", pending: "Payment retrying", halted: "Stopped: payments failed",
  paused: "Paused", cancelled: "Cancelled", completed: "Completed", expired: "Expired", created: "Not completed",
};

function whereFrom(b) {
  const sub = b.sub || {};
  if (b.source === "purchase") return `Paid once · access until ${day(b.until)}`;
  if (b.source === "subscription") {
    if (sub.cancel_at_cycle_end || sub.status === "cancelled") return `Membership · ends on ${day(sub.paid_until)}, no more charges`;
    if (sub.status === "pending") return `Membership · your last payment didn't go through; Razorpay is retrying`;
    return `Membership · renews every month${sub.paid_until ? `, next on ${day(sub.paid_until)}` : ""}`;
  }
  if (b.source === "trial") return `Free from your invite · until ${day(b.until)}`;
  return b.plan === "free" ? "Given free at the Business AI workshop" : "Set up for you by the Business AI team";
}

function Membership({ b, buy, reload }) {
  const { toast, refresh } = useHub();
  const sub = b.sub;
  if (!sub || !(LIVE.includes(sub.status) || sub.status === "halted" || (sub.paid_until && new Date(sub.paid_until) > new Date()))) return null;
  const ending = sub.cancel_at_cycle_end || sub.status === "cancelled";
  const bought = b.sources?.purchase;
  const covered = bought && rank(bought) > rank("lite") && b.access?.[bought];
  const notStarted = sub.status === "authenticated" && sub.start_at && new Date(sub.start_at) > new Date();
  const cancel = async () => {
    const when = notStarted ? "Your free month continues; Membership won't start." : `You keep Membership until ${day(sub.paid_until)}. No more charges after that.`;
    if (!window.confirm(`Cancel Membership? ${when}`)) return;
    await api("/billing/cancel", { method: "POST" });
    toast(notStarted ? "Membership cancelled. Nothing will be charged." : `Membership ends on ${day(sub.paid_until)}.`);
    await refresh();
    reload();
  };
  return (
    <div className="panel">
      <div className="row between">
        <span className="label">Membership</span>
        <span className={`token ${ending || sub.status === "halted" ? "grey" : ""}`}>{ending && sub.status === "active" ? "Ending" : SUB_STATUS[sub.status] || sub.status}</span>
      </div>
      {notStarted ? (
        <p>Set up. Your first <span className="money">{rupees(b.ladder[1].price_minor)}</span> charge is on {day(sub.start_at)}, when your free month ends.</p>
      ) : ending ? (
        <p>You keep Membership until {day(sub.paid_until)}. You won't be charged again.</p>
      ) : sub.status === "halted" ? (
        <p>Membership stopped because the monthly payment kept failing. Start it again to continue.</p>
      ) : (
        <p><span className="money">{rupees(b.ladder[1].price_minor)}</span> a month{sub.paid_until ? `, paid up to ${day(sub.paid_until)}` : ""}. Cancel any time; you keep it to the end of the month you paid for.</p>
      )}
      {covered && !ending && LIVE.includes(sub.status) && (
        <p className="small muted">Your {b.ladder[rank(bought)].name} already includes everything in Membership until {day(b.access[bought])}. You can cancel Membership to stop the monthly charge.</p>
      )}
      <div className="row">
        {LIVE.includes(sub.status) && !sub.cancel_at_cycle_end && <Busy className="btn sm ghost" run={cancel}>Cancel Membership</Busy>}
        {(ending || sub.status === "halted") && !covered && <Busy className="btn sm money" run={() => buy("lite")}>Start Membership</Busy>}
      </div>
    </div>
  );
}

/** What a plan adds over the one below it, grouped like the menu. Names and descriptions are the admin's, so changes show here at once. */
function Unlocks({ t, prev, open }) {
  const groups = [];
  for (const u of t.unlocks || []) {
    let g = groups.find((x) => x.name === u.group);
    if (!g) groups.push((g = { name: u.group, items: [] }));
    g.items.push(u);
  }
  if (!groups.length) return null;
  const n = t.unlocks.length;
  return (
    <details className="plan-unlocks" open={open}>
      <summary><span className="label">{prev ? `Everything in ${prev.name}, plus ${n} more` : `What's included · ${n}`}</span></summary>
      <div className="unlock-groups">
        {groups.map((g) => (
          <div key={g.name} className="unlock-group">
            <span className="label">{g.name}</span>
            {g.items.map((u) => <div key={u.key} className="unlock"><b>{u.label}</b>{u.what && <span>{u.what}</span>}</div>)}
          </div>
        ))}
      </div>
    </details>
  );
}

function PlansInner() {
  const hub = useHub();
  const { me, refresh, toast, setContact } = hub;
  const [b, reload] = useLoad("/billing");
  const [params] = useSearchParams();
  const wanted = params.get("tier");

  useEffect(() => {
    if (b && wanted) document.getElementById(`tier-${wanted}`)?.scrollIntoView({ behavior: "smooth", block: "center" });
  }, [b, wanted]);

  if (!b) return <Sheet kicker="Grow · Plans & billing" id="U-01" pillar="Grow" money title={<>One hub. Each level unlocks <em>more</em>.</>} />;

  const buy = async (tier) => {
    const r = await checkout(tier, { me, refresh, toast, setContact });
    if (r.status === "paid") reload();
  };
  const current = b.plan;
  const purchased = b.sources?.purchase;
  const highlight = wanted && TIERS.includes(wanted) ? wanted : current;

  const action = (t) => {
    if (t.tier === "free") return null;
    if (t.tier === "lite") {
      if (b.member) return <span className="token">{b.sub?.status === "authenticated" ? "Set up" : "Active"}</span>;
      if (rank(current) > rank("lite")) return <span className="token grey">Included</span>;
      return <Busy className="btn sm money" run={() => buy("lite")}>Start Membership</Busy>;
    }
    if (rank(t.tier) > rank(current)) return <Busy className="btn sm money" run={() => buy(t.tier)}>Buy</Busy>;
    if (t.tier === purchased) return <Busy className="btn sm ghost" run={() => buy(t.tier)}>Extend</Busy>;
    return <span className="token grey">Included</span>;
  };

  return (
    <Sheet kicker="Grow · Plans & billing" id="U-01" pillar="Grow" money title={<>One hub. Each level unlocks <em>more</em>.</>}
      sub="Everything you build stays yours as you move up. Membership is monthly and you can cancel any time; the programs are paid once.">
      <div className="panel active">
        <span className="label">Your plan</span>
        <div className="row between">
          <h2>{b.plan_name}</h2>
          {b.mode === "dev" && <span className="token grey">Test payments · no real money</span>}
        </div>
        <p className="muted">{whereFrom(b)}</p>
        <div className="statgrid">
          <Stat label="AI runs a month" value={b.ladder[rank(current)].runs} />
          <Stat label="AI level" value={<span style={{ fontSize: 16 }}>{b.ladder[rank(current)].agent_level}</span>} />
          {Object.entries(b.access || {}).filter(([, u]) => u && new Date(u) > new Date()).map(([t, u]) => (
            <Stat key={t} label={`${b.ladder[rank(t)].name} until`} value={<span style={{ fontSize: 18 }}>{day(u)}</span>} />
          ))}
        </div>
        {b.mode === "contact" && <p className="small muted">Online payment isn't switched on yet. Choose a plan and our team will call you to set it up.</p>}
      </div>

      <Membership b={b} buy={buy} reload={reload} />

      <div className="stack">
        {b.ladder.map((t, i) => (
          <div key={t.tier} id={`tier-${t.tier}`} className={`panel ${t.tier === highlight ? "active" : ""}`}>
            <div className="row between">
              <div className="stack" style={{ gap: 4 }}>
                <span className="label">{t.tier === current ? "Your plan" : `AI level · ${t.agent_level}`}</span>
                <h2>{t.name}</h2>
                <span className="small muted">{t.pitch}</span>
              </div>
              <div className="stack" style={{ justifyItems: "end", gap: 8 }}>
                <span className={t.price_minor ? "money" : ""} style={{ fontFamily: "var(--head)", fontSize: 26 }}>
                  {rupees(t.price_minor)}{t.billing === "subscription" ? <span className="small">/month</span> : null}
                </span>
                {t.billing === "one_time" && <span className="small muted">one payment · {t.access_days} days</span>}
                {action(t)}
              </div>
            </div>
            <hr className="rule" />
            <div className="row" style={{ gap: 8 }}>
              <span className="token grey">{t.runs} AI runs / month</span>
              {t.tier === current && <span className="token grey">{t.agent_level}</span>}
              {t.team_size > 0 && <span className="token grey">{t.team_size} team logins</span>}
              {t.promise && t.promise !== t.pitch && <span className="small muted">{t.promise}</span>}
            </div>
            <Unlocks t={t} prev={i > 0 ? b.ladder[i - 1] : null}
              open={t.tier === wanted || rank(t.tier) === rank(current) + 1 || (rank(current) === TIERS.length - 1 && t.tier === current)} />
          </div>
        ))}
      </div>

      <p className="small muted">Each program's call recordings, with notes, open in <Link to="/recordings">Recordings</Link> once you're on that plan.</p>

      <div className="stack">
        <span className="label">Payment history</span>
        {b.payments.length ? (
          <table className="t cards">
            <thead><tr><th>Date</th><th>Plan</th><th>Amount</th><th>Status</th></tr></thead>
            <tbody>{b.payments.map((p) => {
              const [label, tone] = STATUS[p.status] || [p.status, "grey"];
              return (
                <tr key={p.id}>
                  <td data-l="Date">{day(p.paid_at || p.created_at)}</td>
                  <td data-l="Plan">{p.tier_name}{p.kind === "subscription" ? " · monthly" : p.kind === "dev" ? " · test" : ""}</td>
                  <td data-l="Amount"><span className="money">{paise(p.amount)}</span>
                    {p.refunded_amount && p.status !== "refunded" ? <div className="small muted"><span className="money">{paise(p.refunded_amount)}</span> refunded</div> : null}</td>
                  <td data-l="Status"><span className={`token ${tone}`}>{label}</span></td>
                </tr>
              );
            })}</tbody>
          </table>
        ) : <div className="empty"><p className="muted">No payments yet.</p></div>}
        <p className="small muted">Payments are processed by Razorpay. For a receipt or a refund, reply to your payment email or message the Business AI team.</p>
      </div>
      <EmailLogin me={me} refresh={refresh} toast={toast} />
    </Sheet>
  );
}

/** Lets an owner who joined with their mobile also log in with an email and password. */
function EmailLogin({ me, refresh, toast }) {
  const has = Boolean(me.user.email);
  const [f, setF] = useState({ email: me.user.email || "", password: "", current_password: "" });
  const set = (k) => (e) => setF({ ...f, [k]: e.target.value });
  return (
    <div className="panel">
      <span className="label">Log in with email</span>
      <p className="small muted">{has ? <>You can log in with <b style={{ color: "var(--paper)" }}>{me.user.email}</b> as well as your mobile number. Change it here.</>
        : "Add an email and password, so you can log in even when a WhatsApp code can't reach you."}</p>
      <div className="grid3">
        <label className="field"><span className="label">Email</span><input className="input" type="email" autoComplete="email" value={f.email} onChange={set("email")} /></label>
        <label className="field"><span className="label">{has ? "New password" : "Password"}</span><input className="input" type="password" autoComplete="new-password" placeholder="At least 8 characters" value={f.password} onChange={set("password")} /></label>
        {has && <label className="field"><span className="label">Current password</span><input className="input" type="password" autoComplete="current-password" value={f.current_password} onChange={set("current_password")} /></label>}
      </div>
      <div><Busy className="btn" disabled={!f.email || f.password.length < 8} run={async () => {
        await api("/auth/password", { method: "PUT", body: f });
        setF({ ...f, password: "", current_password: "" });
        await refresh();
        toast("Email login saved.");
      }}>{has ? "Update email login" : "Add email login"}</Busy></div>
    </div>
  );
}

/* v2.3 (OwnerDesk): one plan, paid outside the hub on Razorpay; the team switches it on (Admin → Onboarding). */
function PlansV23() {
  const { me, setContact, refresh, toast } = useHub();
  const m = me.membership || {};
  const links = me.links || {};
  const saving = m.price_year_minor ? m.price_minor * 12 - m.price_year_minor : 0;
  const engines = (me.menu || []).filter((g) => g.engine);
  const status = me.paid
    ? { label: "Your plan", title: m.name, line: m.paid_until ? `Paid until ${day(m.paid_until)}.` : "Switched on for you by the OwnerDesk team." }
    : me.event
      ? { label: "Event access", title: "Your event access", line: `Open until ${day(me.event.until)}${me.event.day ? ` · Day ${me.event.day} of the event` : ""}. Everything you make stays in your hub.` }
      : { label: "Your plan", title: "Free", line: "Your Knowledge Hub and Engine Audit stay open. Everything you made is saved — join to open the engines again." };
  const ask = () => { api("/upgrade-click", { method: "POST", body: { tier: "lite", from_feature: "plans" } }).catch(() => {}); setContact(true); };
  return (
    <Sheet kicker="Account · Plans & billing" id="U-01" money title={<>One plan. <em>Every engine.</em></>}
      sub="OwnerDesk Membership opens all 6 engines of your business, with your AI tokens every month.">
      <div className="panel active">
        <span className="label">{status.label}</span>
        <h2 style={{ margin: 0 }}>{status.title}</h2>
        <p className="muted" style={{ margin: 0 }}>{status.line}</p>
        <div className="row" style={{ gap: 18, flexWrap: "wrap" }}>
          <Stat label={me.event ? "AI tokens for the event" : "AI tokens this month"} value={`${me.runs.left} left`} />
          <Stat label="Social images" value={`${me.images?.allowance ?? 0} ${me.event ? "for the event" : "a month"}`} />
        </div>
      </div>

      {!me.paid && (
        <div className="stack">
          <span className="label">Join {m.name || "OwnerDesk Membership"}</span>
          <div className="plan-prices">
            <div className="plan-price">
              <span className="label">Monthly</span>
              <span className="big money">{rupees(m.price_minor)}<span className="small">/month</span></span>
              {links.pay_monthly_link ? <a className="btn money" href={links.pay_monthly_link} target="_blank" rel="noopener noreferrer">Pay monthly</a>
                : <button className="btn money" onClick={ask}>Ask us to call you</button>}
            </div>
            {m.price_year_minor ? (
              <div className="plan-price best">
                <span className="label">Yearly</span>
                <span className="big money">{rupees(m.price_year_minor)}<span className="small">/year</span></span>
                {saving > 0 && <span className="small muted">{rupees(saving)} less than 12 monthly payments.</span>}
                {links.pay_yearly_link ? <a className="btn money" href={links.pay_yearly_link} target="_blank" rel="noopener noreferrer">Pay yearly</a>
                  : <button className="btn money" onClick={ask}>Ask us to call you</button>}
              </div>
            ) : null}
          </div>
          <p className="small muted">Payment is on Razorpay. After you pay, the OwnerDesk team switches your plan on — usually within a few hours. Nothing you've made is lost.</p>
        </div>
      )}

      <div className="stack">
        <span className="label">What's included</span>
        <div className="engine-cards">
          {engines.map((g) => (
            <div key={g.section} className="engine-card">
              <h3>{g.group}</h3>
              <p className="small muted">{g.items.map((i) => i.label).join(" · ")}</p>
            </div>
          ))}
          <div className="engine-card">
            <h3>Every month</h3>
            <p className="small muted">{m.runs} AI tokens · {m.images} social media images · the business and product brochures</p>
          </div>
        </div>
      </div>

      <EmailLogin me={me} refresh={refresh} toast={toast} />

      <div className="stack">
        <span className="label">Help</span>
        {links.community_link && <p><a href={links.community_link} target="_blank" rel="noopener noreferrer">Join the WhatsApp community</a></p>}
        {links.support_email ? <p>Questions about your plan or a payment: <a href={`mailto:${links.support_email}`}>{links.support_email}</a></p>
          : <p className="small muted">Questions about your plan or a payment: message the OwnerDesk team.</p>}
      </div>
    </Sheet>
  );
}

export default function Plans() {
  const { me } = useHub();
  if (me?.ui?.menu === "engines") return <Gate role="owner" kicker="Account · Plans & billing" id="U-01"><PlansV23 /></Gate>;
  return <Gate role="owner" kicker="Grow · Plans & billing" id="U-01"><PlansInner /></Gate>;
}
