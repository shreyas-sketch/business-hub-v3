import React, { useState } from "react";
import { Link } from "react-router-dom";
import { api, rs } from "../api.js";
import { Busy, FormError, Gate, Sheet, canAct, useHub, useLoad } from "../ui.jsx";
import { Loading, Pill } from "../v2.jsx";
import "./gst.css";

/* GST set-aside (v2.2): GST on your sales minus GST on your bills — about what to keep aside for the next GST payment.
   An estimate from what's in the hub, never a GST return and never tax advice. Due dates come from the compliance calendar. */

const fmt = (iso, o = { day: "numeric", month: "short" }) => (iso ? new Date(`${iso}T00:00:00`).toLocaleDateString("en-IN", o) : "");
const plural = (n, one, many) => `${n} ${n === 1 ? one : many}`;
/** ₹2,947.80 — paise shown only when there are any. */
const paise = (n) => "₹" + new Intl.NumberFormat("en-IN", { minimumFractionDigits: Number(n) % 1 ? 2 : 0, maximumFractionDigits: 2 }).format(Number(n || 0));

function DueLine({ p, registered }) {
  const d = p.due;
  if (!d) {
    return registered === null ? <div className="gst-due"><Pill kind="warn">No date yet</Pill></div> : null;   // the card's notes say what to fill
  }
  if (d.status === "done") {
    return <div className="gst-due"><Pill kind="on">Done</Pill><span><b>{d.title}</b> for {d.period} is marked done.</span></div>;
  }
  const left = p.days_left;
  const pill = left < 0 ? `Overdue · ${plural(-left, "day", "days")}` : left === 0 ? "Due today" : left === 1 ? "Tomorrow" : `In ${left} days`;
  return (
    <div className="gst-due">
      <Pill kind={left <= 3 ? "warn" : ""}>{pill}</Pill>
      <span><b>{d.title}</b> · {left < 0 ? "was due" : "due"} {fmt(d.date, { weekday: "short", day: "numeric", month: "short" })}</span>
    </div>
  );
}

function CashFlowAction({ p, canSee, onChange }) {
  const { toast } = useHub();
  const [ask, setAsk] = useState(false);
  const [err, setErr] = useState("");
  const est = p.estimate_rounded;
  const put = async (replace) => {
    try {
      await api("/gst-setaside/cash-flow", { method: "PUT", body: { period: p.period, replace } });
    } catch (e) {
      if (e.status === 409) onChange();   // it changed under us (e.g. an amount typed in Compliance): show the latest
      throw e;
    }
    setAsk(false);
    toast(replace || p.needs_update ? `Cash flow now counts ${rs(est)}` : `Counted in your cash flow on ${fmt(p.due?.date)}`);
    onChange();
  };
  const remove = async () => {
    await api(`/gst-setaside/cash-flow?period=${encodeURIComponent(p.period)}`, { method: "DELETE" });
    toast("Removed from your cash flow");
    onChange();
  };
  const ours = p.cash_flow_amount !== null && p.cash_flow_amount !== undefined;
  const owners = p.owner_amount !== null && p.owner_amount !== undefined;
  let body = null;
  if (owners) {
    body = (
      <>
        <span className="small">You typed <b className="money">{paise(p.owner_amount)}</b> for this payment in your compliance calendar
          {p.in_cash_flow ? " — that's the amount your cash flow counts." : "."}</span>
        {p.can_count && !ask && Math.round(p.owner_amount) !== est && (
          <div><button className="btn sm ghost" onClick={() => setAsk(true)}>Use the estimate of {rs(est)} instead</button></div>
        )}
        {ask && (
          <div className="gst-ask" role="group" aria-label="Replace your amount?">
            <span>Replace your {paise(p.owner_amount)} with the estimate of {rs(est)}?</span>
            <div className="row">
              <Busy className="btn sm money" onError={setErr} run={() => put(true)}>Replace it</Busy>
              <button className="btn sm ghost" onClick={() => setAsk(false)}>Keep mine</button>
            </div>
          </div>
        )}
      </>
    );
  } else if (ours && p.in_cash_flow) {
    body = (
      <>
        <span className="gst-in">In your cash flow ✓ · {rs(p.cash_flow_amount)} on {fmt(p.due?.date)}</span>
        {p.needs_update && (est > 0
          ? <span className="small">The estimate is now {rs(est)}{p.complete ? "" : " and still growing"}.</span>
          : <span className="small">The estimate is now ₹0 — nothing to keep aside.</span>)}
        <div className="row">
          {p.needs_update && p.can_count && <Busy className="btn sm primary" onError={setErr} run={() => put(false)}>Update to {rs(est)}</Busy>}
          {p.can_remove && <Busy className="btn sm ghost" onError={setErr} run={remove}>Remove from cash flow</Busy>}
          {canSee && <Link className="btn sm ghost" to="/cash-flow">See cash flow</Link>}
        </div>
      </>
    );
  } else if (p.can_count) {
    body = <div><Busy className="btn primary" onError={setErr} run={() => put(false)}>Count it in my cash flow</Busy></div>;
  } else if (p.can_remove) {   // this page's amount on a payment whose date has passed
    body = <div><Busy className="btn sm ghost" onError={setErr} run={remove}>Remove {rs(p.cash_flow_amount)} from cash flow</Busy></div>;
  }
  if (!body && !p.cash_flow_note) return null;
  return (
    <div className="gst-cf">
      {body}
      {p.cash_flow_note && <span className="small muted">{p.cash_flow_note}</span>}
      {p.cash_flow_shows_from && (p.can_count || p.in_cash_flow) && (
        <span className="small muted">Your 30-day cash flow shows it from {fmt(p.cash_flow_shows_from)}.</span>
      )}
      <FormError msg={err} />
    </div>
  );
}

