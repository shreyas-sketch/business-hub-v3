import React, { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api, day, rs } from "../api.js";
import { Busy, Field, FormError, Sheet, Stat, useHub, useLoad } from "../ui.jsx";
import { Loading, Num, Pill } from "../v2.jsx";
import "./member.css";

const fmt = (x) => new Intl.NumberFormat("en-IN", { maximumFractionDigits: 1 }).format(x || 0);

/** Done so far against the month's target, with a white line where an even pace would be today. */
function Pace({ label, got, of, pct, expected, money }) {
  const show = (v) => (money ? <span className="money">{rs(v)}</span> : fmt(v));
  return (
    <div className="stack" style={{ gap: 8 }}>
      <div className="row between">
        <span className="label">{label}</span>
        <Pill kind={pct >= expected ? "on" : ""}>{pct >= expected ? "On pace" : "Behind pace"}</Pill>
      </div>
      <div className="row" style={{ alignItems: "baseline", gap: 8 }}>
        <span className="num" style={{ fontSize: 28 }}>{show(got)}</span>
        <span className="small muted">of {show(of)} · {pct}%</span>
      </div>
      <div className="m-pace" role="img" aria-label={`${label}: ${pct}% of the month's target; an even pace would be ${expected}% by today`}>
        <i style={{ width: `${Math.min(pct, 100)}%` }} /><b style={{ left: `${Math.min(expected, 100)}%` }} />
      </div>
    </div>
  );
}

const STATUS_KIND = { on_track: "on", slightly_behind: "warn", well_behind: "warn" };
const SOURCE = { invoices: "Counted from your invoices this month (a won lead with its own invoice counts once).",
  won_leads: "Counted from leads marked Won this month, with their value. Once you raise invoices, they're counted instead.",
  payments: "Counted from money received this month: payments marked paid." };

/** "Am I on target this month?" — month to date against the target, the month-end it's heading for, and the pace needed. */
function OnTarget({ t, mode, onMode }) {
  const [err, setErr] = useState("");
  return (
    <div className="panel active">
      <div className="row between">
        <span className="label">Am I on target this month?</span>
        <Pill kind={STATUS_KIND[t.status]}>{t.status_label}</Pill>
      </div>
      <p className="m-today" style={{ fontSize: 20 }}>{t.line}</p>
      <div className="stack" style={{ gap: 8 }}>
        <div className="row" style={{ alignItems: "baseline", gap: 8 }}>
          <span className="num" style={{ fontSize: 28 }}><span className="money">{rs(Math.round(t.month_to_date))}</span></span>
          <span className="small muted">of <span className="money">{rs(Math.round(t.target))}</span> · {t.pct_of_target}%</span>
        </div>
        <div className="m-pace" role="img" aria-label={`${t.pct_of_target}% of the target so far; ${t.month_gone_pct}% of the month has gone`}>
          <i style={{ width: `${Math.min(t.pct_of_target, 100)}%` }} /><b style={{ left: `${Math.min(t.month_gone_pct, 100)}%` }} />
        </div>
        <span className="small muted m-key"><i /> {t.month_gone_pct}% of the month gone · {t.days_left} {t.days_left === 1 ? "day" : "days"} left</span>
      </div>
      <div className="statgrid">
        <Stat label="Heading for" value={rs(Math.round(t.projected))} money note="estimated: this month's pace to month-end" />
        <Stat label={t.gap > 0 ? "Short by" : "Ahead by"} value={rs(Math.round(Math.abs(t.gap)))} money note="against the target, at this pace" />
        <Stat label="Needed each day" value={rs(Math.round(t.required_daily))} money note={t.days_left > 0 ? `for the ${t.days_left} days left` : "today"} />
      </div>
      {t.activity?.line && <p className="small">{t.activity.line}</p>}
      {t.actions?.length > 0 && (
        <div className="stack" style={{ gap: 6 }}>
          <span className="label">{t.status === "on_track" ? "Keep it going" : "What closes the gap"}</span>
          <ul className="read-list">{t.actions.map((a) => <li key={a.text}>{a.path ? <Link to={a.path}>{a.text}</Link> : a.text}</li>)}</ul>
        </div>
      )}
      <div className="stack" style={{ gap: 8 }}>
        <span className="label">Count a sale</span>
        <div className="row" role="radiogroup" aria-label="Count a sale">
          {[["invoiced", "When it's invoiced"], ["paid", "When the money arrives"]].map(([k, l]) => (
            <Busy key={k} className={`btn sm ${mode === k ? "primary" : "ghost"}`} aria-pressed={mode === k} onError={setErr}
              run={async () => { if (mode !== k) await onMode(k); }}>{l}</Busy>
          ))}
        </div>
        <FormError msg={err} />
        <span className="small muted">{SOURCE[t.source] || ""}</span>
      </div>
    </div>
  );
}

