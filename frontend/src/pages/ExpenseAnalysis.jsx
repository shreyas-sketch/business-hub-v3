import React, { useState } from "react";
import { api, tokenSpan } from "../api.js";
import { Busy, FormError, canAct, useHub, useLoad } from "../ui.jsx";
import { Empty, Loading, Pill } from "../v2.jsx";
import "./data.css";

/* v2.3 Data engine: the analysis (worked out by the hub, no AI) and the owner's AI money advice (totals only). */

const whole = (n) => "₹" + new Intl.NumberFormat("en-IN", { maximumFractionDigits: 0 }).format(Math.round(Number(n || 0)));
const monthName = (m) => new Date(`${m}-01T00:00:00`).toLocaleDateString("en-IN", { month: "short", year: "2-digit" });

function Months({ an }) {
  const money = an.money?.months?.length ? Object.fromEntries(an.money.months.map((x) => [x.month, x])) : null;
  const rows = an.months.map((m) => ({ ...m, in: money?.[m.month]?.in ?? null }));
  const top = Math.max(1, ...rows.map((r) => Math.max(r.out, r.in || 0)));
  return (
    <div className="panel">
      <span className="label">Month by month</span>
      <div className="da-months">
        {rows.map((r) => (
          <div key={r.month} className="da-month">
            <span>{monthName(r.month)}{r.partial ? <span className="small muted"> · so far</span> : null}</span>
            <div className="da-bars" role="img" aria-label={`${monthName(r.month)}: spent ${whole(r.out)}${r.in != null ? `, money in ${whole(r.in)}` : ""}`}>
              <div className="da-bar"><i style={{ width: `${(r.out / top) * 100}%` }} /></div>
              {money && <div className="da-bar in"><i style={{ width: `${((r.in || 0) / top) * 100}%` }} /></div>}
            </div>
            <span className="small"><span className="money">{whole(r.out)}</span>{money ? <span className="muted"> · in {whole(r.in || 0)}</span> : null}</span>
          </div>
        ))}
      </div>
      <div className="da-legend">
        <span><i />Spent (Expenses)</span>
        {money && <span><i className="in" />Money in ({an.money.source === "sales" ? "your sales in the hub" : "the bank statement"})</span>}
      </div>
      {!money && <p className="small muted">Money in shows here once the hub has your sales (invoices, or a sales sheet) or a bank statement on the Bank statement page.</p>}
    </div>
  );
}

function Categories({ an }) {
  if (!an.categories.length) return null;
  return (
    <div className="panel">
      <span className="label">Each category over time</span>
      {an.categories.map((c) => {
        const top = Math.max(1, ...c.by_month.map((x) => x.amount));
        return (
          <div key={c.key} className="da-cat">
            <div className="top">
              <span><b>{c.label}</b>{c.salary ? <span className="small muted"> · salaries</span> : null}</span>
              <span className="small"><span className="money">{whole(c.amount)}</span> · {c.share}% of spend</span>
            </div>
            <div className="da-spark" role="img" aria-label={`${c.label} by month: ${c.by_month.map((x) => `${monthName(x.month)} ${whole(x.amount)}`).join(", ")}`}>
              {c.by_month.map((x, i) => <i key={x.month} className={i === c.by_month.length - 1 ? "now" : ""} style={{ height: `${Math.max(4, (x.amount / top) * 100)}%` }} title={`${monthName(x.month)}: ${whole(x.amount)}`} />)}
            </div>
            {c.usual != null && c.last_vs_avg_pct != null && (
              <span className={`small ${c.last_vs_avg_pct >= 25 ? "" : "muted"}`}>
                {monthName(an.last_full_month)}: {whole(c.last_month)} against a usual {whole(c.usual)}
                {c.last_vs_avg_pct ? ` (${Math.abs(c.last_vs_avg_pct)}% ${c.last_vs_avg_pct > 0 ? "more" : "less"})` : ""}
              </span>
            )}
          </div>
        );
      })}
    </div>
  );
}

