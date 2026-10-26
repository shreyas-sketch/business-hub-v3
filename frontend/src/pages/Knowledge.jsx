import React, { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { ago, api, rs } from "../api.js";
import { Busy, Field, FormError, Sheet, Switch, useHub, useLoad } from "../ui.jsx";
import { Loading, Num, Pill } from "../v2.jsx";
import SharedInputs from "../components/SharedInputs.jsx";
import "./knowledge.css";

/* Knowledge Hub (the Business Brain, v2.1, 3.1): fill it once; the website, brand kit, compliance calendar, cash flow,
   targets and every AI writer read from it. Saves go to /api/knowledge/<section>; the basics stay on /start. */

const todayIso = () => new Date().toLocaleDateString("en-CA");   // YYYY-MM-DD, the owner's own day
const SECTION_LABEL = { basics: "your business basics", contact: "your contact details", identity: "business identity", money: "money basics",
  targets: "targets", brand: "brand" };

function SectionHead({ s, children }) {
  if (!s) return null;
  return (
    <div className="kh-head">
      <div className="stack" style={{ gap: 6 }}>
        <div className="row" style={{ gap: 10 }}>
          <h3>{s.label}</h3>
          <Pill kind={s.done ? "on" : "warn"}>{s.done ? "Done" : `${s.missing.length} to add`}</Pill>
        </div>
        {!s.done && <span className="small muted">Missing: {s.missing.join(", ")}</span>}
        {s.unlocks?.path && <span className="small muted">Switches on <Link to={s.unlocks.path}>{s.unlocks.label}</Link></span>}
      </div>
      {children}
    </div>
  );
}

/** "Update my hub": what was built from the old details, and the AI tokens bringing it up to date will use — shown before
    anything runs. Saving the Knowledge Hub never spends runs on its own. */
function UpdateHub({ updates, onChange }) {
  const { toast, refresh } = useHub();
  const [plan, setPlan] = useState(null);
  const key = (updates || []).map((u) => `${u.key}:${u.reason}:${u.updated}`).join(",");
  useEffect(() => { api("/knowledge/update").then(setPlan).catch(() => setPlan(null)); }, [key]);
  if (!plan?.items?.length) return null;
  const short = plan.runs > plan.runs_left;
  return (
    <div className="panel active kh-banner" role="status">
      <div className="stack" style={{ gap: 4 }}>
        <span className="label">Update my hub</span>
        <p className="small">Built from your old details: {plan.items.map((i) => i.label).join(", ")}.</p>
        <p className="small muted">Updating uses {plan.runs} AI {plan.runs === 1 ? "token" : "tokens"} — you have {plan.runs_left} left this month. Anything you edited yourself is kept.</p>
      </div>
      <div className="row">
        <Busy className="btn primary ai" disabled={short} run={async () => {
          const r = await api("/knowledge/update", { method: "POST" });
          onChange(r.updates); setPlan(r.plan); refresh();
          const done = Object.values(r.result).filter((v) => v.startsWith("refreshed")).length;
          toast(done ? `Updated ${done} ${done === 1 ? "thing" : "things"}` : "Nothing could be updated just now", done ? "ok" : "warn");
        }}>Update my hub · {plan.runs} {plan.runs === 1 ? "token" : "tokens"}</Busy>
        {short && <span className="small" style={{ color: "var(--amber)" }}>Not enough tokens left this month for all of it.</span>}
      </div>
    </div>
  );
}

function Updates({ updates, onChange }) {
  const { toast } = useHub();
  const [confirm, setConfirm] = useState(null);
  updates = (updates || []).filter((u) => u.reason === "edited");   // the rest is on "Update my hub"
  if (!updates.length) return null;
  return (
    <div className="panel active kh-banner" role="status">
      <div className="stack" style={{ gap: 4 }}>
        <span className="label">You edited these yourself</span>
        <p className="small muted">They were built from the old details, and you've changed them since, so we kept your version. Your choice:</p>
      </div>
      {updates.map((u) => (
        <div key={u.key} className="kh-update">
          <div className="stack" style={{ gap: 4 }}>
            <b>{u.path ? <Link to={u.path}>{u.label}</Link> : u.label}</b>
            <span className="small muted">{u.why}{u.sections?.length ? ` Changed: ${u.sections.map((x) => SECTION_LABEL[x] || x).join(", ")}.` : ""}</span>
            {u.note && <span className="small muted">{u.note}</span>}
          </div>
          {confirm === u.key ? (
            <div className="stack" style={{ gap: 8 }}>
              <span className="small">{u.reason === "edited" ? "This replaces your own edits with a fresh version from the Knowledge Hub. It may use 1 AI token." : "This tries the refresh again. It may use 1 AI token."}</span>
              <div className="row">
                <Busy className="btn sm primary ai" run={async () => { onChange(await api(`/knowledge/updates/${u.key}/refresh`, { method: "POST" })); setConfirm(null); toast(`${u.label} refreshed`); }}>
                  {u.reason === "edited" ? "Yes, replace my edits" : "Try again"}
                </Busy>
                <button className="btn sm ghost" onClick={() => setConfirm(null)}>Cancel</button>
              </div>
            </div>
          ) : (
            <div className="row">
              <button className="btn sm" onClick={() => setConfirm(u.key)}>Refresh</button>
              {u.reason === "edited" && (
                <Busy className="btn sm ghost" run={async () => { onChange(await api(`/knowledge/updates/${u.key}/keep`, { method: "POST" })); toast("Kept your version"); }}>Keep mine</Busy>
              )}
            </div>
          )}
        </div>
      ))}
    </div>
  );
}

function Summary({ rows }) {
  return (
    <dl className="kh-dl">
      {rows.map(([k, v]) => (
        <React.Fragment key={k}><dt>{k}</dt><dd className={v ? "" : "muted"}>{v || "Not added yet"}</dd></React.Fragment>
      ))}
    </dl>
  );
}

function Identity({ d, opts, onSaved }) {
  const { toast } = useHub();
  const i0 = d.identity || {};
  const [f, setF] = useState({ entity: i0.entity || "", gst_registered: i0.gst_registered ?? false, gstin: i0.gstin || "", gst_scheme: i0.gst_scheme || "monthly",
    state: i0.state || "", staff: i0.staff ?? "", registrations: i0.registrations || [] });
  const [err, setErr] = useState("");
  const set = (k) => (v) => setF({ ...f, [k]: v });
  const toggleReg = (v) => setF({ ...f, registrations: f.registrations.includes(v) ? f.registrations.filter((x) => x !== v) : [...f.registrations, v] });
  const ready = f.entity && f.state && f.staff !== "" && Number(f.staff) >= 0;
  const save = async () => {
    const body = { ...f, staff: Number(f.staff), gstin: f.gst_registered ? f.gstin.trim().toUpperCase() : "" };
    onSaved(await api("/knowledge/identity", { method: "PUT", body }));
    toast("Saved. Your compliance calendar follows it.");
  };
  return (
    <>
      <div className="grid2">
        <Field label="Legal entity">
          <select className="select" value={f.entity} onChange={(e) => set("entity")(e.target.value)}>
            <option value="">Choose…</option>
            {opts.entities.map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}
          </select>
        </Field>
        <Field label="State" hint="Where the business is registered">
          <select className="select" value={f.state} onChange={(e) => set("state")(e.target.value)}>
            <option value="">Choose…</option>
            {opts.states.map((s) => <option key={s} value={s}>{s}</option>)}
          </select>
        </Field>
        <Field label="Number of staff" hint="People on your payroll, including you if you draw a salary">
          <Num min="0" step="1" value={f.staff} onChange={set("staff")} />
        </Field>
        <div className="field kh-switchfield">
          <span className="label">GST</span>
          <Switch checked={f.gst_registered} onChange={set("gst_registered")} label={f.gst_registered ? "GST registered" : "Not registered for GST"} />
        </div>
        {f.gst_registered && (
          <>
            <Field label="GSTIN" hint="15 characters, like 27ABCDE1234F1Z5">
              <input className="input mono" maxLength={15} value={f.gstin} onChange={(e) => set("gstin")(e.target.value.toUpperCase())} />
            </Field>
            <Field label="How you file" hint="QRMP: returns every quarter, tax paid every month">
              <select className="select" value={f.gst_scheme} onChange={(e) => set("gst_scheme")(e.target.value)}>
                <option value="monthly">Monthly returns</option>
                <option value="qrmp">Quarterly returns (QRMP)</option>
              </select>
            </Field>
          </>
        )}
      </div>
      <div className="stack" style={{ gap: 8 }}>
        <span className="label">Registrations you hold</span>
        <div className="kh-checks">
          {opts.registrations.map((r) => (
            <label key={r.value} className="kh-check"><input type="checkbox" checked={f.registrations.includes(r.value)} onChange={() => toggleReg(r.value)} />{r.label}</label>
          ))}
        </div>
      </div>
      <FormError msg={err} />
      <div><Busy className="btn primary" disabled={!ready} onError={setErr} run={save}>Save identity</Busy></div>
    </>
  );
}

const BLANK_COST = { name: "", amount: "", day: 1, kind: "other" };

function MoneyBasics({ d, opts, onSaved }) {
  const { toast } = useHub();
  const m = d.money || {};
  const [f, setF] = useState({ payment_terms_days: m.payment_terms_days ?? "", bank_balance: m.bank_balance ?? "", bank_balance_on: m.bank_balance_on || todayIso(),
    fixed_costs: (m.fixed_costs || []).map((c) => ({ ...c })), gst_rate: m.gst_rate ?? 18, safety_buffer: m.safety_buffer ?? "" });
  const [err, setErr] = useState("");
  const set = (k) => (v) => setF({ ...f, [k]: v });
  const setCost = (i, k, v) => setF({ ...f, fixed_costs: f.fixed_costs.map((c, j) => (j === i ? { ...c, [k]: v } : c)) });
  const costsOk = f.fixed_costs.every((c) => c.name.trim().length >= 2 && Number(c.amount) > 0 && Number(c.day) >= 1 && Number(c.day) <= 31);
  const ready = f.payment_terms_days !== "" && f.bank_balance !== "" && f.bank_balance_on && f.bank_balance_on <= todayIso() && costsOk;
  const monthly = f.fixed_costs.reduce((s, c) => s + (Number(c.amount) || 0), 0);
  const save = async () => {
    const body = { payment_terms_days: Number(f.payment_terms_days), bank_balance: Number(f.bank_balance), bank_balance_on: f.bank_balance_on,
      fixed_costs: f.fixed_costs.map((c) => ({ name: c.name.trim(), amount: Number(c.amount), day: Number(c.day), kind: c.kind })),
      gst_rate: Number(f.gst_rate), safety_buffer: Number(f.safety_buffer) || 0 };
    onSaved(await api("/knowledge/money", { method: "PUT", body }));
    toast("Saved. Your cash flow starts from this balance.");
  };
  return (
    <>
      {m.balance_days_old > 7 && <p className="kh-note">Your bank balance is {m.balance_days_old} days old. Add today's for a true cash-flow forecast.</p>}
      <div className="grid2">
        <Field label="Bank balance (₹)" hint={f.bank_balance !== "" ? <>All your business accounts together: <span className="money">{rs(f.bank_balance)}</span>. An overdraft is a minus number.</> : "All your business accounts together. An overdraft is a minus number."}>
          <Num step="any" value={f.bank_balance} onChange={set("bank_balance")} />
        </Field>
        <Field label="Balance on" hint="The day you checked it">
          <input className="input" type="date" max={todayIso()} value={f.bank_balance_on} onChange={(e) => set("bank_balance_on")(e.target.value)} />
        </Field>
        <Field label="Usual payment terms (days)" hint="How many days customers usually get to pay. 0 if they pay on the spot.">
          <Num min="0" max="365" step="1" value={f.payment_terms_days} onChange={set("payment_terms_days")} />
        </Field>
        <Field label="Usual GST rate">
          <select className="select" value={f.gst_rate} onChange={(e) => set("gst_rate")(Number(e.target.value))}>
            {opts.gst_rates.map((r) => <option key={r} value={r}>{r}%</option>)}
          </select>
        </Field>
        <Field label="Safety buffer (₹) — optional" hint="The cash flow warns you when the balance would drop below this.">
          <Num min="0" step="any" value={f.safety_buffer} onChange={set("safety_buffer")} />
        </Field>
      </div>
      <div className="stack" style={{ gap: 8 }}>
        <div className="row between">
          <span className="label">Fixed monthly costs</span>
          {monthly > 0 && <span className="small muted">Total <span className="money">{rs(monthly)}</span> a month</span>}
        </div>
        {f.fixed_costs.length === 0 && <p className="small muted">Salaries, rent, EMIs, subscriptions — anything that goes out every month on the same day.</p>}
        {f.fixed_costs.map((c, i) => (
          <div key={i} className="kh-cost">
            <Field label="What"><input className="input" maxLength={80} value={c.name} placeholder="Salaries" onChange={(e) => setCost(i, "name", e.target.value)} /></Field>
            <Field label="₹ a month"><Num min="0" step="any" value={c.amount} onChange={(v) => setCost(i, "amount", v)} /></Field>
            <Field label="Day"><Num min="1" max="31" step="1" value={c.day} onChange={(v) => setCost(i, "day", v)} /></Field>
            <Field label="Kind">
              <select className="select" value={c.kind} onChange={(e) => setCost(i, "kind", e.target.value)}>
                {opts.cost_kinds.map((k) => <option key={k.value} value={k.value}>{k.label}</option>)}
              </select>
            </Field>
            <button className="btn sm ghost kh-remove" aria-label={`Remove ${c.name || "this cost"}`} onClick={() => setF({ ...f, fixed_costs: f.fixed_costs.filter((_, j) => j !== i) })}>Remove</button>
          </div>
        ))}
        {f.fixed_costs.length < 40 && <div><button className="btn sm ghost" onClick={() => setF({ ...f, fixed_costs: [...f.fixed_costs, { ...BLANK_COST }] })}>Add a cost</button></div>}
      </div>
      <FormError msg={err} />
      <div><Busy className="btn primary" disabled={!ready} onError={setErr} run={save}>Save money basics</Busy></div>
    </>
  );
}

function Targets({ d, onSaved }) {
  const { toast } = useHub();
  const [v, setV] = useState(d.targets?.monthly_revenue ?? "");
  const [err, setErr] = useState("");
  return (
    <>
      <Field label="Monthly revenue target (₹)" hint={Number(v) > 0 ? <><span className="money">{rs(v)}</span> a month. The same number your Magic Number tracks.</> : "The same number your Magic Number tracks."}>
        <Num min="0" step="any" value={v} onChange={setV} />
      </Field>
      <FormError msg={err} />
      <div><Busy className="btn primary" disabled={!(Number(v) > 0)} onError={setErr} run={async () => {
        onSaved(await api("/knowledge/targets", { method: "PUT", body: { monthly_revenue: Number(v) } }));
        toast("Saved");
      }}>Save target</Busy></div>
    </>
  );
}

export default function Knowledge() {
  const [loaded, reload, err] = useLoad("/knowledge");
  const [d, setD] = useState(null);
  useEffect(() => { if (loaded) setD(loaded); }, [loaded]);
  // After a save, what costs no AI token updates in the background; look again shortly for what waits for "Update my hub".
  const saved = (next) => { setD(next); setTimeout(() => reload(), 4000); };

  const head = { kicker: "Structure · Knowledge Hub", id: "KH-01", pillar: "Structure", title: <>Your <em>Knowledge Hub</em>.</>,
    sub: "Fill it once. Your website, brand kit, compliance calendar, cash flow, targets and every AI writer read from it." };
  if (!d) return <Sheet {...head}>{err ? <FormError msg={err.message} /> : <Loading />}</Sheet>;
  const c = d.completeness;
  const sec = Object.fromEntries(c.sections.map((s) => [s.key, s]));
  const b = d.basics || {};
  const offers = (b.offers || []).map((o) => o.name).filter(Boolean).join(", ");
  return (
    <Sheet {...head}>
      <div className="panel kh-meter">
        <div className="row between">
          <span className="label">How complete</span>
          {d.version > 0 && <span className="small muted">Version {d.version}</span>}
        </div>
        <div className="row" style={{ alignItems: "baseline", gap: 12 }}>
          <span className="num aqua">{c.percent}%</span>
          <span className="small muted">{c.hint}</span>
        </div>
        <div className="meter"><i style={{ width: `${c.percent}%` }} /></div>
        <div className="kh-chips">
          {c.sections.map((s) => <a key={s.key} href={`#kh-${s.key}`} className={`pill ${s.done ? "on" : ""}`}>{s.label}</a>)}
        </div>
      </div>

      <UpdateHub updates={d.updates} onChange={(updates) => setD({ ...d, updates })} />
      <Updates updates={d.updates} onChange={(updates) => setD({ ...d, updates })} />

      <section id="kh-inputs" className="panel">
        <SharedInputs title="Your website, a competitor, and 4 quick questions" showQuestions="always" />
      </section>

      <section id="kh-basics" className="panel">
        <SectionHead s={sec.basics}><Link className="btn sm ghost" to="/start">Edit</Link></SectionHead>
        <Summary rows={[["Name", b.name], ["What you do", b.industry], ["City", b.city], ["Offers", offers], ["Who buys", b.ideal_customer], ["Why customers choose you", b.why_us]]} />
      </section>

      <section id="kh-contact" className="panel">
        <SectionHead s={sec.contact}><Link className="btn sm ghost" to="/start">Edit</Link></SectionHead>
        <Summary rows={[["WhatsApp number", b.whatsapp], ["Email", b.email], ["Google review link", b.review_link], ["How customers pay you", b.payment_note]]} />
        <span className="small muted">Your website shows your phone and WhatsApp number as they are here, always.</span>
      </section>

      <section id="kh-identity" className="panel">
        <SectionHead s={sec.identity} />
        <Identity d={d} opts={d.options} onSaved={saved} />
      </section>

      <section id="kh-money" className="panel">
        <SectionHead s={sec.money} />
        <MoneyBasics d={d} opts={d.options} onSaved={saved} />
      </section>

      <section id="kh-targets" className="panel">
        <SectionHead s={sec.targets} />
        <Targets d={d} onSaved={saved} />
      </section>

      <section id="kh-brand" className="panel">
        <SectionHead s={sec.brand}>
          <div className="row"><Link className="btn sm ghost" to="/brand">Brand message</Link><Link className="btn sm ghost" to="/brand-kit">Brand kit</Link></div>
        </SectionHead>
        <Summary rows={[["Brand message", d.brand?.message], ["Tone of voice", d.brand?.voice?.tone]]} />
        <span className="small muted">The tone of voice is written in the Brand kit. Every AI writer in the hub uses it.</span>
      </section>
      {d.money?.bank_balance_saved_at && <p className="small muted">Bank balance last updated {ago(d.money.bank_balance_saved_at)}.</p>}
    </Sheet>
  );
}