function Body({ initial }) {
  const { toast } = useHub();
  const [data, setData] = useState(initial);
  const i0 = initial.inputs || {};
  const fromBrain = !initial.inputs && Number(initial.brain_target) > 0;
  const [f, setF] = useState({ monthly_target: i0.monthly_target ?? (fromBrain ? initial.brain_target : ""), avg_sale: i0.avg_sale ?? "", close_rate: i0.close_rate ?? "", meeting_rate: i0.meeting_rate || "" });
  const [target, setTarget] = useState(initial.target ?? null);
  const [mode, setMode] = useState(initial.count_when || initial.target?.mode || "invoiced");
  // Before the Magic Number is worked out, the Knowledge Hub's target is enough to say how the month is going.
  useEffect(() => {
    if (initial.target === undefined && fromBrain) api("/target").then((r) => { setTarget(r.target); if (r.target?.mode) setMode(r.target.mode); }).catch(() => {});
  }, [initial, fromBrain]);
  const changeMode = async (k) => {
    const r = await api("/target/count", { method: "PUT", body: { count_when: k } });
    setTarget(r.target);
    setMode(k);
    toast(k === "paid" ? "Sales now count when the money arrives" : "Sales now count when they're invoiced");
  };
  const [err, setErr] = useState("");
  const set = (k) => (v) => setF({ ...f, [k]: v });
  const ok = (v, max) => Number(v) > 0 && Number(v) <= max;
  const ready = ok(f.monthly_target, 1e11) && ok(f.avg_sale, 1e10) && ok(f.close_rate, 100) && (f.meeting_rate === "" || (Number(f.meeting_rate) >= 0 && Number(f.meeting_rate) <= 100));
  const save = async () => {
    const body = { monthly_target: Number(f.monthly_target), avg_sale: Number(f.avg_sale), close_rate: Number(f.close_rate), meeting_rate: Number(f.meeting_rate) || 0 };
    const d = await api("/magic", { method: "PUT", body });
    setData(d);
    if (d.target !== undefined) setTarget(d.target);
    toast("Your numbers are worked out");
  };
  const r = data.result && Object.keys(data.result).length ? data.result : null;
  const inp = data.inputs;
  const p = data.progress;
  // one sales number on the page: when "Am I on target" counts from invoices or payments, the pace below uses the same count
  const sales = target ? target.month_to_date : p?.sales;
  const salesPct = inp && p ? Math.round((sales / inp.monthly_target) * 100) : 0;

  return (
    <>
      {target && <OnTarget t={target} mode={mode} onMode={changeMode} />}
      <div className="panel">
        <span className="label">Your numbers</span>
        {fromBrain && !r && <span className="small muted">Your monthly target is filled in from your <Link to="/knowledge">Knowledge Hub</Link>. Saving here updates it there too.</span>}
        <div className="grid2">
          <Field label="Monthly sales target (₹)" hint={Number(f.monthly_target) > 0 ? <>The money you want each month: <span className="money">{rs(f.monthly_target)}</span></> : "The money you want coming in each month"}>
            <Num min="0" placeholder="e.g. 1000000" value={f.monthly_target} onChange={set("monthly_target")} />
          </Field>
          <Field label="Average sale (₹)" hint={Number(f.avg_sale) > 0 ? <>One customer spends about <span className="money">{rs(f.avg_sale)}</span></> : "What one customer spends with you, on average"}>
            <Num min="0" placeholder="e.g. 100000" value={f.avg_sale} onChange={set("avg_sale")} />
          </Field>
          <Field label="Close rate (%)" hint="Out of 10 inquiries, how many buy? 2 of 10 is 20%.">
            <Num min="0" max="100" placeholder="e.g. 20" value={f.close_rate} onChange={set("close_rate")} />
          </Field>
          <Field label="Meeting rate (%) — optional" hint="Out of 10 inquiries, how many become a meeting or site visit?">
            <Num min="0" max="100" placeholder="e.g. 50" value={f.meeting_rate} onChange={set("meeting_rate")} />
          </Field>
        </div>
        <FormError msg={err} />
        <div className="row">
          <Busy className="btn primary" disabled={!ready} run={save} onError={setErr}>{r ? "Work it out again" : "Work out my numbers"}</Busy>
          {data.updated_at && <span className="small muted">Last worked out {day(data.updated_at)}</span>}
        </div>
      </div>

      {r && inp && (
        <>
          <div className="stack">
            <h2>What it takes, worked back</h2>
            <div className="m-tiles">
              <div><span className="m-step">01 · Target</span><span className="num"><span className="money">{rs(inp.monthly_target)}</span></span><span className="small muted">in sales a month</span></div>
              <div><span className="m-step">02 · Customers</span><span className="num">{fmt(r.customers_month)}</span><span className="small muted">a month, at <span className="money">{rs(inp.avg_sale)}</span> each</span></div>
              <div><span className="m-step">03 · Inquiries</span><span className="num">{fmt(r.inquiries_month)}</span><span className="small muted">a month, if {fmt(inp.close_rate)}% buy</span></div>
              <div><span className="m-step">04 · Each week</span><span className="num aqua">{fmt(r.inquiries_week)}</span><span className="small muted">inquiries a week</span></div>
              <div><span className="m-step">05 · Each day</span><span className="num aqua">{fmt(r.inquiries_day)}</span><span className="small muted">inquiries a day, over 26 working days</span></div>
            </div>
          </div>
          <div className="statgrid">
            {r.meetings_month != null && <Stat label="Meetings a month" value={fmt(r.meetings_month)} note={`${fmt(inp.meeting_rate)}% of inquiries`} />}
            <Stat label="Follow-ups a day" value={fmt(r.followups_day)} note="3 for every new inquiry" />
            <Stat label="Posts a week" value={fmt(r.posts_week)} note="to keep inquiries coming" />
          </div>
        </>
      )}

      {r && p && (
        <div className="panel">
          <div className="row between">
            <span className="label">This month so far</span>
            <span className="small muted">{p.days_left} {p.days_left === 1 ? "day" : "days"} left · {p.expected_pct}% of the month gone</span>
          </div>
          <div className="grid3">
            <Pace label="Inquiries" got={p.inquiries} of={r.inquiries_month} pct={p.inquiries_pct} expected={p.expected_pct} />
            <Pace label="Customers" got={p.customers} of={r.customers_month} pct={p.customers_pct} expected={p.expected_pct} />
            <Pace label="Sales" got={sales} of={inp.monthly_target} pct={salesPct} expected={p.expected_pct} money />
          </div>
          <span className="small muted m-key"><i /> Where you'd be today at an even pace.</span>
          <span className="small muted">
            Counted for you from <Link to="/leads">Leads</Link>: new inquiries this month and customers won this month.{" "}
            {target ? (SOURCE[target.source] || "") : "Sales: leads marked Won this month, with their value."}
          </span>
        </div>
      )}
    </>
  );
}

export default function Magic() {
  const [d, , err] = useLoad("/magic");
  return (
    <Sheet kicker="Structure · Magic Number" id="MB-06" title={<>Your <em>Magic</em> Number.</>}
      sub="Work back from the money you want to the inquiries you need each day.">
      {!d ? (err ? <FormError msg={err.message} /> : <Loading />) : <Body initial={d} />}
    </Sheet>
  );
}
