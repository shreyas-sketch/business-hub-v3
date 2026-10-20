import React, { useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { api, rs } from "../api.js";
import { Busy, Field, FormError, Gate, Sheet, canAct, useHub, useLoad } from "../ui.jsx";
import { Empty, Loading, Pill } from "../v2.jsx";
import "./cashflow.css";

/* Cash flow: the next 30 days (v2.1, 3.14). A forecast from what the owner entered — never their accounts. */

const todayIso = () => new Date().toLocaleDateString("en-CA", { timeZone: "Asia/Kolkata" });
const fmt = (iso, o = { day: "numeric", month: "short" }) => (iso ? new Date(`${iso}T00:00:00`).toLocaleDateString("en-IN", o) : "");
const wk = (iso) => fmt(iso, { weekday: "short", day: "numeric", month: "short" });
const signed = (n) => (n < 0 ? `−${rs(Math.abs(n))}` : rs(n));

function Balance({ current, onSaved }) {
  const { toast } = useHub();
  const [f, setF] = useState({ amount: current ?? "", on: todayIso() });
  const [err, setErr] = useState("");
  return (
    <div className="stack">
      <div className="grid2">
        <Field label="Bank balance today (₹)" hint="All your business bank accounts together. A negative number for an overdraft.">
          <input className="input" type="number" step="any" inputMode="decimal" value={f.amount} onChange={(e) => setF({ ...f, amount: e.target.value })} />
        </Field>
        <Field label="As on"><input className="input" type="date" max={todayIso()} value={f.on} onChange={(e) => setF({ ...f, on: e.target.value })} /></Field>
      </div>
      <FormError msg={err} />
      <div><Busy className="btn primary" disabled={f.amount === "" || !f.on} onError={setErr} run={async () => {
        await api("/cash-flow/balance", { method: "PUT", body: { amount: Number(f.amount), on: f.on } });
        toast("Balance saved");
        onSaved();
      }}>Save balance</Busy></div>
    </div>
  );
}

function Chart({ days, buffer, lowest }) {
  const low = Math.max(0, days.findIndex((d) => d.date === lowest?.date));
  const [sel, setSel] = useState(low);
  const W = 600, H = 220, pad = 10;
  const vals = days.map((d) => d.balance);
  const top = Math.max(0, buffer || 0, ...vals);
  const bot = Math.min(0, ...vals);
  const span = top - bot || 1;
  const y = (v) => pad + ((top - v) / span) * (H - 2 * pad);
  const step = W / days.length;
  const bw = Math.max(step - 3, 2);
  const d = days[Math.min(sel, days.length - 1)];
  const isLow = (v) => v < (buffer || 0);
  return (
    <div className="cf-chart">
      <div className="cf-read" aria-live="polite">
        <span className="small muted">{wk(d.date)}</span>
        <b className={isLow(d.balance) ? "money" : ""}>{signed(d.balance)}</b>
        {d.in > 0 && <span className="small cf-in">+{rs(d.in)} in</span>}
        {d.out > 0 && <span className="small cf-out">−{rs(d.out)} out</span>}
      </div>
      <svg viewBox={`0 0 ${W} ${H}`} preserveAspectRatio="none" role="img"
        aria-label={`Balance each day for 30 days. Lowest ${signed(lowest?.balance ?? 0)} on ${fmt(lowest?.date)}.`}>
        {days.map((x, i) => {
          const y0 = y(0), y1 = y(x.balance);
          const h = Math.max(Math.abs(y1 - y0), 1);
          return (
            <g key={x.date}>
              <rect className={`cf-bar ${isLow(x.balance) ? "low" : ""} ${i === sel ? "sel" : ""}`} x={i * step + 1.5} width={bw} y={Math.min(y0, y1)} height={h} rx="2" />
              <rect className="hit" x={i * step} width={step} y="0" height={H} onMouseEnter={() => setSel(i)} onClick={() => setSel(i)}>
                <title>{`${wk(x.date)}: ${signed(x.balance)}`}</title>
              </rect>
            </g>
          );
        })}
        <line className="zero" x1="0" x2={W} y1={y(0)} y2={y(0)} />
        {buffer > 0 && <line className="buffer" x1="0" x2={W} y1={y(buffer)} y2={y(buffer)} />}
      </svg>
      <div className="cf-axis"><span>{fmt(days[0].date)}</span><span>{fmt(days[Math.floor(days.length / 2)].date)}</span><span>{fmt(days[days.length - 1].date)}</span></div>
      <div className="cf-legend">
        <span><i style={{ background: "var(--aqua-deep)" }} />Balance at the end of the day</span>
        <span><i style={{ background: "var(--amber)" }} />Below {buffer > 0 ? `your safety buffer of ${rs(buffer)}` : "zero"}</span>
      </div>
    </div>
  );
}

const blankItem = () => ({ kind: "out", title: "", amount: "", recurrence: "none", date: todayIso(), day_of_month: "" });

function ItemForm({ initial, onDone, onCancel }) {
  const { toast } = useHub();
  const [f, setF] = useState(initial || blankItem());
  const [err, setErr] = useState("");
  const set = (k) => (e) => setF({ ...f, [k]: e.target.value });
  const ready = f.title.trim().length >= 2 && Number(f.amount) > 0 && (f.recurrence === "monthly" ? Number(f.day_of_month) >= 1 : f.date);
  return (
    <div className="stack">
      <div className="grid3">
        <Field label="Money">
          <select className="select" value={f.kind} onChange={set("kind")}><option value="out">Going out</option><option value="in">Coming in</option></select>
        </Field>
        <Field label="What for"><input className="input" maxLength={100} value={f.title} onChange={set("title")} placeholder={f.kind === "out" ? "Supplier payment" : "Rent from the godown"} /></Field>
        <Field label="Amount (₹)"><input className="input" type="number" min="1" step="any" inputMode="decimal" value={f.amount} onChange={set("amount")} /></Field>
        <Field label="Repeats">
          <select className="select" value={f.recurrence} onChange={set("recurrence")}><option value="none">Once</option><option value="monthly">Every month</option></select>
        </Field>
        {f.recurrence === "monthly"
          ? <Field label="Day of the month"><input className="input" type="number" min="1" max="31" value={f.day_of_month} onChange={set("day_of_month")} /></Field>
          : <Field label="Date"><input className="input" type="date" value={f.date || ""} onChange={set("date")} /></Field>}
      </div>
      <FormError msg={err} />
      <div className="row">
        <Busy className="btn sm primary" disabled={!ready} onError={setErr} run={async () => {
          const body = { kind: f.kind, title: f.title.trim(), amount: Number(f.amount), recurrence: f.recurrence,
            date: f.recurrence === "none" ? f.date : null, day_of_month: f.recurrence === "monthly" ? Number(f.day_of_month) : null };
          await api(initial?.id ? `/cash-flow/items/${initial.id}` : "/cash-flow/items", { method: initial?.id ? "PUT" : "POST", body });
          toast(initial?.id ? "Saved" : "Added");
          onDone();
        }}>{initial?.id ? "Save" : "Add"}</Busy>
        {onCancel && <button className="btn sm ghost" onClick={onCancel}>Cancel</button>}
      </div>
    </div>
  );
}

function Items({ items, onChange }) {
  const { toast } = useHub();
  const [edit, setEdit] = useState(null);
  const [adding, setAdding] = useState(false);
  return (
    <div className="panel">
      <div className="row between">
        <span className="label">Other money in and out</span>
        {!adding && <button className="btn sm" onClick={() => setAdding(true)}>Add</button>}
      </div>
      <p className="small muted">Salaries, rent and EMIs come from the money section of your <Link to="/knowledge">Knowledge Hub</Link>. Add anything else here — a one-off supplier payment, income that isn't an invoice.</p>
      {adding && <ItemForm onDone={() => { setAdding(false); onChange(); }} onCancel={() => setAdding(false)} />}
      <div className="cf-items">
        {!items.length && !adding && <span className="small muted">Nothing added yet.</span>}
        {items.map((it) => edit === it.id ? (
          <div key={it.id} className="it" style={{ display: "block" }}>
            <ItemForm initial={{ ...it, amount: it.amount, day_of_month: it.day_of_month || "", date: it.date || todayIso() }}
              onDone={() => { setEdit(null); onChange(); }} onCancel={() => setEdit(null)} />
          </div>
        ) : (
          <div key={it.id} className="it">
            <div>
              <b>{it.title}</b>
              <div className="small muted">{it.recurrence === "monthly" ? `Every month on day ${it.day_of_month}` : fmt(it.date, { day: "numeric", month: "short", year: "numeric" })}</div>
            </div>
            <div className="row">
              <span className={it.kind === "in" ? "cf-in" : "cf-out"}>{it.kind === "in" ? "+" : "−"}{rs(it.amount)}</span>
              <button className="btn sm ghost" onClick={() => setEdit(it.id)}>Edit</button>
              <Busy className="btn sm ghost" run={async () => {
                if (!window.confirm(`Remove “${it.title}”?`)) return;
                await api(`/cash-flow/items/${it.id}`, { method: "DELETE" });
                toast("Removed");
                onChange();
              }}>Delete</Busy>
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}

function Settings({ f, onSaved }) {
  const { toast } = useHub();
  const [s, setS] = useState({ safety_buffer: f.safety_buffer || 0, quote_probability: f.quote_probability || 0 });
  return (
    <div className="panel flat">
      <span className="label">How the forecast counts</span>
      <div className="grid2">
        <Field label="Safety buffer (₹)" hint="Warn me when the balance goes below this, not only below zero.">
          <input className="input" type="number" min="0" step="any" inputMode="decimal" value={s.safety_buffer} onChange={(e) => setS({ ...s, safety_buffer: e.target.value })} />
        </Field>
        <Field label="Count open quotations at (%)" hint="0 leaves quotations out. 30 counts 30% of each sent quotation, after its validity and your payment terms.">
          <input className="input" type="number" min="0" max="100" value={s.quote_probability} onChange={(e) => setS({ ...s, quote_probability: e.target.value })} />
        </Field>
      </div>
      <div><Busy className="btn sm" run={async () => {
        await api("/cash-flow/settings", { method: "PUT", body: { safety_buffer: Number(s.safety_buffer) || 0, quote_probability: Math.max(0, Math.min(100, Number(s.quote_probability) || 0)) } });
        toast("Saved");
        onSaved();
      }}>Save</Busy></div>
    </div>
  );
}

function CashInner() {
  const { me } = useHub();
  const owner = canAct(me, "owner");
  const [whatIf, setWhatIf] = useState(null);
  const path = whatIf ? `/cash-flow?delay_id=${encodeURIComponent(whatIf.id)}&delay_days=15` : "/cash-flow";
  const [f, reload, err] = useLoad(path);
  const [fresh, setFresh] = useState(false);
  const daysWithEvents = useMemo(() => (f?.days || []).filter((d) => d.events.length), [f]);
  const head = { kicker: "Structure · Cash flow", id: "ST-07", pillar: "Structure", money: true };
  if (!f) return <Sheet {...head} title={<>The next <em>30 days</em>.</>}>{err ? <FormError msg={err.message} /> : <Loading />}</Sheet>;

  if (!f.ready) {
    return (
      <Sheet {...head} title={<>Start with today's <em>bank balance</em>.</>}
        sub="The forecast starts from your bank balance, then adds what customers owe you and takes away salaries, rent and other payments, day by day.">
        <div className="cf-label">{f.label}</div>
        <div className="panel active">
          {owner ? <Balance onSaved={reload} /> : <p>Ask the owner to add today's bank balance. The forecast starts from it.</p>}
        </div>
        <Items items={f.items || []} onChange={reload} />
      </Sheet>
    );
  }

  const buffer = f.safety_buffer || 0;
  const s = f.shortfall;
  const title = s ? <>Short by <em className="money">{rs(s.short_by)}</em> on {fmt(s.date)}.</> : <>No shortfall in the next <em>30 days</em>.</>;
  return (
    <Sheet {...head} title={title}
      sub="Your balance each day: dues on the day each customer usually pays, salaries and fixed costs on their day, plus anything you add.">
      <div className="cf-label">{f.label}</div>

      {whatIf && (
        <div className="cf-whatif">
          <span>What if <b>{whatIf.customer}</b> pays 15 days later than usual?</span>
          <button className="btn sm" onClick={() => setWhatIf(null)}>Back to the forecast</button>
        </div>
      )}

      <div className="statgrid">
        <div className="stat"><span className="label">Starting balance</span><span className="num">{signed(f.opening)}</span>
          <span className="small muted">Bank balance on {fmt(f.balance_on)}{f.received_since_balance ? ` + ${rs(f.received_since_balance)} marked paid since` : ""}</span></div>
        <div className="stat"><span className="label">Lowest point</span><span className={`num ${f.lowest.balance < buffer ? "money" : ""}`}>{signed(f.lowest.balance)}</span>
          <span className="small muted">on {wk(f.lowest.date)}</span></div>
        <div className="stat"><span className="label">Coming in · 30 days</span><span className="num">{rs(f.money_in)}</span><span className="small muted">Expected, not certain</span></div>
        <div className="stat"><span className="label">Going out · 30 days</span><span className="num">{rs(f.money_out)}</span></div>
      </div>

      {f.balance_days_old > 7 && (
        <div className="panel active">
          <span className="label">Your balance is {f.balance_days_old} days old</span>
          <p className="small">The forecast is only as good as the balance it starts from.</p>
          {owner ? (fresh ? <Balance current={null} onSaved={() => { setFresh(false); reload(); }} /> : <div><button className="btn sm primary" onClick={() => setFresh(true)}>Update today's balance</button></div>)
            : <span className="small muted">Ask the owner to update it.</span>}
        </div>
      )}

      {s ? (
        <div className="cf-short">
          <b>{rs(s.short_by)} short on {wk(s.date)}{s.until && s.until !== s.date ? `, until ${wk(s.until)}` : ""}</b>
          <span className="small">The balance goes {s.balance < 0 ? `to ${signed(s.balance)}` : `below your safety buffer of ${rs(buffer)}`}. Collecting the dues below first closes the gap.</span>
        </div>
      ) : (
        <div className="cf-ok small">Your balance stays {buffer > 0 ? `above your safety buffer of ${rs(buffer)}` : "above zero"} every day of the next 30, if customers pay as they usually do.</div>
      )}

      <div className="panel"><Chart key={path} days={f.days} buffer={buffer} lowest={f.lowest} /></div>

      {f.close_the_gap?.length > 0 && (
        <div className="panel">
          <span className="label">{s ? "How to close the gap — collect these first" : "Dues to keep an eye on"}</span>
          <div className="cf-gap">
            {f.close_the_gap.map((g) => (
              <div key={g.id}>
                <span>
                  <b>{g.customer}</b>
                  <span className="small muted"> · due {fmt(g.due_date)}{g.expected ? ` · expected ${fmt(g.expected)}` : " · not expected in these 30 days"}</span>
                  <div className="row" style={{ gap: 6, marginTop: 6 }}>
                    <Pill kind={g.habit === "pays on time" ? "on" : g.habit === "often late" ? "warn" : ""}>{g.habit}</Pill>
                    {g.late && <Pill kind="warn">Past due</Pill>}
                    {g.before_short && <Pill kind="money">Before the shortfall</Pill>}
                  </div>
                </span>
                <span className="amt">{rs(g.amount)}</span>
                <div className="acts">
                  {g.expected && (!whatIf || whatIf.id !== g.id) && <button className="btn sm ghost" onClick={() => setWhatIf({ id: g.id, customer: g.customer })}>What if they pay 15 days late?</button>}
                  {me.features?.money_owed && <Link className="btn sm" to="/money">Chase it in Money owed</Link>}
                </div>
              </div>
            ))}
          </div>
          {f.can_follow_up && <span className="small muted">Automatic payment follow-ups can chase these by email — see <Link to="/agents">Automations</Link>.</span>}
        </div>
      )}

      <div className="panel flat cf-days">
        <span className="label">Day by day</span>
        {!daysWithEvents.length ? <Empty>No money in or out in the next 30 days yet.</Empty> : daysWithEvents.map((d) => (
          <details key={d.date}>
            <summary>
              <span>{wk(d.date)}</span>
              <span className="small muted ev">{d.events.length} {d.events.length === 1 ? "item" : "items"}{d.in ? ` · +${rs(d.in)}` : ""}{d.out ? ` · −${rs(d.out)}` : ""}</span>
              <span className={`bal ${d.balance < buffer ? "low money" : ""}`}>{signed(d.balance)}</span>
            </summary>
            <ul>{d.events.map((e, i) => (
              <li key={i}>
                <span>{e.title || (e.kind === "in" ? "Money in" : "Money out")}{e.late ? " (past due, expected now)" : ""}{e.source === "compliance" ? " · compliance" : e.source === "quote" ? " · quotation" : ""}</span>
                <span className={e.kind === "in" ? "cf-in" : "cf-out"}>{e.kind === "in" ? "+" : "−"}{rs(e.amount)}</span>
              </li>
            ))}</ul>
          </details>
        ))}
      </div>

      <Items items={f.items || []} onChange={reload} />
      <Settings key={`${f.safety_buffer}-${f.quote_probability}`} f={f} onSaved={reload} />
    </Sheet>
  );
}

export default function CashFlow() {
  return (
    <Gate feature="cash_flow" role="manager" kicker="Structure · Cash flow" id="ST-07"
      what="A forecast of your bank balance for the next 30 days, with the date it would go short and the dues to collect first.">
      <CashInner />
    </Gate>
  );
}