function Breakdown({ p }) {
  const c = p.counts || {};
  return (
    <details className="gst-break">
      <summary>How this is worked out</summary>
      <ul>
        <li><span>Invoices in the hub ({c.invoices}{c.gateway_invoices ? `, ${c.gateway_invoices} from Razorpay` : ""})</span><span>{paise(p.sales_gst)}</span></li>
        {(p.credit_notes_gst > 0 || c.credit_notes > 0) && <li><span>Credit notes ({c.credit_notes})</span><span>−{paise(p.credit_notes_gst)}</span></li>}
        <li><span>Uploaded sales with GST ({c.uploads})</span><span>{paise(p.uploaded_sales_gst)}</span></li>
        <li className="sum"><span>GST on your sales</span><span>{paise(p.net_sales_gst)}</span></li>
        <li><span>Approved GST bills in Expenses ({p.bills_count})</span><span>−{paise(p.bills_gst)}</span></li>
        <li className="sum"><span>Estimate</span><span>{paise(p.estimate)}</span></li>
        {p.carry_forward > 0 && <li><span>Bills more than sales (not taken off any other month)</span><span>{paise(p.carry_forward)}</span></li>}
      </ul>
    </details>
  );
}

function PeriodCard({ p, registered, canSee, onChange }) {
  return (
    <section className={`gst-card ${p.complete ? "" : "running"}`} aria-label={`${p.period_label} GST estimate`}>
      <div className="gst-head">
        <h3>{p.period_label}</h3>
        <Pill>{p.complete ? "Last month" : "This month so far"}</Pill>
      </div>
      <div className="gst-eq">
        <div className="gst-row"><span>GST on your sales</span><span className="gst-amt">{paise(p.net_sales_gst)}</span></div>
        <div className="gst-row"><span>− GST on your bills</span><span className="gst-amt">{paise(p.bills_gst)}</span></div>
        <div className="gst-row total">
          <span>= about this much to keep aside <Pill kind="money">Estimate</Pill></span>
          <b className="money">{rs(p.estimate_rounded)}</b>
        </div>
      </div>
      <Breakdown p={p} />
      <DueLine p={p} registered={registered} />
      <CashFlowAction p={p} canSee={canSee} onChange={onChange} />
      {p.notes?.length > 0 && <ul className="gst-notes">{p.notes.map((n, i) => <li key={i}>{n}</li>)}</ul>}
    </section>
  );
}

