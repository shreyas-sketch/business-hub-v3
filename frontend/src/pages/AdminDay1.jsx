import React, { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { ago, api, localPhone, rs } from "../api.js";
import { Busy, Copy, Field, FormError, Sheet, Switch, useHub, useLoad } from "../ui.jsx";
import { Pill, Tabs } from "../v2.jsx";
import { Rich } from "./Day1.jsx";
import "./day1.css";

/* Admin → Day-1 reports: the ₹99 webinar's no-login report. Webinars (numbers, people, send, export), the library the
   findings and "imagine" lines come from, a preview, and the queue's logs. */

const ANSWERS = ["yes", "partly", "no"];
const QUESTIONS = ["focus", "margins", "sales", "payments", "offers", "reply", "followup", "content", "process", "numbers"];
const CHECKS = [["https", "https"], ["mobile", "Works on phones"], ["title", "Title for Google"], ["contact", "Phone/email"],
  ["whatsapp", "WhatsApp button"], ["form", "Enquiry form"], ["proof", "Reviews"], ["light", "Opens fast"]];
const SAMPLE = {
  first_name: "Riya", city: "Pune",
  site: { host: "example-interiors.in", read: true, checks: { https: true, mobile: true, title: true, contact: true, whatsapp: false, form: false, proof: false, light: true } },
  answers: { focus: "partly", margins: "yes", sales: "partly", payments: "no", offers: "no", reply: "partly", followup: "no", content: "no", process: "partly", numbers: "no" },
  numbers: { revenue_band: "50l_1cr", team_size: "10_30", enquiries: 40, close_rate: "20", aov: 85000, reviews_12m: "none" },
};
const STATUS = { queued: "Waiting", building: "Building", done: "Ready", failed: "Failed" };

function People({ code, onChange }) {
  const { toast } = useHub();
  const [filter, setFilter] = useState("");
  const [rows, reload] = useLoad(`/admin/day1/cohorts/${code}/runs?status=${filter}`, [filter]);
  const act = async (path, msg) => { await api(path, { method: "POST" }); toast(msg); reload(); onChange(); };
  return (
    <div className="stack">
      <Tabs tabs={[["", "Everyone"], ["pending", "Not sent yet"], ["failed", "Failed"], ["queued", "Waiting"]]} value={filter} onChange={setFilter} />
      {!rows ? <p className="muted">Loading…</p> : rows.length === 0 ? <p className="muted small">Nobody here.</p> : (
        <table className="t cards">
          <thead><tr><th>Person</th><th>Report</th><th>WhatsApp</th><th>Saved</th><th /></tr></thead>
          <tbody>{rows.map((r) => (
            <tr key={r.id}>
              <td><b>{r.first_name}</b><div className="small muted mono">{localPhone(r.phone)}{r.city ? ` · ${r.city}` : ""}</div>
                <div className="small muted">{r.website ? `${r.website}${r.website_read === false ? " (couldn't open)" : ""}` : "No website"} · {ago(r.created_at)}</div></td>
              <td data-l="Report">{r.status === "done"
                ? <span><b>{r.score}</b><span className="muted">/100</span> · <span className="money">{rs(r.gap_year)}</span>/yr</span>
                : <span className={r.status === "failed" ? "d1-bad" : "muted"}>{STATUS[r.status]}{r.error ? ` — ${r.error}` : ""}</span>}</td>
              <td data-l="WhatsApp" className="small">{r.sent_at
                ? <span className="d1-ok">Sent{r.send_count > 1 ? ` ×${r.send_count}` : ""}{r.read ? " · read" : r.delivered ? " · delivered" : ""}{r.opened_at ? " · opened" : ""}</span>
                : r.last_send && !r.last_send.ok ? <span className="d1-bad">Not sent ({r.last_send.via})</span> : <span className="muted">Not sent</span>}</td>
              <td data-l="Saved">{r.saved ? <Pill kind="on">Saved</Pill> : <span className="muted small">No</span>}</td>
              <td><div className="row" style={{ gap: 6 }}>
                {r.status === "done" && <Busy className="btn sm" run={() => act(`/admin/day1/runs/${r.id}/send`, `Sent to ${r.first_name}`)}>{r.sent_at ? "Resend" : "Send report"}</Busy>}
                {r.status === "failed" && <Busy className="btn sm" run={() => act(`/admin/day1/runs/${r.id}/retry`, "Back in the queue")}>Try again</Busy>}
                {r.png_url && <a className="btn sm ghost" href={r.png_url} target="_blank" rel="noopener">Image</a>}
                {r.pdf_url && <a className="btn sm ghost" href={r.pdf_url} target="_blank" rel="noopener">PDF</a>}
                <a className="btn sm ghost" href={r.share_url} target="_blank" rel="noopener">Share page</a>
              </div></td>
            </tr>))}
          </tbody>
        </table>
      )}
    </div>
  );
}

function Webinars() {
  const { toast } = useHub();
  const [list, reload] = useLoad("/admin/day1/cohorts");
  const [open, setOpen] = useState(null);
  const [c, setC] = useState({ code: "", name: "", workshop_date: "" });
  useEffect(() => {   // numbers move during a live webinar
    const t = setInterval(() => { if (document.visibilityState === "visible") reload(); }, 8000);
    return () => clearInterval(t);
  }, [reload]);
  if (!list) return <p className="muted">Loading…</p>;
  return (
    <div className="stack">
      <div className="panel">
        <span className="label">New webinar</span>
        <p className="small muted">A webinar is a cohort. Its link is <span className="mono">/report?c=CODE</span>: each WhatsApp number gets one report per webinar.</p>
        <div className="grid3 form-row" style={{ gridTemplateColumns: "1fr 2fr 1fr auto", alignItems: "end" }}>
          <Field label="Code"><input className="input mono" placeholder="WEB-OCT-14" value={c.code} onChange={(e) => setC({ ...c, code: e.target.value.toUpperCase() })} /></Field>
          <Field label="Name"><input className="input" placeholder="₹99 webinar · 14 Oct" value={c.name} onChange={(e) => setC({ ...c, name: e.target.value })} /></Field>
          <Field label="Date"><input className="input" type="date" value={c.workshop_date} onChange={(e) => setC({ ...c, workshop_date: e.target.value })} /></Field>
          <Busy className="btn" run={async () => { await api("/admin/cohorts", { method: "POST", body: { ...c, workshop_date: c.workshop_date || null } }); toast("Webinar added"); setC({ code: "", name: "", workshop_date: "" }); reload(); }}>Add</Busy>
        </div>
      </div>
      {list.length === 0 && <p className="muted">No webinars yet.</p>}
      {list.map((w) => (
        <div key={w.code} className="panel">
          <div className="row between">
            <div className="stack" style={{ gap: 2 }}><b className="mono">{w.code}</b><span className="small muted">{w.name}{w.date ? ` · ${w.date}` : ""}</span></div>
            <Switch checked={w.open} label={w.open ? "Open" : "Closed"} onChange={async (v) => { await api(`/admin/day1/cohorts/${w.code}`, { method: "PATCH", body: { open: v } }); reload(); }} />
          </div>
          <div className="inline-add"><input className="input mono" readOnly value={w.link} aria-label="Report link" /><Copy text={w.link} label="Copy link" /></div>
          <div className="d1-stats">
            <span><b>{w.runs}</b> started</span><span><b>{w.completed}</b> ready</span>{w.building > 0 && <span><b>{w.building}</b> building</span>}
            {w.failed > 0 && <span className="d1-bad"><b>{w.failed}</b> failed</span>}<span><b>{w.sent}</b> sent</span>
            <span><b>{w.delivered}</b> delivered</span><span><b>{w.read}</b> read</span><span><b>{w.opened}</b> opened</span><span><b>{w.saved}</b> saved</span>
          </div>
          <div className="row">
            <Busy className="btn sm primary" disabled={!w.pending} run={async () => {
              if (!window.confirm(`Send ${w.pending} reports on WhatsApp now?`)) return;
              const r = await api(`/admin/day1/cohorts/${w.code}/send-pending`, { method: "POST" });
              toast(`Sending ${r.queued} reports…`); setTimeout(reload, 2500);
            }}>Send all pending ({w.pending})</Busy>
            <a className="btn sm" href={`/api/admin/day1/cohorts/${w.code}/export.csv`} download>Export cohort CSV</a>
            <button className="btn sm ghost" onClick={() => setOpen(open === w.code ? null : w.code)}>{open === w.code ? "Hide people" : "People"}</button>
          </div>
          {open === w.code && <People code={w.code} onChange={reload} />}
        </div>
      ))}
    </div>
  );
}

function Library({ cfg, draft, setDraft, onSaved }) {
  const { toast } = useHub();
  const [err, setErr] = useState("");
  const t = (k, v) => setDraft({ ...draft, templates: { ...draft.templates, [k]: v } });
  const f = (k, patch) => setDraft({ ...draft, findings: { ...draft.findings, [k]: { ...draft.findings[k], ...patch } } });
  const order = [...cfg.findings].sort((a, b) => (draft.findings[a.key].priority - draft.findings[b.key].priority));
  return (
    <div className="stack d1-admin">
      <div className="panel flat">
        <span className="label">How it works</span>
        <p className="small">The three findings are the first three that apply, in priority order; each rule is fixed in code and reads only what the attendee gave. The five "imagine" lines answer those findings. Write <span className="mono">[words]</span> for systems words (aqua on the report); rupee amounts turn amber by themselves. The AI never writes a finding or a number.</p>
        <p className="small muted">Placeholders: {Object.entries(cfg.placeholders).map(([k, v]) => <span key={k} title={v} className="mono">{`{${k}}`} </span>)}</p>
      </div>

      <div className="panel">
        <span className="label">The gap line</span>
        <Field label="Cold rate (%)" hint="Lost enquiries = enquiries a month × this; × close rate × average order × 12 = about ₹X a year.">
          <input className="input" type="number" min={1} max={90} style={{ maxWidth: 120 }} value={draft.constants.cold_rate_pct}
            onChange={(e) => setDraft({ ...draft, constants: { ...draft.constants, cold_rate_pct: e.target.value === "" ? "" : Number(e.target.value) } })} />
        </Field>
      </div>

      <div className="panel">
        <span className="label">Findings and imagine lines</span>
        {order.map((x) => {
          const d = draft.findings[x.key];
          return (
            <div key={x.key} className={`d1-finding ${d.on ? "" : "off"}`}>
              <div className="row between">
                <span className="rule">When: {x.rule}</span>
                <div className="row" style={{ gap: 8 }}>
                  <label className="small muted">Priority <input className="input d1-prio" type="number" min={1} max={999} value={d.priority} onChange={(e) => f(x.key, { priority: Number(e.target.value) || 1 })} /></label>
                  <Switch checked={d.on} label={d.on ? "On" : "Off"} onChange={(v) => f(x.key, { on: v })} />
                </div>
              </div>
              <input className="input" aria-label="Label" value={d.label} onChange={(e) => f(x.key, { label: e.target.value })} />
              <textarea className="textarea" aria-label="Finding" value={d.text} onChange={(e) => f(x.key, { text: e.target.value })} />
              <div className="grid2">
                {[0, 1].map((i) => <input key={i} className="input" aria-label={`Imagine line ${i + 1}`} value={d.imagine[i]}
                  onChange={(e) => f(x.key, { imagine: d.imagine.map((l, j) => (j === i ? e.target.value : l)) })} />)}
              </div>
              {(d.label !== x.default.label || d.text !== x.default.text || d.imagine.join() !== x.default.imagine.join()) &&
                <button className="btn sm ghost" style={{ justifySelf: "start" }} onClick={() => f(x.key, { label: x.default.label, text: x.default.text, imagine: [...x.default.imagine] })}>Back to the default wording</button>}
            </div>
          );
        })}
      </div>

      <div className="panel">
        <span className="label">Report text</span>
        {cfg.templates.map((x) => (
          <Field key={x.key} label={x.label}>
            <textarea className="textarea" value={draft.templates[x.key]} onChange={(e) => t(x.key, e.target.value)} />
            {draft.templates[x.key] !== x.default && <button className="btn sm ghost" style={{ justifySelf: "start" }} onClick={() => t(x.key, x.default)}>Back to the default</button>}
          </Field>
        ))}
        <Field label="Spare imagine lines" hint="Used only when fewer than five come from the findings. One per line.">
          <textarea className="textarea" value={draft.imagine_defaults.join("\n")} onChange={(e) => setDraft({ ...draft, imagine_defaults: e.target.value.split("\n") })} />
        </Field>
      </div>

      <FormError msg={err} />
      <div className="row">
        <Busy className="btn primary" onError={setErr} run={async () => {
          await api("/admin/day1/config", { method: "PUT", body: payload(draft) }); toast("Saved — new reports use it"); onSaved();
        }}>Save</Busy>
        <Busy className="btn ghost" run={async () => {
          if (!window.confirm("Put every finding, line and number back to the defaults?")) return;
          await api("/admin/day1/config/reset", { method: "POST" }); toast("Back to the defaults"); onSaved();
        }}>Reset everything</Busy>
        {cfg.updated_at && <span className="small muted">Last saved {ago(cfg.updated_at)}</span>}
      </div>
    </div>
  );
}

const payload = (d) => ({
  templates: d.templates,
  findings: Object.fromEntries(Object.entries(d.findings).map(([k, v]) => [k, { on: v.on, priority: v.priority, label: v.label, text: v.text, imagine: v.imagine }])),
  imagine_defaults: d.imagine_defaults.map((l) => l.trim()).filter(Boolean),
  constants: d.constants,
});

function Preview({ draft }) {
  const [s, setS] = useState(SAMPLE);
  const [out, setOut] = useState(null);
  const [err, setErr] = useState("");
  const site = s.site;
  return (
    <div className="d1-preview">
      <div>{out ? <img src={out.png} alt={out.report.title} /> : <p className="muted small">Press Preview to draw the report with your unsaved edits.</p>}
        {out && <p className="small muted">{Math.round(out.png_bytes / 1024)} KB · findings: {out.report.finding_keys.join(", ") || "none"}</p>}</div>
      <div className="stack">
        <span className="label">Sample attendee (made up)</span>
        <div className="grid2">
          <Field label="First name"><input className="input" value={s.first_name} onChange={(e) => setS({ ...s, first_name: e.target.value })} /></Field>
          <Field label="City"><input className="input" value={s.city} onChange={(e) => setS({ ...s, city: e.target.value })} /></Field>
        </div>
        <Switch checked={!!site} label={site ? "Has a website" : "No website"} onChange={(v) => setS({ ...s, site: v ? SAMPLE.site : null })} />
        {site && <div className="row" style={{ gap: 6 }}>{CHECKS.map(([k, l]) => (
          <button key={k} type="button" className={`btn sm ${site.checks[k] ? "" : "ghost"}`} aria-pressed={!!site.checks[k]}
            onClick={() => setS({ ...s, site: { ...site, checks: { ...site.checks, [k]: !site.checks[k] } } })}>{site.checks[k] ? "✓ " : "✕ "}{l}</button>))}</div>}
        <div className="grid2">
          {QUESTIONS.map((q) => (
            <label key={q} className="row between small"><span>{q}</span>
              <select className="select" style={{ maxWidth: 120 }} value={s.answers[q]} onChange={(e) => setS({ ...s, answers: { ...s.answers, [q]: e.target.value } })}>
                {ANSWERS.map((a) => <option key={a}>{a}</option>)}</select></label>))}
        </div>
        <div className="grid2">
          <Field label="Enquiries a month"><input className="input" type="number" value={s.numbers.enquiries} onChange={(e) => setS({ ...s, numbers: { ...s.numbers, enquiries: Number(e.target.value) } })} /></Field>
          <Field label="Average order (₹)"><input className="input" type="number" value={s.numbers.aov} onChange={(e) => setS({ ...s, numbers: { ...s.numbers, aov: Number(e.target.value) } })} /></Field>
          <Field label="Close rate"><select className="select" value={s.numbers.close_rate} onChange={(e) => setS({ ...s, numbers: { ...s.numbers, close_rate: e.target.value } })}>
            {["10", "20", "30", "40"].map((v) => <option key={v} value={v}>{v === "40" ? "40%+" : `${v}%`}</option>)}</select></Field>
          <Field label="Google reviews, last 12 months"><select className="select" value={s.numbers.reviews_12m} onChange={(e) => setS({ ...s, numbers: { ...s.numbers, reviews_12m: e.target.value } })}>
            {[["none", "None"], ["1_5", "1 to 5"], ["6_20", "6 to 20"], ["20_plus", "More than 20"], ["not_sure", "Not sure"]].map(([v, l]) => <option key={v} value={v}>{l}</option>)}</select></Field>
        </div>
        <FormError msg={err} />
        <Busy className="btn primary" onError={setErr} run={async () => setOut(await api("/admin/day1/preview", { method: "POST", body: { config: payload(draft), sample: s } }))}>Preview</Busy>
        {out && (
          <div className="panel flat">
            <span className="label">{out.report.missing.heading} · {out.report.missing.tag}</span>
            <p><b><Rich text={out.report.missing.line} /></b></p>
            <p className="small"><Rich text={out.report.missing.math} /></p>
          </div>
        )}
      </div>
    </div>
  );
}

function Logs() {
  const { toast } = useHub();
  const [l, reload] = useLoad("/admin/day1/logs");
  if (!l) return <p className="muted">Loading…</p>;
  const retry = async (id) => { await api(`/admin/day1/runs/${id}/retry`, { method: "POST" }); toast("Back in the queue"); reload(); };
  return (
    <div className="stack">
      <div className="grid3">
        <div className="panel"><span className="label">Queue</span><span className="num">{l.queue.queued + l.queue.building}</span>
          <span className="small muted">{l.queue.queued} waiting · {l.queue.building} building · {l.workers} at a time per server</span></div>
        <div className="panel"><span className="label">Last 24 hours</span><span className="num">{l.queue.done_24h}</span>
          <span className="small muted">built · about {l.seconds.average ?? "—"} s each (slowest {l.seconds.slowest ?? "—"} s){l.png_kb_max ? ` · largest image ${l.png_kb_max} KB` : ""}</span></div>
        <div className="panel"><span className="label">AI cost per run</span><span className="num">₹{l.ai.cost_per_run_inr}</span><span className="small muted">{l.ai.note}</span></div>
      </div>
      <div className="panel flat">
        <span className="small">Today: <b>{l.today}</b> of {l.daily_cap} reports allowed. WhatsApp template: {l.whatsapp.campaign
          ? <span className="d1-ok">set ({l.whatsapp.media}, link as {l.whatsapp.link})</span> : <span className="d1-bad">not set — set AISENSY_REPORT_CAMPAIGN</span>}
          {" · "}delivery webhook: {l.whatsapp.status_webhook ? <span className="d1-ok">on</span> : <span className="muted">off</span>}</span>
      </div>
      <div className="panel">
        <span className="label">Failed reports ({l.queue.failed})</span>
        {l.failures.length === 0 ? <p className="small muted">None.</p> : (
          <table className="t cards"><thead><tr><th>Person</th><th>Webinar</th><th>Error</th><th /></tr></thead>
            <tbody>{l.failures.map((r) => <tr key={r.id}><td><b>{r.first_name}</b> <span className="small muted mono">{localPhone(r.phone)}</span></td>
              <td data-l="Tried" className="small">{r.attempts}×</td><td className="small d1-bad">{r.error}</td>
              <td><Busy className="btn sm" run={() => retry(r.id)}>Try again</Busy></td></tr>)}</tbody></table>)}
      </div>
      {l.send_failures.length > 0 && (
        <div className="panel">
          <span className="label">WhatsApp sends that didn't go</span>
          <table className="t cards"><thead><tr><th>Person</th><th>Why</th><th /></tr></thead>
            <tbody>{l.send_failures.map((r) => <tr key={r.id}><td><b>{r.first_name}</b> <span className="small muted mono">{localPhone(r.phone)}</span></td>
              <td className="small d1-bad">{r.last_send?.via}</td>
              <td><Busy className="btn sm" run={async () => { await api(`/admin/day1/runs/${r.id}/send`, { method: "POST" }); toast("Sent"); reload(); }}>Send again</Busy></td></tr>)}</tbody></table>
        </div>
      )}
      <button className="btn sm ghost" style={{ justifySelf: "start" }} onClick={reload}>Refresh</button>
    </div>
  );
}

export default function AdminDay1() {
  const [tab, setTab] = useState("webinars");
  const [cfg, reloadCfg] = useLoad("/admin/day1/config");
  const [draft, setDraft] = useState(null);
  useEffect(() => {
    if (!cfg) return;
    setDraft({ templates: Object.fromEntries(cfg.templates.map((x) => [x.key, x.value])),
      findings: Object.fromEntries(cfg.findings.map((x) => [x.key, { on: x.on, priority: x.priority, label: x.label, text: x.text, imagine: [...x.imagine] }])),
      imagine_defaults: [...cfg.imagine_defaults], constants: { ...cfg.constants } });
  }, [cfg]);
  return (
    <Sheet kicker="Admin · Day-1 reports" id="A-07" pillar="Admin" title={<>The webinar's one-page <em>report</em>.</>}
      sub="During the ₹99 webinar each attendee makes their report with no login. Send them on WhatsApp, see who opened and saved, and export the cohort for the Performance Hub.">
      <nav className="row" aria-label="Admin"><Link className="btn sm ghost" to="/admin">Pulse</Link><Link className="btn sm ghost" to="/admin/features">Features &amp; plans</Link></nav>
      <Tabs tabs={[["webinars", "Webinars"], ["library", "Library"], ["preview", "Preview"], ["logs", "Logs"]]} value={tab} onChange={setTab} />
      {tab === "webinars" && <Webinars />}
      {tab === "library" && (cfg && draft ? <Library cfg={cfg} draft={draft} setDraft={setDraft} onSaved={reloadCfg} /> : <p className="muted">Loading…</p>)}
      {tab === "preview" && (draft ? <Preview draft={draft} /> : <p className="muted">Loading…</p>)}
      {tab === "logs" && <Logs />}
    </Sheet>
  );
}
