import React, { useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { ago, api, upload } from "../api.js";
import { Busy, Field, FormError, Sheet, useHub } from "../ui.jsx";
import { Empty, Loading, Pill, Tabs } from "../v2.jsx";
import "./expenses.css";

/* Bank statement reader (v2.2): upload a statement → money in and out, where it went, repeating payments → Expenses in one tap.
   The file is never kept; the masked list is kept 24 hours for review. Owner only. */

const money = (n) => "₹" + new Intl.NumberFormat("en-IN", { minimumFractionDigits: Number(n) % 1 ? 2 : 0, maximumFractionDigits: 2 }).format(Number(n || 0));
const whole = (n) => "₹" + new Intl.NumberFormat("en-IN", { maximumFractionDigits: 0 }).format(Math.round(Number(n || 0)));
const dayName = (iso) => (iso ? new Date(`${iso}T00:00:00`).toLocaleDateString("en-IN", { day: "numeric", month: "short", year: "numeric" }) : "");
const ord = (n) => { const x = ["th", "st", "nd", "rd"], v = n % 100; return `${n}${x[(v - 20) % 10] || x[v] || x[0]}`; };
const monthName = (m) => new Date(`${m}-01T00:00:00`).toLocaleDateString("en-IN", { month: "short", year: "numeric" });
const istTime = (iso) => (iso ? new Date(iso).toLocaleString("en-IN", { timeZone: "Asia/Kolkata", day: "numeric", month: "short", hour: "numeric", minute: "2-digit" }) : "");

function UploadBox({ onRead, small }) {
  const [file, setFile] = useState(null);
  const [password, setPassword] = useState("");
  const [needsPw, setNeedsPw] = useState(false);
  const [err, setErr] = useState("");
  const read = async () => {
    setErr("");
    const form = new FormData();
    form.append("file", file);
    if (needsPw) form.append("password", password);
    try {
      const s = await upload("/bank-statement", form);
      setPassword(""); setNeedsPw(false); setFile(null);
      onRead(s);
    } catch (e) {
      if (e.code === "password_needed" || e.code === "wrong_password") setNeedsPw(true);
      setPassword("");
      throw e;
    }
  };
  return (
    <div className={`panel ${small ? "" : "active"}`}>
      <span className="label">{small ? "Read another statement" : "Upload your bank statement"}</span>
      {!small && <p className="small">Download it from net banking — <b>Excel or CSV works best</b>; a PDF works too. Up to 10 MB; a few months at a time.</p>}
      <Field label="Statement file">
        <input className="input" type="file" accept=".pdf,.csv,.xls,.xlsx,.htm,.html,.xml,.txt" onChange={(e) => { setFile(e.target.files?.[0] || null); setErr(""); }} />
      </Field>
      {needsPw && (
        <Field label="The PDF's password" hint="Banks often use your customer ID or your date of birth. We use it once to open the file and don't keep it.">
          <input className="input" type="password" autoComplete="off" maxLength={64} value={password} onChange={(e) => setPassword(e.target.value)} />
        </Field>
      )}
      <FormError msg={err} />
      <div className="row">
        <Busy className="btn primary" disabled={!file || (needsPw && !password)} onError={(e) => setErr(e.message)} run={read}>Read my statement</Busy>
      </div>
      <p className="small muted">We read it here and don't keep the file. Long numbers, e-mail and UPI IDs are hidden. The list of payments stays for 24 hours so you can check it, then it's deleted — or delete it yourself any time. No AI reads it.</p>
    </div>
  );
}

function Picture({ s }) {
  const max = Math.max(...s.by_category.map((c) => c.amount), 1);
  return (
    <div className="stack">
      <div className="panel">
        <span className="label">Month by month</span>
        <div className="ex-kv">
          {s.months.map((m) => (
            <div key={m.month}><span>{monthName(m.month)}</span>
              <span className="small"><span className="money">in {whole(m.in)}</span> · <span>out {whole(m.out)}</span></span></div>
          ))}
        </div>
      </div>
      <div className="panel">
        <span className="label">Where it went</span>
        <div className="ex-cats">
          {s.by_category.map((c) => (
            <div key={c.key} className="ex-cat">
              <div className="top"><span>{c.label}</span><span className="money">{whole(c.amount)}</span></div>
              <div className="ex-track" role="img" aria-label={`${c.label}: ${whole(c.amount)}`}><i style={{ width: `${(c.amount / max) * 100}%` }} /></div>
            </div>
          ))}
        </div>
        <p className="small muted">Cash withdrawn ({whole(s.cash_withdrawn)}) and money moved to your own accounts or investments ({whole(s.transfers_out)}) aren't counted as spending.</p>
      </div>
      {s.top_payees.length > 0 && (
        <div className="panel">
          <span className="label">Who you paid the most</span>
          <div className="ex-kv">
            {s.top_payees.map((p) => <div key={p.payee}><span>{p.payee} <span className="small muted">· {p.count} {p.count === 1 ? "payment" : "payments"}</span></span><span className="money">{whole(p.amount)}</span></div>)}
          </div>
        </div>
      )}
    </div>
  );
}

function Repeating({ s, onChange }) {
  const { me } = useHub();
  if (!s.recurring.length) return <Empty>No payment repeats month after month in this statement. Upload two or three months to find them.</Empty>;
  return (
    <div className="panel">
      <span className="label">Payments that repeat every month</span>
      <div className="ex-kv">
        {s.recurring.map((r) => (
          <div key={r.key}>
            <span><b>{r.payee || r.label}</b><span className="small muted"> · {r.label} · around the {ord(r.day_of_month)} · {r.months} months</span></span>
            <span className="row" style={{ gap: 8 }}>
              <span className="money">{money(r.amount)}</span>
              {me?.features?.cash_flow && (r.in_cash_flow ? <Pill kind="on">In cash flow</Pill>
                : <Busy className="btn sm" run={async () => onChange(await api(`/bank-statement/${s.id}/recurring-to-cash-flow`, { method: "POST", body: { key: r.key } }))}>Add to cash flow</Busy>)}
            </span>
          </div>
        ))}
      </div>
      <p className="small muted">Added to Cash flow, a payment is counted every month on that day — a forecast, not a promise. Check it isn't already in your Knowledge Hub's fixed costs.</p>
    </div>
  );
}

function Payments({ s, onChange }) {
  const [show, setShow] = useState("waiting");
  const [picked, setPicked] = useState(() => new Set());
  const [remember, setRemember] = useState(true);
  const [salaries, setSalaries] = useState(false);
  const [err, setErr] = useState("");
  const [done, setDone] = useState(null);
  const rows = useMemo(() => s.transactions.filter((t) => (show === "in" ? t.kind === "credit" : t.kind === "debit" && (show === "all" || (t.expense && !t.in_expenses)))), [s, show]);
  const open = s.transactions.filter((t) => t.expense && !t.in_expenses);
  const allCount = s.waiting + (salaries ? s.waiting_salaries : 0);
  const toggle = (ref) => setPicked((p) => { const n = new Set(p); n.has(ref) ? n.delete(ref) : n.add(ref); return n; });
  const setCat = async (t, category) => {
    setErr("");
    try { onChange(await api(`/bank-statement/${s.id}/transactions/${t.ref}`, { method: "PATCH", body: { category, remember } })); }
    catch (e) { setErr(e.message); }
  };
  const add = async (body) => {
    const r = await api(`/bank-statement/${s.id}/to-expenses`, { method: "POST", body: { ...body, include_salaries: salaries } });
    setPicked(new Set());
    setDone({ added: r.added, skipped: Object.entries(r.reasons || {}).map(([k, why]) => ({ k, n: r[k], why })) });
    onChange(r.statement);
  };
  return (
    <div className="panel">
      <div className="row between" style={{ flexWrap: "wrap", gap: 10 }}>
        <span className="label">The payments</span>
        <div className="ex-chips">
          {[["waiting", `Not in Expenses (${open.length})`], ["all", "All money out"], ["in", "Money in"]].map(([k, l]) => (
            <button key={k} type="button" className="ex-chip" aria-pressed={show === k} onClick={() => setShow(k)}>{l}</button>
          ))}
        </div>
      </div>
      {show !== "in" && (
        <label className="d1-consent small"><input type="checkbox" checked={remember} onChange={(e) => setRemember(e.target.checked)} />
          <span>When I change a category, use it for this payee every time.</span></label>
      )}
      <FormError msg={err} />
      {done && (
        <div className="small" role="status">
          <p>{done.added} {done.added === 1 ? "payment" : "payments"} added to Expenses. <Link to="/expenses">Open Expenses</Link></p>
          {done.skipped.map((x) => <p key={x.k} className="muted">{x.n} left out — {x.why}</p>)}
        </div>
      )}
      {show === "waiting" && s.waiting_salaries > 0 && (
        <label className="d1-consent small"><input type="checkbox" checked={salaries} onChange={(e) => setSalaries(e.target.checked)} />
          <span>Also add the {s.waiting_salaries} salary {s.waiting_salaries === 1 ? "payment" : "payments"} ({whole(s.waiting_salaries_amount)}). Your managers can see Expenses.</span></label>
      )}
      {show === "waiting" && s.to_check > 0 && (
        <p className="small"><Pill kind="warn">Check</Pill> {s.to_check} {s.to_check === 1 ? "row doesn't" : "rows don't"} say whether money went out or came in, so we guessed. "Add all" leaves {s.to_check === 1 ? "it" : "them"} out — tick {s.to_check === 1 ? "it" : "them"} yourself if {s.to_check === 1 ? "it's" : "they're"} really a payment.</p>
      )}
      {open.length > 0 && show === "waiting" && (
        <div className="row">
          {allCount > 0 && <Busy className="btn primary" onError={(e) => setErr(e.message)} run={() => add({ all_waiting: true })}>Add all {allCount} to Expenses</Busy>}
          {picked.size > 0 && <Busy className="btn" onError={(e) => setErr(e.message)} run={() => add({ refs: [...picked] })}>Add the {picked.size} I picked</Busy>}
        </div>
      )}
      {!rows.length ? <p className="small muted">{show === "waiting" ? "Everything from this statement is in Expenses." : "Nothing here."}</p> : (
        <div className="ex-list">
          {rows.slice(0, 400).map((t) => (
            <div key={t.ref} className="ex-row">
              <div className="who">
                {show === "waiting" && <input type="checkbox" aria-label="Pick" checked={picked.has(t.ref)} onChange={() => toggle(t.ref)} style={{ marginRight: 8 }} />}
                <b>{t.payee || "Payment"}</b>
                {t.guessed && <> <Pill kind="warn">Check</Pill></>}
                {t.salary && !t.in_expenses && <span className="small muted"> · salary</span>}
                <div className="small muted">{dayName(t.date)} · {t.narration}</div>
                {t.kind === "debit" ? (
                  <select className="select" style={{ marginTop: 6, maxWidth: 280 }} value={t.category} aria-label="Category" onChange={(e) => setCat(t, e.target.value)}>
                    {s.categories.map((c) => <option key={c.key} value={c.key}>{c.label}</option>)}
                  </select>
                ) : <div className="small">{t.label}</div>}
              </div>
              <div className="amt">
                <span className="money">{t.kind === "credit" ? "+" : ""}{money(t.amount)}</span>
                {t.in_expenses && <Pill kind="on">In Expenses</Pill>}
                {!t.expense && t.kind === "debit" && <span className="small muted">Not spending</span>}
              </div>
            </div>
          ))}
          {rows.length > 400 && <p className="small muted">Showing the first 400 of {rows.length}. "Add all" adds every one (except rows to check, and salaries unless ticked).</p>}
        </div>
      )}
    </div>
  );
}

export default function BankStatement() {
  const [data, setData] = useState(null);
  const [s, setS] = useState(null);
  const [err, setErr] = useState("");
  const [tab, setTab] = useState("picture");
  useEffect(() => { api("/bank-statement").then((d) => { setData(d); setS(d.latest); }).catch((e) => setErr(e.message)); }, []);
  const head = { kicker: "Structure · Bank statement reader", id: "ST-14", pillar: "Structure", money: true };
  const title = <>See where the money <em>went</em>.</>;
  const sub = "Upload the statement from net banking: money in and out, where it went, the payments that repeat — and into Expenses in one tap.";
  if (!data) return <Sheet {...head} title={title} sub={sub}>{err ? <FormError msg={err} /> : <Loading />}</Sheet>;
  return (
    <Sheet {...head} title={title} sub={sub}>
      {!s && <UploadBox onRead={setS} />}
      {s && (
        <>
          <div className="ex-top">
            <span className="small">
              {s.bank || "Bank statement"}{s.account_last4 ? ` · account ending ${s.account_last4}` : ""} · {dayName(s.from)} to {dayName(s.to)}
              <span className="muted"> · read {ago(s.created_at)}, deleted on its own {istTime(s.expires_at)}</span>
            </span>
            <Busy className="btn sm ghost" run={async () => { await api(`/bank-statement/${s.id}`, { method: "DELETE" }); setS(null); }}>Delete it now</Busy>
          </div>
          <div className="statgrid">
            <div className="stat"><span className="label">Money in</span><span className="num money">{whole(s.money_in)}</span></div>
            <div className="stat"><span className="label">Spent</span><span className="num money">{whole(s.spent)}</span>
              <span className="small muted">Money out, without cash withdrawn and transfers to your own accounts.</span></div>
            <div className="stat"><span className="label">Not in Expenses yet</span><span className="num">{s.waiting}</span>
              <span className="small muted">{whole(s.waiting_amount)}</span></div>
          </div>
          {s.warnings.length > 0 && (
            <div className="ex-jumps"><b>Check these</b>{s.warnings.map((w) => <span key={w} className="small">{w}</span>)}</div>
          )}
          <Tabs tabs={[["picture", "Where it went"], ["payments", "The payments"], ["repeat", "Repeating"]]} value={tab} onChange={setTab} />
          {tab === "picture" && <Picture s={s} />}
          {tab === "payments" && <Payments s={s} onChange={setS} />}
          {tab === "repeat" && <Repeating s={s} onChange={setS} />}
          <UploadBox small onRead={(x) => { setS(x); setTab("picture"); }} />
        </>
      )}
    </Sheet>
  );
}