function Counted({ s, f }) {
  return (
    <div className="panel">
      <span className="label">What's counted</span>
      <ul className="gst-counted">
        <li>
          <b>Invoices made in the hub</b>, and Razorpay payments the hub turned into invoices.
          <span className="small muted"> {plural(s.invoices, "invoice", "invoices")} in these two months{s.gateway_invoices ? `, ${s.gateway_invoices} from Razorpay` : ""}.</span>
          {f.quotations && <> <Link to="/quotes">Invoices</Link></>}
        </li>
        <li>
          <b>Credit notes</b> — a refund takes its GST off.
          <span className="small muted"> {plural(s.credit_notes, "credit note", "credit notes")}.</span>
        </li>
        <li>
          <b>Sales you uploaded with a GST column</b>. A sale already in the hub (same invoice number and year, and the same customer or amount) is counted once.
          <span className="small muted"> {plural(s.uploads, "sale", "sales")} with GST.</span>
          {f.customer_risk && <> <Link to="/customer-risk">Customer risk</Link></>}
          {f.getting_paid && <> · <Link to="/getting-paid">Getting paid</Link></>}
        </li>
        <li>
          <b>Bills marked as GST bills in Expenses</b>, once approved — the GST you can claim back.
          <span className="small muted"> {plural(s.bills, "bill", "bills")}.</span>
          {f.expenses && <> <Link to="/expenses">Expenses</Link></>}
        </li>
      </ul>
      <p className="small muted">Not counted: sales and bills that aren't in the hub, reverse charge, interest and late fees, and credit carried forward from earlier months.</p>
    </div>
  );
}

function GstInner() {
  const { me } = useHub();
  const [d, reload, err] = useLoad("/gst-setaside");
  const head = { kicker: "Structure · GST set-aside", id: "ST-15", pillar: "Structure", money: true };
  const title = <>Keep this much <em>aside</em> for GST.</>;
  if (!d) return <Sheet {...head} title={title}>{err ? <FormError msg={err.message} /> : <Loading />}</Sheet>;
  const f = me?.features || {};
  const owner = canAct(me, "owner");
  const toKnowledge = owner
    ? <div><Link className="btn primary" to="/knowledge">Open the Knowledge Hub</Link></div>
    : <span className="small muted">Ask the business owner to fill it in the Knowledge Hub.</span>;

  if (d.registered === false) {
    return (
      <Sheet {...head} title={<>No GST to keep <em>aside</em>.</>} sub={d.label}>
        <div className="panel active">
          <p>{d.message}</p>
          {d.notes.map((n, i) => <p key={i} className="small">{n}</p>)}
          {owner ? <div><Link className="btn" to="/knowledge">Check it in the Knowledge Hub</Link></div>
            : <span className="small muted">If that's wrong, ask the business owner to change it in the Knowledge Hub.</span>}
        </div>
      </Sheet>
    );
  }

  const general = d.notes || [];
  return (
    <Sheet {...head} title={title} sub={d.label}>
      {d.registered === null && (
        <div className="panel active">
          <span className="label">One step first</span>
          <p>{d.message}</p>
          {toKnowledge}
        </div>
      )}

      <div className="gst-cards">
        {d.periods.map((p) => <PeriodCard key={p.period} p={p} registered={d.registered} canSee={!!f.cash_flow} onChange={reload} />)}
      </div>

      {general.length > 0 && (
        <div className="panel flat">
          <span className="label">Worth knowing</span>
          <ul className="gst-notes">{general.map((n, i) => <li key={i}>{n}</li>)}</ul>
        </div>
      )}

      <Counted s={d.sources} f={f} />

      <p className="small muted">
        Not tax advice. Your CA works out the real amount when the return is filed — confirm with them before you pay.
        {f.compliance_calendar ? <> Due dates come from your <Link to="/compliance">compliance calendar</Link>.</> : " Due dates come from the compliance rules table."}
      </p>
    </Sheet>
  );
}

export default function Gst() {
  return (
    <Gate feature="gst_setaside" role="manager" kicker="Structure · GST set-aside" id="ST-15"
      what="GST on your sales minus GST on your bills: about what to keep aside for the next GST payment, with its due date.">
      <GstInner />
    </Gate>
  );
}
