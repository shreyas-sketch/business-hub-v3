import React from "react";
import { Link } from "react-router-dom";
import { localPhone, rs } from "../api.js";
import { Gate, LoadError, Sheet, canAct, useHub, useLoad } from "../ui.jsx";
import { Loading, Pill } from "../v2.jsx";
import SalesUpload from "./SalesUpload.jsx";
import "./moneyinsights.css";

/* Customer risk (v2.2): who the sales depend on over the last 12 months, and regular customers who have gone quiet.
   Read-only. The WhatsApp check-in opens the owner's own WhatsApp; the hub sends nothing. */

const HEAD = { kicker: "Structure · Customer risk", id: "ST-11", pillar: "Structure", money: true };
const fmt = (iso) => (iso ? new Date(`${iso}T00:00:00`).toLocaleDateString("en-IN", { day: "numeric", month: "short", year: "numeric" }) : "");
const LEVEL = { high: ["Depends a lot on a few", "warn"], medium: ["Worth watching", "money"], low: ["Spread out", "on"] };
const rupee = (n) => rs(Math.round(n || 0));
// a crore or more in a stat tile: "₹1.67 Cr" (the full figure is in the tooltip), so it never breaks across lines
const big = (n) => (Math.abs(n || 0) >= 1e7 ? `₹${(n / 1e7).toFixed(2)} Cr` : rupee(n));

function NotEnough({ r }) {
  const { me } = useHub();
  const owner = canAct(me, "owner");
  return (
    <div className="panel">
      <span className="label">Not enough sales here yet</span>
      <p className="mi-line">{r.sales_counted
        ? `We have ${r.sales_counted} ${r.sales_counted === 1 ? "sale" : "sales"} from ${r.customers_counted} ${r.customers_counted === 1 ? "customer" : "customers"}.`
        : "No sales in the hub yet."}</p>
      <p className="small muted">To say anything useful we need at least 10 sales and 3 customers. Cash and walk-in sales count in your total but not as customers.</p>
      <span className="label">Bring your sales in — any of these</span>
      <ol className="mi-ways">
        <li><b>Upload a sales sheet</b> from Tally or Excel — just below. This is the quickest way to bring in a year or two of sales.</li>
        <li><b>Connect your Razorpay</b>: every payment becomes an invoice here on its own.{" "}
          {me.features?.connections && owner ? <Link to="/connections">Open Connections</Link> : <span className="muted">Ask the owner to connect it in Connections.</span>}</li>
        <li><b>Make your invoices in the hub</b> from now on.{" "}
          {me.features?.quotations ? <Link to="/quotes">Open Quotations &amp; invoices</Link> : <span className="muted">Invoices come with Running the Business.</span>}</li>
      </ol>
    </div>
  );
}

function Depend({ r }) {
  const [label, kind] = LEVEL[r.concentration] || ["", ""];
  const k = r.how_many_make_80;
  return (
    <div className="panel">
      <div className="mi-head">
        <span className="label">Who your sales depend on · last 12 months</span>
        {label && <Pill kind={kind}>{label}</Pill>}
      </div>
      <p className={`mi-line ${r.concentration === "high" ? "warn" : ""}`}>{r.line}</p>
      <p className="small">
        {k ? <><b>{k} {k === 1 ? "customer makes" : "customers make"} 80%</b> of your sales.</>
          : "No small group of customers makes 80% of your sales."}
        {r.walk_in_share_pct ? ` Cash and walk-in sales are ${r.walk_in_share_pct}% — counted in the total, not as one customer.` : ""}
      </p>
      <div className="statgrid">
        <div className="stat"><span className="label">Sales · 12 months</span><span className="num" title={rupee(r.total_12m)}>{big(r.total_12m)}</span>
          <span className="small muted">{r.customers_12m} {r.customers_12m === 1 ? "customer" : "customers"}{r.includes_estimates ? " · includes estimates" : ""}</span></div>
        <div className="stat"><span className="label">Biggest customer</span><span className={`num ${r.biggest_share_pct >= 25 ? "money" : ""}`}>{r.biggest_share_pct}%</span>
          <span className="small muted">of your sales</span></div>
        <div className="stat"><span className="label">Top 3 customers</span><span className={`num ${r.top3_share_pct >= 50 ? "money" : ""}`}>{r.top3_share_pct}%</span>
          <span className="small muted">of your sales</span></div>
      </div>
      <div className="mi-bars" aria-label="Your biggest customers, each as a share of all your sales">
        {r.top.map((t, i) => (
          <div key={`${t.name}-${i}`} className={`mi-bar ${t.share_pct >= 25 ? "big" : ""} ${t.estimate ? "est" : ""}`}>
            <div className="top">
              <b>{t.name}{t.estimate ? <span className="small muted"> · estimate, from your customer list</span> : null}</b>
              <span>{rupee(t.amount)} · {t.share_pct}%</span>
            </div>
            <div className="track" role="presentation"><div className="fill" style={{ width: `${Math.min(100, Math.max(0, t.share_pct))}%` }} /></div>
          </div>
        ))}
      </div>
      <p className="small muted">Each bar is that customer's share of all your sales in the last 12 months. Credit notes and returns are taken off each customer's own sales.
        {r.returns_bigger_than_sales ? ` ${r.returns_bigger_than_sales} ${r.returns_bigger_than_sales === 1 ? "customer" : "customers"} returned more than they bought in these 12 months — counted as zero, not as a minus.` : ""}
        {r.includes_estimates ? " Customers who are only on your Customers list are counted from the totals there — an estimate, since that list doesn't say when each purchase was." : ""}</p>
    </div>
  );
}

