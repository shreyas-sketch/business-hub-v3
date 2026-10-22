import React, { useState } from "react";
import { api, rs, upload } from "../api.js";
import { Busy, FormError, useHub, useLoad } from "../ui.jsx";
import "./moneyinsights.css";

/* "Bring in your sales": a Tally or Excel sales sheet → the hub's sales history (used by Customer risk, Getting paid and
   the GST set-aside). Shared by those pages. */

const MAX_MB = 10;
const fmt = (iso) => (iso ? new Date(`${iso}T00:00:00`).toLocaleDateString("en-IN", { day: "numeric", month: "short", year: "numeric" }) : "");
const FIELD = { date: "Date", customer: "Customer", amount: "Amount", taxable: "Value before tax", gst: "GST", invoice_no: "Invoice no.",
  due_date: "Due date", paid_on: "Paid on", phone: "Phone", voucher_type: "Voucher type" };

const NEEDED = [
  ["Date", "The day of the sale. 31-03-2026, 31/03/26 and 31-Mar-26 all work."],
  ["Customer", "Or Party, Party name, Particulars, Buyer."],
  ["Amount", "The bill total with GST. In Tally this is Gross Total."],
];
const OPTIONAL = [
  ["GST", "One tax column, or CGST, SGST and IGST."],
  ["Invoice no.", "Or Voucher no., Bill no. Stops a sale counting twice."],
  ["Due date", "When the customer should pay."],
  ["Paid on", "The day the money came in."],
  ["Phone or mobile", "For WhatsApp check-ins."],
];

function Result({ res }) {
  const cols = Object.entries(res.columns_found || {})
    .filter(([k]) => FIELD[k])
    .map(([k, v]) => `${FIELD[k]} ← ${Array.isArray(v) ? v.join(" + ") : v}`);
  return (
    <div className="mi-result" role="status">
      {res.lines.map((l) => <p key={l}>{l}</p>)}
      {res.rows > 0 && <p className="small muted">{fmt(res.from)} to {fmt(res.to)} · {res.customers} {res.customers === 1 ? "customer" : "customers"}{res.sheet ? ` · sheet “${res.sheet}”` : ""}</p>}
      {res.skipped_examples?.length > 0 && <p className="small muted">Rows to check in your file: {res.skipped_examples.join(", ")}{res.skipped_unreadable > res.skipped_examples.length ? " and more" : ""}.</p>}
      {res.dates_read === "month first" && <p className="small muted">Your dates are written month first (like 03/15/2026), so we read them that way.</p>}
      {cols.length > 0 && <p className="small muted">Columns we read: {cols.join(" · ")}</p>}
    </div>
  );
}

export default function SalesUpload({ note, onChange }) {
  const { toast } = useHub();
  const [list, reload] = useLoad("/sales-upload");
  const [busy, setBusy] = useState(false);
  const [res, setRes] = useState(null);
  const [err, setErr] = useState("");

  const pick = async (e) => {
    const file = e.target.files?.[0];
    e.target.value = "";
    if (!file) return;
    setErr(""); setRes(null);
    if (/\.xls$/i.test(file.name)) { setErr("This is an old Excel file (.xls). Open it in Excel, save it as .xlsx or CSV, and upload that."); return; }
    if (file.size > MAX_MB * 1024 * 1024) { setErr(`Keep the file under ${MAX_MB} MB. Upload one year at a time.`); return; }
    setBusy(true);
    try {
      const form = new FormData();
      form.append("file", file);
      const r = await upload("/sales-upload", form);
      setRes(r);
      toast(r.rows ? `${r.rows.toLocaleString("en-IN")} sales added` : "No new sales in that file");
      reload();
      onChange?.();
    } catch (x) {
      if (!x.handled) setErr(x.message);
    } finally { setBusy(false); }
  };

  const batches = list?.batches || [];
  return (
    <div className="panel active">
      <div className="row between">
        <span className="label">Bring in your sales</span>
        <label className={`btn sm primary mi-file ${busy ? "off" : ""}`}>
          {busy ? "Reading…" : "Upload Excel or CSV"}
          <input type="file" accept=".csv,.xlsx" onChange={pick} disabled={busy} aria-label="Upload a sales sheet (Excel or CSV)" />
        </label>
      </div>
      <p className="small muted">Most owners keep sales in Tally or Excel. Upload that sheet and these pages use it. From Tally: open the Sales Register, pick the period, and export it to Excel. Upload the file as it is — the title lines on top are fine. Item-wise sheets work too: lines with the same invoice number, customer and date are added up into one bill.</p>
      {note && <p className="small">{note}</p>}
      <div className="grid2">
        <div className="stack" style={{ gap: 8, alignContent: "start" }}>
          <span className="label">Needed</span>
          <ul className="mi-cols">{NEEDED.map(([l, w]) => <li key={l}><b>{l}</b><span className="muted">{w}</span></li>)}</ul>
        </div>
        <div className="stack" style={{ gap: 8, alignContent: "start" }}>
          <span className="label">Good to have</span>
          <ul className="mi-cols">{OPTIONAL.map(([l, w]) => <li key={l}><b>{l}</b><span className="muted">{w}</span></li>)}</ul>
        </div>
      </div>
      <p className="small muted">CSV or Excel (.xlsx), up to {MAX_MB} MB and 20,000 rows. Rows we can't read are counted and listed, never guessed. Sales already in the hub, or uploaded before, are not counted twice.</p>
      <FormError msg={err} />
      {res && <Result res={res} />}

      {batches.length > 0 && (
        <div className="stack" style={{ gap: 6 }}>
          <span className="label">Your uploads</span>
          <div className="mi-list">
            {batches.map((b) => (
              <div key={b.id}>
                <div className="who">
                  <b>{b.filename || "Sales sheet"}</b>
                  <span className="small muted">{b.rows.toLocaleString("en-IN")} {b.rows === 1 ? "sale" : "sales"} · {fmt(b.from)} to {fmt(b.to)}{b.created_at ? ` · uploaded ${new Date(b.created_at).toLocaleDateString("en-IN", { day: "numeric", month: "short" })}` : ""}</span>
                  {!b.has_paid_on && <span className="small muted">No “Paid on” column in this one.</span>}
                </div>
                <span className="amt">{rs(Math.round(b.total))}</span>
                <div className="acts">
                  <Busy className="btn sm ghost" run={async () => {
                    if (!window.confirm(`Remove “${b.filename || "this upload"}”? Its ${b.rows} sales come out of the hub. You can upload it again later.`)) return;
                    await api(`/sales-upload/${b.id}`, { method: "DELETE" });
                    toast("Upload removed");
                    setRes(null);
                    reload();
                    onChange?.();
                  }}>Remove this upload</Busy>
                </div>
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}
