import React, { useEffect, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { api, day, localPhone, rs, upload, waLink } from "../api.js";
import { Busy, Copy, Field, FormError, Modal, Sheet, Switch, canAct, useHub, useLoad } from "../ui.jsx";
import { Empty, Loading, Num, Pill, Tabs } from "../v2.jsx";
import VoiceNote from "../components/VoiceNote.jsx";
import "./running.css";
import "./invoicing.css";

const GST_RATES = [0, 5, 12, 18, 28];
const MODES = [["intra", "Same state — CGST + SGST"], ["inter", "Another state — IGST"], ["none", "No GST"]];
const Q_STATUS = { draft: ["Draft", "off"], sent: ["Sent", "on"], accepted: ["Accepted", "on"], lost: ["Lost", "off"] };
const todayIso = () => new Date().toLocaleDateString("en-CA");

/** The same sums as the server (desk.py totals): each line rounded to paise, GST per line, CGST/SGST split from the total GST. */
const r2 = (x) => Math.round((x + Number.EPSILON) * 100) / 100;
function totals(items, mode) {
  let sub = 0;
  let gst = 0;
  const amounts = items.map((i) => {
    const amt = r2((Number(i.qty) || 0) * (Number(i.rate) || 0));
    sub += amt;
    gst += mode === "none" ? 0 : r2((amt * (Number(i.gst) || 0)) / 100);
    return amt;
  });
  sub = r2(sub);
  gst = r2(gst);
  const out = { amounts, subtotal: sub, gst, total: r2(sub + gst), cgst: 0, sgst: 0, igst: 0 };
  if (mode === "intra") { out.cgst = r2(gst / 2); out.sgst = r2(gst - out.cgst); }
  else if (mode === "inter") out.igst = gst;
  return out;
}

const blankItem = () => ({ name: "", qty: "1", unit: "nos", rate: "", gst: 18 });
const blankForm = () => ({ customer: { name: "", phone: "", gstin: "", address: "" }, items: [blankItem()], gst_mode: "intra", notes: "", terms: "", valid_days: 15, lead_id: "" });
const fromQuote = (q) => ({
  customer: { name: q.customer?.name || "", phone: q.customer?.phone ? localPhone(q.customer.phone) : "", gstin: q.customer?.gstin || "", address: q.customer?.address || "" },
  items: (q.items || []).map((i) => ({ name: i.name, qty: String(i.qty), unit: i.unit || "nos", rate: i.rate ? String(i.rate) : "", gst: i.gst })),
  gst_mode: q.gst_mode || "intra", notes: q.notes || "", terms: q.terms || "", valid_days: q.valid_days || 15, lead_id: q.lead_id || "",
});

function DraftBox({ onDraft, initial = "" }) {
  const { me, refresh } = useHub();
  const [brief, setBrief] = useState(initial);
  const [err, setErr] = useState("");
  return (
    <div className="panel flat">
      <span className="label">Draft from a voice note or a few lines</span>
      <textarea className="textarea" maxLength={6000} value={brief} onChange={(e) => setBrief(e.target.value)} aria-label="What the quotation is for"
        placeholder="Who it is for and what they want: items, sizes, quantities and any rates you know" />
      <div className="rb-voice">
        <VoiceNote purpose="quote" onText={(t) => setBrief((b) => (b.trim() ? `${b.trim()}\n${t}` : t))} />
      </div>
      <FormError msg={err} />
      <div className="row">
        <Busy className="btn ai" disabled={brief.trim().length < 5} onError={setErr} run={async () => {
          const r = await api("/quotes/draft", { method: "POST", body: { brief: brief.trim() } });
          onDraft(r);
          refresh();
        }}>Draft the quotation</Busy>
        <span className="hint">Drafting uses 1 AI token.{me.features?.company_brain ? " Rates come from your Company Brain where it has them." : ""} Nothing is saved until you press Save.</span>
      </div>
    </div>
  );
}

function Editor({ quote, prefill, onClose, onSaved }) {
  const { toast } = useHub();
  const edit = !!quote;
  // "Make a quotation" on an inquiry: the customer and the inquiry come filled in, and their message is the draft's brief
  const [f, setF] = useState(() => (edit ? fromQuote(quote) : prefill
    ? { ...blankForm(), customer: { ...blankForm().customer, name: prefill.name, phone: prefill.phone }, lead_id: prefill.lead_id } : blankForm()));
  const [leads, setLeads] = useState([]);
  const [err, setErr] = useState("");
  useEffect(() => {
    if (edit) return;
    api("/leads").then((l) => setLeads((l || []).filter((x) => x.status === "new" || x.status === "contacted"))).catch(() => {});
  }, [edit]);
  const t = totals(f.items, f.gst_mode);
  const setC = (k) => (e) => setF({ ...f, customer: { ...f.customer, [k]: e.target.value } });
  const setI = (i, k) => (e) => setF({ ...f, items: f.items.map((it, j) => (j === i ? { ...it, [k]: e.target.value } : it)) });
  const named = f.items.filter((i) => i.name.trim());
  const zero = named.filter((i) => !(Number(i.rate) > 0)).length;
  const pickLead = (id) => {
    const l = leads.find((x) => x.id === id);
    setF({ ...f, lead_id: id, customer: l && !f.customer.name.trim() ? { ...f.customer, name: l.name || "", phone: l.phone ? localPhone(l.phone) : "" } : f.customer });
  };
  const fill = (r) => {
    const items = (r.items || []).map((i) => ({ name: i.name, qty: String(i.qty || 1), unit: i.unit || "nos", rate: i.rate ? String(i.rate) : "", gst: GST_RATES.includes(i.gst) ? i.gst : 18 }));
    setF((cur) => ({ ...cur, items: items.length ? items : cur.items, notes: r.notes || cur.notes,
      customer: { ...cur.customer, name: r.customer?.name || cur.customer.name, phone: r.customer?.phone || cur.customer.phone } }));
    toast(r.missing_rates ? `Drafted. ${r.missing_rates} ${r.missing_rates === 1 ? "line needs" : "lines need"} a rate` : "Drafted. Check every line, then save");
  };
  const save = async () => {
    if (f.customer.name.trim().length < 2) throw new Error("Add the customer's name.");
    if (!named.length) throw new Error("Add at least one line with a name.");
    if (named.some((i) => !(Number(i.qty) > 0))) throw new Error("Every line needs a quantity above 0.");
    if (zero && !window.confirm(`${zero} ${zero === 1 ? "line has" : "lines have"} no rate (₹0). Save anyway?`)) return;
    const body = {
      customer: { name: f.customer.name.trim(), phone: f.customer.phone.trim(), gstin: f.customer.gstin.trim().toUpperCase(), address: f.customer.address.trim() },
      items: named.map((i) => ({ name: i.name.trim(), qty: Number(i.qty), unit: i.unit.trim() || "nos", rate: Number(i.rate) || 0, gst: Number(i.gst) })),
      gst_mode: f.gst_mode, notes: f.notes.trim(), terms: f.terms.trim(), valid_days: Number(f.valid_days) || 15, lead_id: f.lead_id || null,
    };
    const q = await api(edit ? `/quotes/${quote.id}` : "/quotes", { method: edit ? "PUT" : "POST", body });
    toast(edit ? `Saved ${q.number}` : `Saved ${q.number} as a draft`);
    onSaved(q);
  };
  return (
    <div className="panel active">
      <div className="row between">
        <h2>{edit ? <>Edit <em>{quote.number}</em></> : <>New <em>quotation</em></>}</h2>
        <button className="btn sm ghost" onClick={onClose}>Cancel</button>
      </div>
      {!edit && <DraftBox onDraft={fill} initial={prefill?.brief || ""} />}

      <span className="label">Customer</span>
      <div className="grid2">
        <Field label="Name"><input className="input" maxLength={120} value={f.customer.name} onChange={setC("name")} placeholder="Rohit Shah" /></Field>
        <Field label="WhatsApp number"><input className="input" inputMode="tel" maxLength={24} value={f.customer.phone} onChange={setC("phone")} placeholder="98200 12345" /></Field>
        <Field label="Customer GSTIN (optional)"><input className="input mono" maxLength={15} value={f.customer.gstin} onChange={setC("gstin")} placeholder="27ABCDE1234F1Z5" /></Field>
        <Field label="Address (optional)"><input className="input" maxLength={300} value={f.customer.address} onChange={setC("address")} placeholder="Thane West" /></Field>
      </div>
      {!edit && leads.length > 0 && (
        <Field label="Inquiry this is for (optional)" hint="When the quotation is accepted or lost, this lead is marked won or lost.">
          <select className="select" value={f.lead_id} onChange={(e) => pickLead(e.target.value)}>
            <option value="">None</option>
            {leads.map((l) => <option key={l.id} value={l.id}>{l.name}{l.phone ? ` · ${localPhone(l.phone)}` : ""}</option>)}
          </select>
        </Field>
      )}

      <span className="label">Items</span>
      <div className="rb-lines">
        <div className="rb-line head" aria-hidden="true"><span>Item</span><span>Qty</span><span>Unit</span><span>Rate ₹</span><span>GST</span><span style={{ textAlign: "right" }}>Amount</span><span /></div>
        {f.items.map((it, i) => (
          <div key={i} className={`rb-line ${it.name.trim() && !(Number(it.rate) > 0) ? "zero" : ""}`}>
            <label className="rb-cell"><span>Item</span><input className="input" maxLength={160} value={it.name} onChange={setI(i, "name")} aria-label={`Line ${i + 1}: item`} placeholder="Modular kitchen 10 ft" /></label>
            <label className="rb-cell"><span>Qty</span><input className="input" type="number" min="0" step="any" inputMode="decimal" value={it.qty} onChange={setI(i, "qty")} aria-label={`Line ${i + 1}: quantity`} /></label>
            <label className="rb-cell"><span>Unit</span><input className="input" maxLength={20} value={it.unit} onChange={setI(i, "unit")} aria-label={`Line ${i + 1}: unit`} /></label>
            <label className="rb-cell"><span>Rate ₹</span><input className="input rate" type="number" min="0" step="any" inputMode="decimal" value={it.rate} onChange={setI(i, "rate")} aria-label={`Line ${i + 1}: rate in rupees`} placeholder={it.name.trim() ? "Add rate" : ""} /></label>
            <label className="rb-cell"><span>GST</span>
              <select className="select" value={it.gst} disabled={f.gst_mode === "none"} onChange={(e) => setF({ ...f, items: f.items.map((x, j) => (j === i ? { ...x, gst: Number(e.target.value) } : x)) })} aria-label={`Line ${i + 1}: GST rate`}>
                {GST_RATES.map((g) => <option key={g} value={g}>{g}%</option>)}
              </select>
            </label>
            <span className="amt money small">{rs(t.amounts[i])}</span>
            <button className="rb-x" type="button" aria-label={`Remove line ${i + 1}`} disabled={f.items.length === 1} onClick={() => setF({ ...f, items: f.items.filter((_, j) => j !== i) })}>×</button>
          </div>
        ))}
      </div>
      <div className="row between">
        <button className="btn sm" type="button" disabled={f.items.length >= 60} onClick={() => setF({ ...f, items: [...f.items, blankItem()] })}>Add a line</button>
        {zero > 0 && <span className="small" style={{ color: "var(--amber)" }}>{zero} {zero === 1 ? "line needs" : "lines need"} a rate</span>}
      </div>

      <div className="grid2" style={{ alignItems: "start" }}>
        <Field label="GST">
          <select className="select" value={f.gst_mode} onChange={(e) => setF({ ...f, gst_mode: e.target.value })}>
            {MODES.map(([k, l]) => <option key={k} value={k}>{l}</option>)}
          </select>
        </Field>
        <div className="rb-totals" aria-live="polite">
          <div><span className="muted">Subtotal</span><span className="money">{rs(t.subtotal)}</span></div>
          {f.gst_mode === "intra" && <><div><span className="muted">CGST</span><span className="money">{rs(t.cgst)}</span></div><div><span className="muted">SGST</span><span className="money">{rs(t.sgst)}</span></div></>}
          {f.gst_mode === "inter" && <div><span className="muted">IGST</span><span className="money">{rs(t.igst)}</span></div>}
          <div className="grand"><span>Total</span><span className="money">{rs(t.total)}</span></div>
        </div>
      </div>

      <div className="grid2">
        <Field label="Valid for (days)"><Num min="1" max="365" step="1" value={f.valid_days} onChange={(v) => setF({ ...f, valid_days: v })} /></Field>
        <Field label="Notes (optional)"><input className="input" maxLength={1000} value={f.notes} onChange={(e) => setF({ ...f, notes: e.target.value })} placeholder="Delivery in 3 weeks" /></Field>
      </div>
      <Field label="Terms (optional)" hint="Leave empty to use the usual terms from your business details.">
        <textarea className="textarea" maxLength={1500} value={f.terms} onChange={(e) => setF({ ...f, terms: e.target.value })} placeholder="50% advance, balance on installation" />
      </Field>
      <FormError msg={err} />
      <div className="row">
        <Busy className="btn primary" onError={setErr} run={save}>{edit ? "Save changes" : "Save quotation"}</Busy>
        <button className="btn ghost" onClick={onClose}>Cancel</button>
      </div>
    </div>
  );
}

function Settings({ onClose }) {
  const { me, toast } = useHub();
  const [s] = useLoad("/quotes/settings");
  const [f, setF] = useState(null);
  const [err, setErr] = useState("");
  useEffect(() => { if (s && !f) setF({ gstin: s.gstin || "", address: s.address || "", payment: s.payment || "", terms: s.terms || "" }); }, [s, f]);
  const owner = canAct(me, "owner");
  const set = (k) => (e) => setF({ ...f, [k]: e.target.value });
  return (
    <div className="panel">
      <div className="row between"><span className="label">Your business details on every quotation</span><button className="btn sm ghost" onClick={onClose}>Close</button></div>
      {!f ? <Loading /> : (
        <>
          <div className="grid2">
            <Field label="Your GSTIN"><input className="input mono" maxLength={15} value={f.gstin} onChange={set("gstin")} disabled={!owner} placeholder="27ABCDE1234F1Z5" /></Field>
            <Field label="How customers pay you" hint="UPI id or bank details, printed on quotations and invoices."><input className="input" maxLength={300} value={f.payment} onChange={set("payment")} disabled={!owner} placeholder="UPI shreeganesh@okicici" /></Field>
          </div>
          <Field label="Business address"><textarea className="textarea" style={{ minHeight: 70 }} maxLength={300} value={f.address} onChange={set("address")} disabled={!owner} /></Field>
          <Field label="Usual terms" hint="Used on a quotation when its own terms are left empty."><textarea className="textarea" maxLength={1500} value={f.terms} onChange={set("terms")} disabled={!owner} placeholder="50% advance. Prices valid for 15 days." /></Field>
          <FormError msg={err} />
          {owner ? (
            <div><Busy className="btn primary" onError={setErr} run={async () => {
              await api("/quotes/settings", { method: "PUT", body: { ...f, gstin: f.gstin.trim().toUpperCase(), address: f.address.trim(), payment: f.payment.trim(), terms: f.terms.trim() } });
              toast("Business details saved");
              onClose();
            }}>Save details</Busy></div>
          ) : <p className="small muted">Only the business owner can change these.</p>}
          <EmailInvoices owner={owner} />
        </>
      )}
    </div>
  );
}

/** "Email invoices automatically": off until the owner switches it on. An invoice is never a reminder. */
function EmailInvoices({ owner }) {
  const { toast } = useHub();
  const [s, reload] = useLoad("/invoices/settings");
  if (!s) return null;
  return (
    <div className="stack inv-email" style={{ gap: 6 }}>
      <Switch checked={s.email_auto} disabled={!owner} label="Email invoices automatically"
        onChange={async (on) => {
          await api("/invoices/settings", { method: "PUT", body: { email_auto: on } });
          toast(on ? "Invoices will be emailed to customers who have an email" : "Invoices won't be emailed on their own");
          reload();
        }} />
      <span className="hint">When it's on, every new invoice — from a quotation, typed here, or a payment through your gateway — is emailed to the customer as a PDF, if you have their email. It's only the invoice: no reminder words, no payment chasing.</span>
      {s.email_auto && !s.email_ready && <span className="small" style={{ color: "var(--amber)" }}>Email isn't set up on this hub yet, so nothing goes out until it is.</span>}
    </div>
  );
}

const blankInvoice = () => ({ customer: { name: "", phone: "", email: "", gstin: "", state: "" }, items: [blankItem()], gst_mode: "", due_in_days: 7, paid_already: false });

/** A direct invoice — typed, or from a billing entry ("Sold 40 boxes to Mehta Traders, ₹850 each…") checked here first. */
function InvoiceEditor({ onClose, onSaved }) {
  const { me, toast } = useHub();
  const [f, setF] = useState(blankInvoice);
  const [opts] = useLoad("/invoices/settings");      // all 36 states and union territories, and the owner's own GST position
  const states = opts?.states || [];
  const [entry, setEntry] = useState("");
  const [err, setErr] = useState("");
  const mode = f.gst_mode || "intra";
  const t = totals(f.items, mode);
  const setC = (k) => (e) => setF({ ...f, customer: { ...f.customer, [k]: e.target.value } });
  const setI = (i, k) => (e) => setF({ ...f, items: f.items.map((it, j) => (j === i ? { ...it, [k]: e.target.value } : it)) });
  const named = f.items.filter((i) => i.name.trim());
  const zero = named.filter((i) => !(Number(i.rate) > 0)).length;
  const save = async () => {
    if (f.customer.name.trim().length < 2) throw new Error("Add the customer's name.");
    if (!named.length) throw new Error("Add at least one line with a name.");
    if (named.some((i) => !(Number(i.qty) > 0))) throw new Error("Every line needs a quantity above 0.");
    if (zero) throw new Error(`${zero} ${zero === 1 ? "line has" : "lines have"} no rate. Add it — an invoice can't guess a price.`);
    const c = f.customer;
    const inv = await api("/invoices", { method: "POST", body: {
      customer: { name: c.name.trim(), phone: c.phone.trim(), email: c.email.trim(), gstin: c.gstin.trim().toUpperCase(), state: c.state },
      items: named.map((i) => ({ name: i.name.trim(), qty: Number(i.qty), unit: i.unit.trim() || "nos", rate: Number(i.rate), gst: Number(i.gst) })),
      gst_mode: f.gst_mode || null, due_in_days: Number(f.due_in_days) || 0, paid_already: f.paid_already } });
    toast(`Invoice ${inv.number} made${inv.email?.sent ? " and emailed" : ""}`);
    onSaved(inv);
  };
  return (
    <div className="panel active">
      <div className="row between">
        <h2>New <em>invoice</em></h2>
        <button className="btn sm ghost" onClick={onClose}>Cancel</button>
      </div>
      <div className="panel flat">
        <span className="label">Billing entry — type or say the sale</span>
        <textarea className="textarea" style={{ minHeight: 70 }} maxLength={2000} value={entry} onChange={(e) => setEntry(e.target.value)} aria-label="The sale in a few words"
          placeholder="Sold 40 boxes to Mehta Traders, ₹850 each, 18% GST, due in 30 days" />
        <div className="rb-voice"><VoiceNote purpose="quote" onText={(x) => setEntry((b) => (b.trim() ? `${b.trim()} ${x}` : x))} /></div>
        <div className="row">
          <Busy className="btn ai" disabled={entry.trim().length < 5} onError={setErr} run={async () => {
            const d = await api("/invoices/draft", { method: "POST", body: { text: entry.trim() } });
            setF({ customer: { ...blankInvoice().customer, ...Object.fromEntries(Object.entries(d.customer || {}).filter(([, v]) => v)) },
              items: d.items.map((i) => ({ name: i.name, qty: String(i.qty), unit: i.unit || "nos", rate: i.rate ? String(i.rate) : "", gst: GST_RATES.includes(i.gst) ? i.gst : 18 })),
              gst_mode: "", due_in_days: d.due_in_days, paid_already: d.paid_already });
            toast(d.missing_rates ? `Drafted. ${d.missing_rates} ${d.missing_rates === 1 ? "line needs" : "lines need"} a rate` : "Drafted. Check it, then make the invoice");
          }}>Fill the invoice</Busy>
          <span className="hint">Uses 1 AI token. Only the rates you say are used — a missing one stays empty for you to add.</span>
        </div>
      </div>

      <span className="label">Customer</span>
      <div className="grid2">
        <Field label="Name"><input className="input" maxLength={120} value={f.customer.name} onChange={setC("name")} placeholder="Mehta Traders" /></Field>
        <Field label="WhatsApp number"><input className="input" inputMode="tel" maxLength={24} value={f.customer.phone} onChange={setC("phone")} placeholder="98200 12345" /></Field>
        <Field label="Email (optional)" hint={me.features?.auto_reminders ? "Payment follow-ups and the emailed invoice use it." : undefined}>
          <input className="input" type="email" maxLength={254} value={f.customer.email} onChange={setC("email")} placeholder="accounts@customer.in" /></Field>
        <Field label="Customer GSTIN (optional)"><input className="input mono" maxLength={15} value={f.customer.gstin} onChange={setC("gstin")} placeholder="27ABCDE1234F1Z5" /></Field>
        <Field label="Customer's state" hint={opts?.my_state ? `You're in ${opts.my_state}. Same state: CGST + SGST. Another state: IGST.` : "Same state as you: CGST + SGST. Another state: IGST."}>
          <select className="select" value={f.customer.state} onChange={setC("state")}>
            <option value="">Not sure / same as mine</option>
            {states.map((x) => <option key={x} value={x}>{x}</option>)}
          </select>
        </Field>
      </div>
      {opts && !opts.identity && (
        <p className="small" style={{ color: "var(--amber)" }}>Your GST registration isn't filled in yet, so invoices show no GST. <Link to="/knowledge">Add it in the Knowledge Hub</Link>.</p>
      )}

      <span className="label">Items</span>
      <div className="rb-lines">
        <div className="rb-line head" aria-hidden="true"><span>Item</span><span>Qty</span><span>Unit</span><span>Rate ₹</span><span>GST</span><span style={{ textAlign: "right" }}>Amount</span><span /></div>
        {f.items.map((it, i) => (
          <div key={i} className={`rb-line ${it.name.trim() && !(Number(it.rate) > 0) ? "zero" : ""}`}>
            <label className="rb-cell"><span>Item</span><input className="input" maxLength={160} value={it.name} onChange={setI(i, "name")} aria-label={`Line ${i + 1}: item`} /></label>
            <label className="rb-cell"><span>Qty</span><input className="input" type="number" min="0" step="any" inputMode="decimal" value={it.qty} onChange={setI(i, "qty")} aria-label={`Line ${i + 1}: quantity`} /></label>
            <label className="rb-cell"><span>Unit</span><input className="input" maxLength={20} value={it.unit} onChange={setI(i, "unit")} aria-label={`Line ${i + 1}: unit`} /></label>
            <label className="rb-cell"><span>Rate ₹</span><input className="input rate" type="number" min="0" step="any" inputMode="decimal" value={it.rate} onChange={setI(i, "rate")} aria-label={`Line ${i + 1}: rate in rupees`} placeholder={it.name.trim() ? "Add rate" : ""} /></label>
            <label className="rb-cell"><span>GST</span>
              <select className="select" value={it.gst} disabled={f.gst_mode === "none"} onChange={(e) => setF({ ...f, items: f.items.map((x, j) => (j === i ? { ...x, gst: Number(e.target.value) } : x)) })} aria-label={`Line ${i + 1}: GST rate`}>
                {GST_RATES.map((g) => <option key={g} value={g}>{g}%</option>)}
              </select>
            </label>
            <span className="amt money small">{rs(t.amounts[i])}</span>
            <button className="rb-x" type="button" aria-label={`Remove line ${i + 1}`} disabled={f.items.length === 1} onClick={() => setF({ ...f, items: f.items.filter((_, j) => j !== i) })}>×</button>
          </div>
        ))}
      </div>
      <div className="row between">
        <button className="btn sm" type="button" disabled={f.items.length >= 60} onClick={() => setF({ ...f, items: [...f.items, blankItem()] })}>Add a line</button>
        {zero > 0 && <span className="small" style={{ color: "var(--amber)" }}>{zero} {zero === 1 ? "line needs" : "lines need"} a rate</span>}
      </div>
      <div className="grid2" style={{ alignItems: "start" }}>
        <div className="stack" style={{ gap: 10 }}>
          <Field label="GST">
            <select className="select" value={f.gst_mode} onChange={(e) => setF({ ...f, gst_mode: e.target.value })}>
              <option value="">Work it out from the customer's state</option>
              {MODES.map(([k, l]) => <option key={k} value={k}>{l}</option>)}
            </select>
          </Field>
          <Switch checked={f.paid_already} onChange={(v) => setF({ ...f, paid_already: v })} label="Paid already" />
          {!f.paid_already && <Field label="Payment due in (days)" hint={me.features?.money_owed ? "Added to Money owed so follow-ups can chase it." : undefined}>
            <Num min="0" max="365" step="1" value={f.due_in_days} onChange={(v) => setF({ ...f, due_in_days: v })} /></Field>}
        </div>
        <div className="rb-totals" aria-live="polite">
          <div><span className="muted">Subtotal</span><span className="money">{rs(t.subtotal)}</span></div>
          {mode === "intra" && <><div><span className="muted">CGST</span><span className="money">{rs(t.cgst)}</span></div><div><span className="muted">SGST</span><span className="money">{rs(t.sgst)}</span></div></>}
          {mode === "inter" && <div><span className="muted">IGST</span><span className="money">{rs(t.igst)}</span></div>}
          <div className="grand"><span>Total</span><span className="money">{rs(t.total)}</span></div>
          {!f.gst_mode && opts?.identity && <span className="hint">Shown as same-state; the hub applies IGST if the customer's state differs from yours.</span>}
        </div>
      </div>
      <FormError msg={err} />
      <div className="row">
        <Busy className="btn primary" onError={setErr} run={save}>Make invoice</Busy>
        <button className="btn ghost" onClick={onClose}>Cancel</button>
      </div>
    </div>
  );
}

/** Payments through the owner's own payment gateway become invoices on their own. Shown to owners who have it. */
function GatewayLine() {
  const { me } = useHub();
  const owner = canAct(me, "owner");
  const [g] = useLoad(owner && me.features?.gateway_invoicing ? "/gateway" : "/invoices/settings");
  if (!me.features?.gateway_invoicing || !owner || !g?.connection) return null;
  const c = g.connection;
  return (
    <div className="notice small inv-gw">
      {c.connected
        ? <span>Your payment gateway is connected: <b>{g.invoices_30d}</b> {g.invoices_30d === 1 ? "payment" : "payments"} became invoices and <b>{g.matched_30d}</b> paid an amount you were owed, in the last 30 days{g.credit_notes_30d ? `; ${g.credit_notes_30d} refunds became credit notes` : ""}. Each invoice also goes to your Razorpay account: with a pay link while it's unpaid, as a record once it's paid.</span>
        : <span>Connect your payment gateway and every payment becomes a GST invoice on its own; a refund becomes a credit note.</span>}
      <Link className="btn sm" to="/connections">{c.connected ? "Connection" : "Connect payment gateway"}</Link>
    </div>
  );
}

function CreditNoteRow({ cn }) {
  return (
    <div className="panel" style={{ padding: "16px 18px", gap: 10 }}>
      <div className="row between" style={{ alignItems: "flex-start" }}>
        <div className="stack rb-wrap" style={{ gap: 2 }}>
          <span><b>{cn.number}</b> · {cn.customer?.name}</span>
          <span className="small muted">{cn.against ? `Against ${cn.against} · ` : ""}{cn.source === "gateway" ? "a refund through your payment gateway · " : ""}{day(cn.created_at)}</span>
        </div>
        <span className="money" style={{ fontFamily: "var(--head)", fontSize: 20 }}>−{rs(cn.totals?.total)}</span>
      </div>
      <div className="row" style={{ gap: 8 }}>
        <a className="btn sm" href={`/api/invoices/${cn.id}/pdf`} target="_blank" rel="noreferrer">PDF</a>
        <Copy text={cn.share_text} label="Copy text" className="btn sm ghost" />
      </div>
    </div>
  );
}

function InvoiceModal({ q, onClose, onDone }) {
  const { me, toast } = useHub();
  const [days, setDays] = useState(7);
  const [err, setErr] = useState("");
  return (
    <Modal onClose={onClose}>
      <span className="kicker">Invoice from {q.number}</span>
      <h2>Make the <em>invoice</em>.</h2>
      <p className="sub">{q.customer?.name} · <span className="money">{rs(q.totals?.total)}</span>. The quotation is marked accepted{me.features?.money_owed ? ", and the invoice is added to Money owed so payment reminders can follow it" : ""}.</p>
      <Field label="Payment due in (days)"><Num min="0" max="365" step="1" value={days} onChange={setDays} /></Field>
      <FormError msg={err} />
      <div className="row">
        <Busy className="btn primary" onError={setErr} run={async () => {
          const inv = await api(`/quotes/${q.id}/invoice`, { method: "POST", body: { due_in_days: Number(days) || 0 } });
          toast(`Invoice ${inv.number} made`);
          onDone(inv);
        }}>Make invoice</Busy>
        <button className="btn ghost" onClick={onClose}>Cancel</button>
      </div>
    </Modal>
  );
}

const sendLink = (q) => q.whatsapp_link || waLink(q.share_text);

function QuoteCard({ q, onEdit, onInvoice, reload, showInvoices, onInvoiced }) {
  const { toast } = useHub();
  const [label, kind] = Q_STATUS[q.status] || [q.status, ""];
  const setStatus = async (status, msg) => {
    const x = await api(`/quotes/${q.id}/status`, { method: "POST", body: { status } });
    toast(status === "accepted" && x.invoice_id && !q.invoice_id ? `${q.number} accepted — the invoice is made` : msg);
    reload();
    if (status === "accepted" && x.invoice_id) onInvoiced?.();
  };
  const open = q.status === "draft" || q.status === "sent";
  const unpriced = (q.items || []).filter((i) => !Number(i.rate)).length;
  return (
    <div className="panel" style={{ padding: "16px 18px", gap: 10 }}>
      <div className="row between" style={{ alignItems: "flex-start" }}>
        <div className="stack rb-wrap" style={{ gap: 2 }}>
          <span><b>{q.number}</b> · {q.customer?.name}</span>
          <span className="small muted">
            {(q.items || []).length} {(q.items || []).length === 1 ? "line" : "lines"} · made {day(q.created_at)}
            {q.sent_on ? ` · sent ${day(q.sent_on)}` : ""}{q.status === "sent" && q.followups ? ` · follow-up ${q.followups} of 3` : ""}
            {!q.customer?.phone ? " · no number" : ""}
          </span>
          {q.source === "inquiry" && open && (
            <span className="small" style={{ color: unpriced ? "var(--amber)" : "var(--teal)" }}>
              Drafted from their inquiry. {unpriced ? `${unpriced} ${unpriced === 1 ? "line needs" : "lines need"} a price — press Edit before sending.` : "Check the prices, then send it."}
            </span>
          )}
        </div>
        <div className="row" style={{ gap: 10 }}>
          <span className="money" style={{ fontFamily: "var(--head)", fontSize: 20 }}>{rs(q.totals?.total)}</span>
          <Pill kind={kind}>{label}</Pill>
        </div>
      </div>
      <div className="row" style={{ gap: 8 }}>
        {open && (
          <a className="btn sm primary" href={sendLink(q)} target="_blank" rel="noreferrer"
            onClick={() => { if (q.status === "draft") setStatus("sent", "Marked as sent").catch(() => {}); }}>
            {q.status === "sent" ? "Send again on WhatsApp" : "Send on WhatsApp"}
          </a>
        )}
        {q.status === "draft" && <Busy className="btn sm" run={() => setStatus("sent", "Marked as sent")}>Mark sent</Busy>}
        {open && <Busy className="btn sm" run={() => setStatus("accepted", `${q.number} accepted`)}>Accepted</Busy>}
        {open && <Busy className="btn sm ghost" run={() => setStatus("lost", `${q.number} marked lost`)}>Lost</Busy>}
        {!q.invoice_id && q.status !== "lost" && <button className="btn sm" onClick={() => onInvoice(q)}>Make invoice</button>}
        {q.invoice_id && <button className="btn sm ghost" onClick={showInvoices}>See the invoice</button>}
        <a className="btn sm ghost" href={q.pdf_url} target="_blank" rel="noreferrer">PDF</a>
        <Copy text={q.pdf_url} label="Copy PDF link" className="btn sm ghost" />
        <a className="btn sm ghost" href={`/api/quotes/${q.id}/print`} target="_blank" rel="noreferrer">Print</a>
        <Copy text={q.share_text} label="Copy text" className="btn sm ghost" />
        {open && <button className="btn sm ghost" onClick={() => onEdit(q)}>Edit</button>}
        {q.status === "draft" && (
          <Busy className="btn sm ghost" run={async () => {
            if (!window.confirm(`Delete draft ${q.number}?`)) return;
            await api(`/quotes/${q.id}`, { method: "DELETE" });
            toast("Deleted");
            reload();
          }}>Delete</Busy>
        )}
      </div>
    </div>
  );
}

const RZP_KIND = { paid: "on", issued: "money", partially_paid: "money", cancelled: "off", expired: "off", draft: "" };

/** Its Razorpay invoice: issued with a pay link while unpaid, a record when it was already paid, cancelled when paid another way. */
function RazorpayLine({ inv, reload }) {
  const { toast } = useHub();
  const r = inv.rzp;
  if (!r) return null;
  const what = r.mode === "record" ? "Razorpay record (already paid, not sent to the customer)" : r.mode === "razorpay" ? "Paid through a Razorpay invoice" : "Razorpay invoice";
  return (
    <div className="row inv-rzp" style={{ gap: 8 }}>
      <span className="small muted">{what}{r.invoice_id ? ` · ${r.invoice_id}` : ""}</span>
      <Pill kind={r.state === "failed" ? "warn" : RZP_KIND[r.status] ?? ""}>{r.label}</Pill>
      {r.pay_url && <Copy text={r.pay_url} label="Copy pay link" className="btn sm ghost" />}
      {r.state === "failed" && (
        <>
          <span className="small" style={{ color: "var(--amber)" }}>{r.error}</span>
          <Busy className="btn sm" run={async () => {
            const x = await api(`/invoices/${inv.id}/razorpay`, { method: "POST" });
            toast(x.rzp?.state === "failed" ? x.rzp.error || "Still not added" : "Added to Razorpay", x.rzp?.state === "failed" ? "err" : "ok");
            reload?.();
          }}>Try again</Busy>
        </>
      )}
    </div>
  );
}

/** The business's Razorpay invoices as last read from Razorpay — the hub's own and any made in the Razorpay dashboard. */
function RazorpayInvoices() {
  const { toast } = useHub();
  const [d, reload] = useLoad("/gateway/razorpay-invoices");
  if (!d) return <Loading />;
  if (!d.connected) {
    return <Empty>Add your Razorpay key id and key secret in <Link to="/connections">Connections → Payment gateway</Link>, and every invoice gets its Razorpay invoice with a pay link.</Empty>;
  }
  return (
    <div className="stack">
      <div className="row between">
        <span className="small muted">{d.last_at ? `Read from Razorpay ${day(d.last_at)}` : "Not read from Razorpay yet"} · every {d.every_minutes} minutes{d.error ? ` · ${d.error}` : ""}</span>
        <Busy className="btn sm" run={async () => { await api("/gateway/razorpay-invoices/sync", { method: "POST" }); toast("Read from Razorpay"); reload(); }}>Read now</Busy>
      </div>
      {!d.items.length ? <Empty>No Razorpay invoices yet.</Empty> : (
        <table className="t cards">
          <thead><tr><th>Razorpay invoice</th><th>Customer</th><th>Amount</th><th>Status</th><th>In the hub</th></tr></thead>
          <tbody>{d.items.map((x) => (
            <tr key={x.id}>
              <td><b>{x.receipt || x.id}</b><div className="small muted">{x.id}{x.created_at ? ` · ${day(x.created_at)}` : ""}</div></td>
              <td data-l="Customer">{x.customer?.name || "—"}</td>
              <td data-l="Amount" className="money">{rs(x.amount)}{x.amount_paid && x.amount_paid < x.amount ? <div className="small muted">{rs(x.amount_paid)} paid</div> : null}</td>
              <td data-l="Status"><Pill kind={RZP_KIND[x.status] ?? ""}>{x.label}</Pill></td>
              <td data-l="In the hub" className="small">{x.hub_number ? `Invoice ${x.hub_number}` : <span className="muted">Made in Razorpay</span>}
                {x.short_url && <div><a href={x.short_url} target="_blank" rel="noreferrer">Pay link</a></div>}</td>
            </tr>
          ))}</tbody>
        </table>
      )}
    </div>
  );
}

function InvoiceRow({ inv, from, reload }) {
  const { me, toast } = useHub();
  const overdue = inv.status === "due" && inv.due_date && inv.due_date < todayIso();
  const emailed = inv.email?.sent;
  return (
    <div className="panel" style={{ padding: "16px 18px", gap: 10 }}>
      <div className="row between" style={{ alignItems: "flex-start" }}>
        <div className="stack rb-wrap" style={{ gap: 2 }}>
          <span><b>{inv.number}</b> · {inv.customer?.name}</span>
          <span className="small muted">{from ? `From ${from} · ` : ""}{inv.source === "gateway" ? "a payment through your payment gateway · " : ""}{inv.due_date ? `due ${day(inv.due_date)}` : ""}{inv.status === "paid" && inv.paid_at ? `${inv.due_date ? " · " : ""}paid ${day(inv.paid_at)}` : ""}{inv.part_paid && inv.status !== "paid" ? ` · ${rs(inv.part_paid)} received so far` : ""}{inv.refunded ? ` · ${rs(inv.refunded)} refunded` : ""}{emailed ? ` · emailed ${day(inv.email.at)}` : ""}</span>
          {inv.gst_note && <span className="small" style={{ color: "var(--amber)" }}>{inv.gst_note} <Link to="/knowledge">Fill it in</Link></span>}
        </div>
        <div className="row" style={{ gap: 10 }}>
          <span className="money" style={{ fontFamily: "var(--head)", fontSize: 20 }}>{rs(inv.totals?.total)}</span>
          {inv.status === "paid" ? <Pill kind="on">Paid</Pill> : <Pill kind="money">{overdue ? "Overdue" : inv.part_paid ? "Part paid" : "Unpaid"}</Pill>}
        </div>
      </div>
      <div className="row" style={{ gap: 8 }}>
        {inv.status !== "paid" && <a className="btn sm primary" href={sendLink(inv)} target="_blank" rel="noreferrer">Send on WhatsApp</a>}
        <a className="btn sm" href={inv.pdf_url} target="_blank" rel="noreferrer">PDF</a>
        <Copy text={inv.pdf_url} label="Copy PDF link" className="btn sm ghost" />
        <a className="btn sm ghost" href={`/api/quotes/${inv.id}/print`} target="_blank" rel="noreferrer">Print</a>
        <Copy text={inv.share_text} label="Copy text" className="btn sm ghost" />
        {inv.customer?.email && !emailed && (
          <Busy className="btn sm ghost" run={async () => {
            await api(`/invoices/${inv.id}/email`, { method: "POST" });
            toast(`Invoice ${inv.number} emailed to ${inv.customer.email}`);
            reload?.();
          }}>Email it</Busy>
        )}
        {me.features?.money_owed && inv.due_id && <Link className="btn sm ghost" to="/money">{inv.status === "paid" ? "See in Money owed" : "Mark paid in Money owed"}</Link>}
      </div>
      <RazorpayLine inv={inv} reload={reload} />
    </div>
  );
}

/** The owner's own past invoices, read once by the AI, so drafts are written the way this business writes them. */
function StylePanel() {
  const { toast, refresh } = useHub();
  const [d, setD] = useState(null);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");
  useEffect(() => { api("/quotes/style").then(setD).catch(() => setD({ samples: [], max: 3, max_mb: 8 })); }, []);
  if (!d) return null;
  const st = d.style || {};
  const send = async (file) => {
    setBusy(true); setErr("");
    try {
      const form = new FormData();
      form.append("file", file, file.name);
      const r = await upload("/quotes/style", form);
      setD(r); refresh();
      toast(r.filled?.length ? `Learned. Your ${r.filled.join(" and ")} came from it too.` : "Learned how you write your invoices");
    } catch (e) { if (!e.handled) setErr(e.message); } finally { setBusy(false); }
  };
  const pick = (
    <label className={`btn sm ${d.samples.length ? "ghost" : "primary"} ai`} style={{ cursor: busy ? "wait" : "pointer" }}>
      {busy ? "Reading your invoice…" : d.samples.length ? "Add another invoice" : "Upload one of your invoices"}
      <input type="file" hidden accept="application/pdf,.pdf,image/jpeg,image/png,image/webp" disabled={busy}
        onChange={(e) => { const f = e.target.files?.[0]; e.target.value = ""; if (f) send(f); }} />
    </label>
  );
  return (
    <div className={`panel inv-style ${d.samples.length ? "" : "active"}`}>
      <div className="row between">
        <div className="stack" style={{ gap: 4 }}>
          <span className="label">{d.samples.length ? "Drafts follow your own invoice style" : "Teach the hub how you write invoices"}</span>
          <span className="small muted">{d.samples.length
            ? `Learned from ${d.samples.length === 1 ? "one of your invoices" : `${d.samples.length} of your invoices`}. Every AI quotation names items, units and GST your way, and uses your past rates for the same items.`
            : "Upload a quotation or invoice you've sent before (PDF or a photo). The AI reads it once and drafts new ones the same way: your item names, units, GST and terms."}</span>
        </div>
        {d.samples.length < d.max && pick}
      </div>
      <FormError msg={err} />
      {d.samples.length > 0 && (
        <details className="inv-style-more">
          <summary>What the hub learned</summary>
          <ul className="inv-style-list small">
            {st.summary && <li><b>Layout:</b> {st.summary}</li>}
            {st.item_naming && <li><b>Item names:</b> {st.item_naming}</li>}
            {st.units?.length > 0 && <li><b>Units:</b> {st.units.join(", ")}</li>}
            {st.gst_rates?.length > 0 && <li><b>GST:</b> {st.gst_rates.map((g) => `${g}%`).join(", ")}{st.gst_mode === "inter" ? " (IGST)" : st.gst_mode === "intra" ? " (CGST + SGST)" : ""}</li>}
            {st.numbering && <li><b>Numbering:</b> {st.numbering}</li>}
            {st.items?.length > 0 && <li><b>Past rates:</b> {st.items.length} item{st.items.length === 1 ? "" : "s"}, used when you don't say a rate</li>}
            {st.terms && <li><b>Terms:</b> {st.terms}</li>}
          </ul>
          <div className="stack" style={{ gap: 6 }}>
            {d.samples.map((x) => (
              <div key={x.id} className="row between inv-style-file">
                <span className="small">{x.name} <span className="muted">· {x.kind === "pdf" ? `PDF, ${x.pages} page${x.pages === 1 ? "" : "s"}` : "photo"} · {day(x.at)}</span></span>
                <Busy className="btn sm ghost" run={async () => { setD(await api(`/quotes/style/${x.id}`, { method: "DELETE" })); toast("Removed"); }}>Remove</Busy>
              </div>
            ))}
          </div>
        </details>
      )}
      <span className="small muted">Uses 1 AI token per invoice. Only what the AI learned is kept, never the file. Up to {d.max} invoices, {d.max_mb} MB each.</span>
    </div>
  );
}

export default function Quotes() {
  const { me } = useHub();
  const [tab, setTab] = useState("quote");
  const [quotes, reloadQ] = useLoad("/quotes?kind=quote");
  const [invoices, reloadI] = useLoad("/quotes?kind=invoice");
  const [credits] = useLoad("/credit-notes");
  const [editing, setEditing] = useState(null);   // null | "new" | "invoice" | a quotation
  const [settings, setSettings] = useState(false);
  const [invoiceFor, setInvoiceFor] = useState(null);
  const [params, setParams] = useSearchParams();
  const [prefill, setPrefill] = useState(null);
  useEffect(() => {
    const id = params.get("inquiry");
    if (!id) return;
    api("/leads").then((list) => {
      const l = (list || []).find((x) => x.id === id);
      if (l) {
        setPrefill({ lead_id: l.id, name: l.name || "", phone: l.phone ? localPhone(l.phone) : "", brief: (l.message || "").slice(0, 6000) });
        setEditing("new");
      }
    }).catch(() => {}).finally(() => setParams({}, { replace: true }));
  }, []);  // eslint-disable-line react-hooks/exhaustive-deps
  const numberOf = Object.fromEntries((quotes || []).map((q) => [q.id, q.number]));
  const waiting = (quotes || []).filter((q) => q.status === "sent").length;
  const title = waiting ? <>{waiting} {waiting === 1 ? "quotation is" : "quotations are"} waiting for a <em>yes</em>.</> : <>Quotations and invoices in <em>minutes</em>.</>;
  const top = () => window.scrollTo({ top: 0, behavior: "smooth" });
  return (
    <Sheet kicker="Sales · Invoicing" id="SA-03" pillar="Sales" money title={title}
      sub="Speak or type what the customer wants; the hub writes the GST quotation. Send it on WhatsApp, then turn it into an invoice the same day they say yes."
      actions={!editing && <>
        <button className="btn" onClick={() => setSettings(!settings)}>Business details</button>
        <button className="btn" onClick={() => { setEditing("invoice"); setSettings(false); }}>New invoice</button>
        <button className="btn primary" onClick={() => { setEditing("new"); setSettings(false); }}>New quotation</button>
      </>}>
      <GatewayLine />
      {me.features?.quote_followup && <p className="small muted">Quote follow-ups on day 2, 5 and 10 after you send a quotation wait in your <Link to="/approvals">Send list</Link>.</p>}
      {settings && !editing && <Settings onClose={() => setSettings(false)} />}
      {!editing && <StylePanel />}
      {editing === "invoice" && <InvoiceEditor onClose={() => setEditing(null)} onSaved={() => { setEditing(null); setTab("invoice"); reloadI(); top(); }} />}
      {editing && editing !== "invoice" && (
        <Editor key={editing === "new" ? `new-${prefill?.lead_id || ""}` : editing.id} quote={editing === "new" ? null : editing} prefill={editing === "new" ? prefill : null}
          onClose={() => { setEditing(null); setPrefill(null); }}
          onSaved={() => { setEditing(null); setPrefill(null); setTab("quote"); reloadQ(); top(); }} />
      )}
      {!editing && (
        <>
          <Tabs tabs={[["quote", `Quotations${quotes ? ` · ${quotes.length}` : ""}`], ["invoice", `Invoices${invoices ? ` · ${invoices.length}` : ""}`],
            ...(credits?.length ? [["credit", `Credit notes · ${credits.length}`]] : []),
            ...(me.features?.gateway_invoicing && canAct(me, "manager") ? [["razorpay", "Razorpay invoices"]] : [])]} value={tab} onChange={setTab} />
          {tab === "quote" && (!quotes ? <Loading /> : !quotes.length ? (
            <div className="empty">
              <h2>No quotations yet.</h2>
              <p className="muted">Start with the next customer who asks for a price. Type a few lines or record a voice note — the hub fills in the lines and the GST.</p>
              <button className="btn primary" onClick={() => setEditing("new")}>Write my first quotation</button>
            </div>
          ) : (
            <div className="stack">
              {quotes.map((q) => (
                <QuoteCard key={q.id} q={q} reload={reloadQ} onEdit={(x) => { setEditing(x); top(); }} onInvoice={setInvoiceFor} showInvoices={() => setTab("invoice")} onInvoiced={reloadI} />
              ))}
            </div>
          ))}
          {tab === "invoice" && (!invoices ? <Loading /> : !invoices.length ? (
            <Empty>No invoices yet. When a customer says yes, press Make invoice on their quotation — or press New invoice for a sale without one.</Empty>
          ) : (
            <div className="stack">{invoices.map((inv) => <InvoiceRow key={inv.id} inv={inv} from={numberOf[inv.quote_id]} reload={reloadI} />)}</div>
          ))}
          {tab === "credit" && <div className="stack">{(credits || []).map((cn) => <CreditNoteRow key={cn.id} cn={cn} />)}</div>}
          {tab === "razorpay" && <RazorpayInvoices />}
        </>
      )}
      {invoiceFor && (
        <InvoiceModal q={invoiceFor} onClose={() => setInvoiceFor(null)} onDone={() => { setInvoiceFor(null); reloadQ(); reloadI(); setTab("invoice"); }} />
      )}
    </Sheet>
  );
}