function Quiet({ r }) {
  return (
    <div className="panel">
      <span className="label">Gone quiet</span>
      <p className={`mi-line ${r.quiet.length ? "warn" : ""}`}>{r.quiet_line}</p>
      <p className="small muted">A regular customer (3 or more purchases) who hasn't bought for more than twice their usual gap, and for over 45 days. The yearly value is an estimate from what they bought in the last 2 years.</p>
      {r.quiet.length > 0 && (
        <div className="mi-list">
          {r.quiet.map((q, i) => (
            <div key={`${q.name}-${i}`}>
              <div className="who">
                <b>{q.name}</b>
                {q.phone && <span className="small muted mono">{localPhone(q.phone)}</span>}
                <span className="small muted">Last bought {fmt(q.last_purchase)} — {q.days_since} days ago. Usually every {q.usual_gap_days} days · {q.purchases} purchases{q.from_list ? " · from your customer list" : ""}</span>
              </div>
              <span className="amt money">{rupee(q.yearly_value)}<small>a year (estimate)</small></span>
              <div className="acts">
                <a className="btn sm" href={q.whatsapp_link} target="_blank" rel="noreferrer">Check in on WhatsApp</a>
                {!q.phone && <span className="small muted">No number saved — WhatsApp will ask whom to send it to.</span>}
              </div>
            </div>
          ))}
        </div>
      )}
      {r.quiet_count > r.quiet.length && <p className="small muted">Showing the {r.quiet.length} biggest of {r.quiet_count}.</p>}
      {r.quiet.length > 0 && <p className="small muted">The button opens WhatsApp with a short hello in your business's name. You read it and send it yourself — nothing goes out from the hub.</p>}
    </div>
  );
}

function Sources({ r }) {
  const s = r.sources;
  const rows = [["Invoices made in the hub", s.invoices], ["Razorpay payments", s.razorpay], ["Uploaded sales sheets", s.upload]];
  return (
    <div className="panel flat">
      <span className="label">Where these numbers come from · last 2 years</span>
      <ul className="read-list small">
        {rows.map(([l, n]) => <li key={l}>{l}: {n.toLocaleString("en-IN")} {n === 1 ? "entry" : "entries"}</li>)}
        <li>Customers list totals: {s.customers_list.toLocaleString("en-IN")} {s.customers_list === 1 ? "customer" : "customers"} with no sales above (an estimate)</li>
      </ul>
      <p className="small muted">As of {fmt(r.as_of)}. A customer is matched by phone number, or by name when there's no number.</p>
    </div>
  );
}

function Inner() {
  const [r, reload, err] = useLoad("/customer-risk");
  if (!r) return <Sheet {...HEAD} title={<>Who your sales <em>depend</em> on.</>}>{err ? <LoadError err={err} /> : <Loading />}</Sheet>;
  return (
    <Sheet {...HEAD} title={<>Who your sales <em>depend</em> on.</>}
      sub="If one or two customers make most of your sales, losing one hurts. And regular customers who stop buying are easy to miss. Both, from your own sales.">
      {r.enough_data ? <><Depend r={r} /><Quiet r={r} /></> : <NotEnough r={r} />}
      {Object.values(r.sources).some(Boolean) && <Sources r={r} />}
      <SalesUpload onChange={reload} />
    </Sheet>
  );
}

export default function CustomerRisk() {
  return (
    <Gate feature="customer_risk" role="manager" kicker={HEAD.kicker} id={HEAD.id}
      what="Who your sales depend on, and regular customers who have gone quiet — with the money at risk.">
      <Inner />
    </Gate>
  );
}
