import React, { useState } from "react";
import { Link } from "react-router-dom";
import { ago, api } from "../api.js";
import { Busy, Copy, Field, FormError, Sheet, useHub, useLoad } from "../ui.jsx";
import { Empty, Loading, Pill } from "../v2.jsx";
import "./competitors.css";

/* Content & company research (Action Program). Post ideas come from the competitor report (or the business profile);
   company research is a plan plus searches the owner opens themselves. Nothing is scraped, no people's details collected. */

const HEAD = { kicker: "Scale · Content & company research", id: "SC-15", pillar: "Scale" };
const SUB = "Post ideas that answer what your competitors changed, and a plan to find companies that fit your ideal customer.";
const weekOf = (iso) => (iso ? new Date(`${iso}T00:00:00`).toLocaleDateString("en-IN", { day: "numeric", month: "short" }) : "");

function Ideas({ out }) {
  const ideas = out.result?.ideas || [];
  return (
    <div className="stack">
      <span className="small muted">{out.note} · {ago(out.at)}</span>
      {ideas.map((x, i) => (
        <div key={i} className="cw-idea">
          <div className="row between" style={{ alignItems: "flex-start" }}>
            <Pill kind="on">{x.format_label}</Pill>
            <Copy text={`${x.hook}\n\n${x.say}`} />
          </div>
          <h3>{x.hook}</h3>
          <p>{x.say}</p>
          <span className="small muted">Answers: {x.answers}</span>
        </div>
      ))}
    </div>
  );
}

function ContentPanel({ data, onDone }) {
  const { refresh, me } = useHub();
  const [err, setErr] = useState("");
  const latest = data.content[0];
  const r = data.report;
  return (
    <div className="panel">
      <span className="label">Post ideas</span>
      <p className="small">
        {r ? <>From your competitor report of the week of {weekOf(r.week)} ({r.changed} {r.changed === 1 ? "competitor" : "competitors"} changed something).</>
          : <>No competitor changes to answer yet, so ideas come from your business profile.{" "}
            {me?.features?.competitor_watch && <>{data.watching ? "They'll use your next report with changes." : <Link to="/competitors">Add competitors to watch</Link>}</>}</>}
      </p>
      <FormError msg={err} />
      <div className="row">
        <Busy className={`btn ${latest ? "" : "primary"}`} onError={setErr} run={async () => {
          await api("/research/content", { method: "POST" });
          onDone();
          refresh();
        }}>{latest ? "Write 5 fresh ideas" : "Write 5 post ideas"}</Busy>
        <span className="small muted">Uses 1 AI run. Posts never name a competitor or copy their words.</span>
      </div>
      {latest ? <Ideas out={latest} /> : <Empty>Your ideas will show here: a hook, what to say, the format, and which competitor move each one answers.</Empty>}
      {data.content.length > 1 && (
        <details className="cw-past">
          <summary><span>Earlier ideas</span><span className="small muted">{data.content.length - 1}</span></summary>
          <div className="stack" style={{ paddingTop: 12 }}>{data.content.slice(1).map((o) => <Ideas key={o.id} out={o} />)}</div>
        </details>
      )}
    </div>
  );
}

