import React, { useState } from "react";
import { Link } from "react-router-dom";
import { localPhone, rs } from "../api.js";
import { Gate, LoadError, Sheet, canAct, useHub, useLoad } from "../ui.jsx";
import { Loading, Pill } from "../v2.jsx";
import SalesUpload from "./SalesUpload.jsx";
import "./moneyinsights.css";

/* Getting paid (v2.2): how long customers really take to pay, and the money still owed by how late it is. Read-only. */

const HEAD = { kicker: "Structure · Getting paid", id: "ST-12", pillar: "Structure", money: true };
const fmt = (iso) => (iso ? new Date(`${iso}T00:00:00`).toLocaleDateString("en-IN", { day: "numeric", month: "short", year: "numeric" }) : "");
const rupee = (n) => rs(Math.round(n || 0));
const days = (n) => `${n} ${Math.round(n) === 1 ? "day" : "days"}`;
const BUCKETS = [["not_due", "Not due yet"], ["d1_30", "1–30 days late"], ["d31_60", "31–60 days late"], ["d61_90", "61–90 days late"], ["d90_plus", "Over 90 days late"]];
const HABIT = { "pays on time": "on", "usually a little late": "money", "often late": "warn" };

function Wait({ g }) {
  const { me } = useHub();
  return (
    <div className="panel">
      <span className="label">Your terms and what really happens · last 12 months</span>
      <p className={`mi-line ${g.terms_days != null && g.avg_days_to_pay > g.terms_days ? "warn" : ""}`}>{g.line}</p>
      <div className="statgrid">
        <div className="stat"><span className="label">Average wait</span><span className="num">{days(Math.round(g.avg_days_to_pay))}</span>
          <span className="small muted">Bigger amounts count more</span></div>
        <div className="stat"><span className="label">Middle payment</span><span className="num">{days(g.median_days_to_pay)}</span>
          <span className="small muted">Half were paid faster than this</span></div>
        <div className="stat"><span className="label">Paid by the due date</span>
          <span className="num">{g.on_time_pct == null ? "—" : `${g.on_time_pct}%`}</span>
          <span className="small muted">{g.on_time_pct == null ? "No due dates yet" : `of ${g.on_time_based_on} ${g.on_time_based_on === 1 ? "payment" : "payments"} with a due date`}</span></div>
        <div className="stat"><span className="label">Your terms</span><span className="num">{g.terms_days == null ? "—" : days(g.terms_days)}</span>
          <span className="small muted">{g.terms_days == null
            ? (canAct(me, "owner") ? <Link to="/knowledge">Add them in the Knowledge Hub</Link> : "Not added in the Knowledge Hub yet")
            : "From your Knowledge Hub"}</span></div>
      </div>
      <p className="small muted">From {g.paid_count} payments: {[
        g.sources.money_owed ? `${g.sources.money_owed} marked paid in Money owed` : "",
        g.sources.sales_sheet ? `${g.sources.sales_sheet} from sales sheets with a “Paid on” column` : "",
      ].filter(Boolean).join(" and ")}. The wait counts from the invoice date{g.sources.money_owed ? ". For amounts typed into Money owed by hand, it counts from the day they were added" : ""}.</p>
    </div>
  );
}

function NotEnough({ g }) {
  const { me } = useHub();
  return (
    <div className="panel">
      <span className="label">Not enough payments yet</span>
      <p className="mi-line">{g.paid_count ? `${g.paid_count} ${g.paid_count === 1 ? "payment" : "payments"} so far.` : "No payments to go on yet."}</p>
      <p className="small muted">We need at least 5 paid amounts from the last 12 months to say how long customers take.</p>
      <ol className="mi-ways">
        {me.features?.money_owed && <li>Mark amounts <b>paid</b> in <Link to="/money">Money owed</Link> when the money comes in.</li>}
        <li>Upload a sales sheet below with a <b>Paid on</b> column (and a Due date column if you have one).</li>
      </ol>
    </div>
  );
}

function Stale({ g }) {
  if (!g.stale_note) return null;
  return (
    <div className="panel flat">
      <span className="label">From an older sales sheet</span>
      <p className="small">{g.stale_note}</p>
    </div>
  );
}