function Advice({ months }) {
  const { me, refresh } = useHub();
  const [state, reload] = useLoad("/expenses-ai/advice");
  const [salaries, setSalaries] = useState(false);
  const [err, setErr] = useState("");
  if (!state) return <Loading />;
  const a = state.advice;
  const ask = async () => {
    setErr("");
    await api("/expenses-ai/advice", { method: "POST", body: { months, include_salaries: salaries } });
    await reload();
    refresh();
  };
  return (
    <div className="panel active">
      <span className="label">AI advice on your money</span>
      <p className="small">The AI reads the totals on this page — spend by month and by category, the payments that repeat, and your sales — and
        says what stands out and what you could do. It never sees your bank statement's lines, and it can only quote figures from this page.</p>
      <label className="d1-consent small"><input type="checkbox" checked={salaries} onChange={(e) => setSalaries(e.target.checked)} />
        <span>Include salaries. Off unless you tick it.</span></label>
      <FormError msg={err} />
      <div className="row">
        <Busy className="btn primary ai" onError={(e) => setErr(e.message)} run={ask}>{a ? "Get new advice" : "Get AI advice"} · 1 token</Busy>
        <span className="small muted">{me.runs.left} tokens left {tokenSpan(me)}</span>
      </div>
      {a && (
        <div className="da-advice">
          <span className="small muted">From {new Date(a.at).toLocaleDateString("en-IN", { day: "numeric", month: "short" })} · last {a.months} months · {a.include_salaries ? "with salaries" : "salaries left out"}</span>
          <h3>{a.headline}</h3>
          {a.findings.length > 0 && <span className="label">What stands out</span>}
          {a.findings.map((f) => <div key={f.title} className="da-item"><b>{f.title}</b><span className="small">{f.detail}</span></div>)}
          {a.recommendations.length > 0 && <span className="label">What you could do</span>}
          {a.recommendations.map((r) => (
            <div key={r.title} className="da-item"><b>{r.title}</b><span className="small">{r.detail}</span>{r.why ? <span className="small muted">{r.why}</span> : null}</div>
          ))}
          <p className="small muted">A draft from the AI to think with — check it against your books before you act.</p>
        </div>
      )}
    </div>
  );
}

export default function ExpenseAnalysis() {
  const { me } = useHub();
  const [months, setMonths] = useState(6);
  const [an, , err] = useLoad(`/expenses-ai/analysis?months=${months}`);
  return (
    <div className="stack">
      <div className="row between">
        <span className="small muted">Worked out by the hub from your Expenses{an?.money?.source === "bank" ? " and the bank statement" : ""} — no AI.</span>
        <div className="ex-chips" role="group" aria-label="Months">
          {[3, 6, 12].map((n) => <button key={n} type="button" className="ex-chip" aria-pressed={months === n} onClick={() => setMonths(n)}>{n} months</button>)}
        </div>
      </div>
      {err && <FormError msg={err.message} />}
      {!an ? <Loading /> : !an.total ? <Empty>No expenses in these months yet. Add some, or bring them in from a bank statement.</Empty> : (
        <>
          <div className="statgrid">
            <div className="stat"><span className="label">Spent · {months} months</span><span className="num money">{whole(an.total)}</span>
              <span className="small muted">{an.count} entries{an.capped ? " (the newest only)" : ""}</span></div>
            {an.categories[0] && <div className="stat"><span className="label">Biggest head</span><span className="num ex-head">{an.categories[0].label}</span>
              <span className="small muted">{whole(an.categories[0].amount)} · {an.categories[0].share}%</span></div>}
            <div className="stat"><span className="label">Repeat every month</span><span className="num">{an.repeats.length}</span>
              <span className="small muted">{an.repeats.length ? `${whole(an.repeats.reduce((s, r) => s + r.amount, 0))} a month` : "None found yet"}</span></div>
          </div>
          <Months an={an} />
          <Categories an={an} />
          {an.top_payees.length > 0 && (
            <div className="panel">
              <span className="label">Who you paid the most</span>
              <div className="ex-kv">{an.top_payees.map((p) => (
                <div key={p.payee}><span>{p.payee}<span className="small muted"> · {p.count} {p.count === 1 ? "payment" : "payments"}</span></span><span className="money">{whole(p.amount)}</span></div>
              ))}</div>
            </div>
          )}
        </>
      )}
      {canAct(me, "owner") && an && an.total > 0 && <Advice months={months} />}
      {canAct(me, "owner") && an && !an.total && <p className="small muted"><Pill>AI advice</Pill> opens here once there are expenses to look at.</p>}
    </div>
  );
}