function downloadCsv(text) {
  const url = URL.createObjectURL(new Blob(["﻿" + text], { type: "text/csv;charset=utf-8" }));
  const a = document.createElement("a");
  a.href = url;
  a.download = "company-research.csv";
  document.body.appendChild(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

function Plan({ out }) {
  const p = out.result || {};
  const f = p.filters || {};
  const main = (out.links || []).filter((l) => !l.type);
  const byType = (out.links || []).filter((l) => l.type);
  return (
    <div className="stack">
      <span className="small muted">For “{out.input?.who}”{out.input?.city ? ` in ${out.input.city}` : ""} · {ago(out.at)}</span>
      {p.summary && <p>{p.summary}</p>}
      <div className="cw-block">
        <span className="label">Kinds of companies to list</span>
        <ul className="read-list">{(p.company_types || []).map((t, i) => <li key={i}><b>{t.type}</b>{t.why ? <span className="muted"> — {t.why}</span> : null}</li>)}</ul>
      </div>
      <div className="cw-block">
        <span className="label">Filters</span>
        <div className="cw-filters">
          {[["Size", f.size], ["Industry", f.industry], ["Location", f.location]].map(([k, v]) => v?.length ? (
            <div key={k}><span className="small muted">{k}</span><span>{v.join(" · ")}</span></div>
          ) : null)}
        </div>
      </div>
      <div className="cw-block">
        <span className="label">Questions to qualify each company</span>
        <ol className="read-list">{(p.qualify || []).map((q, i) => <li key={i}>{q}</li>)}</ol>
      </div>
      <div className="cw-block">
        <span className="label">Ready searches — open them yourself</span>
        <div className="cw-links">
          {main.map((l, i) => <a key={i} className="cw-link" href={l.url} target="_blank" rel="noopener noreferrer"><b>{l.label}</b><span className="small muted">{l.what}</span></a>)}
        </div>
        {byType.length > 0 && (
          <>
            <span className="small muted">On the map, one kind at a time:</span>
            <div className="cw-links">
              {byType.map((l, i) => <a key={i} className="cw-link" href={l.url} target="_blank" rel="noopener noreferrer"><b>{l.what}</b><span className="small muted">Google Maps</span></a>)}
            </div>
          </>
        )}
      </div>
      <div className="row">
        <button className="btn sm" onClick={() => downloadCsv(out.csv)}>Download the CSV template</button>
        <span className="small muted">Columns: company, website, city, contact page, why they fit, status. Fill it as you go.</span>
      </div>
      {p.dropped > 0 && <span className="small muted">{p.dropped} {p.dropped === 1 ? "line" : "lines"} that named a particular company, person or contact {p.dropped === 1 ? "was" : "were"} left out.</span>}
      <div className="cw-note">{out.note}</div>
    </div>
  );
}

function CompaniesPanel({ data, onDone }) {
  const { refresh } = useHub();
  const [f, setF] = useState({ who: "", city: "", extra: "" });
  const [err, setErr] = useState("");
  const set = (k) => (e) => setF({ ...f, [k]: e.target.value });
  const latest = data.companies[0];
  return (
    <div className="panel">
      <span className="label">Find companies that fit</span>
      <p className="small">Describe the companies that buy from you. You get a plan — which kinds of companies to list, how to filter them, what to ask — and searches ready to open.</p>
      <div className="grid2">
        <Field label="What kind of company buys from you?"><input className="input" maxLength={200} value={f.who} onChange={set("who")} placeholder="Restaurants and cloud kitchens" /></Field>
        <Field label="City or area"><input className="input" maxLength={60} value={f.city} onChange={set("city")} placeholder="Pune" /></Field>
      </div>
      <Field label="Anything else (optional)" hint="Size, budget, what they usually need from you.">
        <textarea className="input textarea" maxLength={300} value={f.extra} onChange={set("extra")} placeholder="50+ covers, open for 2 years or more" />
      </Field>
      <FormError msg={err} />
      <div className="row">
        <Busy className={`btn ${latest ? "" : "primary"}`} disabled={f.who.trim().length < 3} onError={setErr} run={async () => {
          await api("/research/companies", { method: "POST", body: { who: f.who.trim(), city: f.city.trim(), extra: f.extra.trim() } });
          onDone();
          refresh();
        }}>Make my research plan</Busy>
        <span className="small muted">Uses 1 AI run.</span>
      </div>
      {latest ? <Plan out={latest} /> : <Empty>Your plan, the ready searches and the CSV template will show here.</Empty>}
      {data.companies.length > 1 && (
        <details className="cw-past">
          <summary><span>Earlier plans</span><span className="small muted">{data.companies.length - 1}</span></summary>
          <div className="stack" style={{ paddingTop: 12 }}>{data.companies.slice(1).map((o) => <Plan key={o.id} out={o} />)}</div>
        </details>
      )}
    </div>
  );
}

export default function Research() {
  const [data, reload, err] = useLoad("/research");
  const title = <>Posts to make, companies to <em>find</em>.</>;
  if (!data) return <Sheet {...HEAD} title={title} sub={SUB}>{err ? <FormError msg={err.message} /> : <Loading />}</Sheet>;
  return (
    <Sheet {...HEAD} title={title} sub={SUB}>
      <div className="cw-note">The hub doesn't scrape social media or collect anyone's personal details. It never opens these searches for you — you do, and you note company details only.</div>
      <ContentPanel data={data} onDone={reload} />
      <CompaniesPanel data={data} onDone={reload} />
    </Sheet>
  );
}