function Ageing({ g }) {
  const { me } = useHub();
  if (!g.owed) return null;
  return (
    <div className="panel">
      <span className="label">Still owed to you, by how late it is</span>
      {g.stuck_line && <p className={`mi-line ${g.stuck > 0 ? "warn" : ""}`}>{g.stuck_line}</p>}
      <div className="mi-age">
        {BUCKETS.map(([k, label]) => {
          const a = g.ageing[k];
          return (
            <div key={k} className={!a.count ? "zero" : k === "d61_90" || k === "d90_plus" ? "late" : ""}>
              <span className="label">{label}</span>
              <b>{rupee(a.amount)}</b>
              <span className="small muted">{a.count} {a.count === 1 ? "amount" : "amounts"}</span>
            </div>
          );
        })}
      </div>
      <p className="small muted">Counted from the due date: {rupee(g.owed)} in all. Money owed is as of today ({fmt(g.as_of)}).{g.sheet_note ? ` ${g.sheet_note}` : ""}</p>
      {me.features?.money_owed && <div><Link className="btn sm" to="/money">Chase these in Money owed</Link></div>}
    </div>
  );
}

function Oldest({ g }) {
  if (!g.oldest.length) return null;
  return (
    <div className="panel">
      <span className="label">Biggest amounts more than 30 days late</span>
      <div className="mi-list">
        {g.oldest.map((o, i) => (
          <div key={i}>
            <div className="who">
              <b>{o.customer}</b>
              <span className="small muted">{o.days_late} days late{o.as_of ? ` on the day you uploaded the sheet (${fmt(o.as_of)})` : ""} · was due {fmt(o.due_date)}{o.invoice_no ? ` · ${o.invoice_no}` : ""} · {o.source === "money owed" ? "Money owed" : "sales sheet"}</span>
              {o.phone && <span className="small muted mono">{localPhone(o.phone)}</span>}
            </div>
            <span className="amt money">{rupee(o.amount)}</span>
          </div>
        ))}
      </div>
    </div>
  );
}

function Payers({ g }) {
  const [all, setAll] = useState(false);
  if (!g.customers.length) return null;
  // the habits worth acting on first: customers who paid more than once, or still owe something; the rest on request
  const main = g.customers.filter((c) => c.paid_count >= 2 || c.unpaid > 0);
  const rest = g.customers.length - main.length;
  const shown = all ? g.customers : (main.length ? main : g.customers).slice(0, 15);
  const hidden = g.customers.length - shown.length;
  return (
    <div className="panel">
      <span className="label">Slowest payers first</span>
      <div className="mi-list">
        {shown.map((c, i) => (
          <div key={`${c.name}-${i}`}>
            <div className="who">
              <b>{c.name}</b>
              <span className="small muted">Waits {days(Math.round(c.avg_days_to_pay))} on average · {c.paid_count} {c.paid_count === 1 ? "payment" : "payments"} · last paid {fmt(c.last_paid)}</span>
              <span className="small muted">{c.avg_days_late == null ? "No due dates for this customer" : c.avg_days_late === 0 ? "Pays by the due date" : `${days(Math.round(c.avg_days_late))} late on average`}</span>
              {HABIT[c.habit] && <div><Pill kind={HABIT[c.habit]}>{c.habit}</Pill></div>}
            </div>
            <span className={`amt ${c.unpaid ? "money" : "muted"}`}>{c.unpaid ? rupee(c.unpaid) : "—"}<small>{c.unpaid ? "still owed" : "nothing owed"}</small></span>
          </div>
        ))}
      </div>
      {hidden > 0 && (
        <button className="btn sm ghost" onClick={() => setAll(true)}>
          Show all {g.customers.length}{rest ? ` (${rest} paid once, nothing owed)` : ""}
        </button>
      )}
      <p className="small muted">Customers with 2 or more payments come first — one payment says little about a habit.</p>
    </div>
  );
}

function Inner() {
  const [g, reload, err] = useLoad("/getting-paid");
  if (!g) return <Sheet {...HEAD} title={<>How long money really <em>takes</em>.</>}>{err ? <LoadError err={err} /> : <Loading />}</Sheet>;
  return (
    <Sheet {...HEAD} title={<>How long money really <em>takes</em>.</>}
      sub="What you ask customers for, against how long they actually take — and what's still owed, by how late it is.">
      {g.enough_data ? <Wait g={g} /> : <NotEnough g={g} />}
      <Ageing g={g} />
      <Stale g={g} />
      <Oldest g={g} />
      <Payers g={g} />
      <SalesUpload onChange={reload}
        note="A “Paid on” column makes this page much better: it shows how long each customer really took. A “Due date” column shows who pays late." />
    </Sheet>
  );
}

export default function GettingPaid() {
  return (
    <Gate feature="getting_paid" role="manager" kicker={HEAD.kicker} id={HEAD.id}
      what="How long each customer really takes to pay, and the money stuck by how old it is.">
      <Inner />
    </Gate>
  );
}
