import React, { useEffect, useRef, useState } from "react";
import { Link, useLocation, useSearchParams } from "react-router-dom";
import { ago, api } from "../api.js";
import { Busy, Copy, FormError, LoadError, Sheet, useHub, useLoad } from "../ui.jsx";
import { Empty, Loading, Pill } from "../v2.jsx";
import "./data.css";

/* v2.3 Data engine — Tender finder: open government tenders on GeM that fit the business, a link to each one, and help to
   apply built from the tender's own bid document. Owner and managers. */

const FIT = { good: ["Good fit", "on"], maybe: ["Check first", "warn"], hard: ["Hard to meet now", "off"] };
const NEED = { have: ["You have it", "on"], check: ["Check", "warn"], missing: ["Missing", "off"] };
const READY = { have: "on", missing: "off", unknown: "" };
const FACTS = [["ends", "Ends"], ["opens", "Bid opens"], ["starts", "Auction starts"], ["parent_bid", "Full terms in bid"],
  ["organisation", "Buyer"], ["ministry", "Ministry / State"],
  ["department", "Department"], ["quantity", "Quantity"], ["estimated_value", "Estimated value (₹)"], ["bid_type", "Type of bid"],
  ["emd_required", "EMD required"], ["emd_amount", "EMD amount (₹)"], ["turnover", "Minimum turnover"], ["experience", "Experience asked"],
  ["past_performance", "Past performance"], ["mse_relaxation", "MSE relaxation"], ["startup_relaxation", "Startup relaxation"],
  ["mse_preference", "MSE purchase preference"], ["payment_days", "Paid within (days)"], ["treds", "TReDS payment"]];

const ends = (t) => (t.days_left === 0 ? "Ends today" : t.days_left === 1 ? "Ends tomorrow" : `${t.days_left} days left`);
const endDate = (iso) => (iso ? new Date(iso).toLocaleString("en-IN", { day: "numeric", month: "short", hour: "numeric", minute: "2-digit", timeZone: "Asia/Kolkata" }) : "");

function ApplyLink({ url, className = "btn" }) {
  return <a className={className} href={url} target="_blank" rel="noopener noreferrer">Apply on GeM ↗</a>;
}

function Words({ data, set }) {
  const [words, setWords] = useState(data.words);
  const [draft, setDraft] = useState("");
  const [err, setErr] = useState("");
  useEffect(() => setWords(data.words), [data.words]);
  const add = () => {
    const w = draft.trim();
    if (w.length >= 3 && words.length < data.max_words && !words.some((x) => x.toLowerCase() === w.toLowerCase())) setWords([...words, w]);
    setDraft("");
  };
  const changed = words.join("|") !== data.words.join("|");
  return (
    <section className="panel tf-words">
      <span className="label">Search words</span>
      <p className="small muted">The words a government buyer would use for what you sell. {data.words_from === "profile" ? "Taken from your Knowledge Hub — change them to fit." : ""}</p>
      <div className="tf-chips">
        {words.map((w) => (
          <span key={w} className="tf-chip">{w}<button aria-label={`Remove ${w}`} onClick={() => setWords(words.filter((x) => x !== w))}>×</button></span>
        ))}
        {words.length < data.max_words && (
          <input className="input" maxLength={40} placeholder="Add a word, like office furniture" value={draft}
            onChange={(e) => setDraft(e.target.value)} onKeyDown={(e) => { if (e.key === "Enter") { e.preventDefault(); add(); } }} onBlur={add} />
        )}
      </div>
      <FormError msg={err} />
      <div className="row">
        <Busy className="btn primary" disabled={!words.length} onError={(e) => setErr(e.message)}
          run={async () => { setErr(""); set(await api("/tenders/search", { method: "POST", body: { words, force: !changed } })); }}>
          {changed ? "Search GeM with these words" : "Check GeM again"}</Busy>
        <Busy className="btn ghost ai" onError={(e) => setErr(e.message)}
          run={async () => { setErr(""); const d = await api("/tenders/words", { method: "POST" }); set(await api("/tenders/search", { method: "POST", body: { words: d.words, force: true } })); }}>
          Suggest better words · {data.costs.words} token</Busy>
      </div>
    </section>
  );
}

function Ready({ ready }) {
  return (
    <div className="tf-ready">
      <span className="small muted">Papers most tenders ask for:</span>
      {ready.map((r) => <Pill key={r.key} kind={READY[r.status]}>{r.label}{r.status === "have" ? " ✓" : r.status === "missing" ? ": not yet" : ": not in your Knowledge Hub"}</Pill>)}
      {ready.some((r) => r.status !== "have") && <Link className="small" to="/knowledge">Update your Knowledge Hub →</Link>}
    </div>
  );
}

function TenderRow({ t, cost, open }) {
  return (
    <article className="tf-row">
      <div className="tf-top">
        <b>{t.title}</b>
        {t.days_left != null && <Pill kind={t.days_left <= 3 ? "warn" : ""}>{ends(t)}</Pill>}
      </div>
      <span className="small muted">{[t.ministry, t.department].filter(Boolean).join(" · ") || "Government buyer"}</span>
      <span className="small">
        {t.kind} {t.bid_number}{t.quantity ? ` · Quantity ${t.quantity}` : ""}{t.ends_at ? ` · Ends ${endDate(t.ends_at)}` : ""}
      </span>
      <span className="small muted">Found with: {t.words.join(", ")}</span>
      <div className="row">
        <ApplyLink url={t.url} className="btn primary sm" />
        <button className={`btn sm ${t.helped ? "" : "ai"}`} onClick={() => open(t.bid_id, !t.helped)}>{t.helped ? "See help to apply" : `Help to apply · ${cost} token`}</button>
      </div>
    </article>
  );
}

function Help({ bidId, row, cost, onBack, onBuilt }) {
  const { refresh } = useHub();
  const asked = useLocation().state?.build;   // opened with the "Help to apply" button, not by a reload or a shared link
  const [h, setH] = useState(null);
  const [err, setErr] = useState("");
  const [state, setState] = useState("loading");   // loading | building | ask
  const started = useRef(false);
  const done = (x) => { setH(x); refresh(); onBuilt(); };
  const build = () => {
    setErr(""); setState("building");
    return api(`/tenders/${bidId}/help`, { method: "POST" }).then(done).catch(async (e) => {
      // The connection dropped, but the hub may still finish and keep the help: look for it for a while before asking again.
      for (let i = 0; e.network && i < 8; i += 1) {
        await new Promise((ok) => setTimeout(ok, 5000));
        const kept = await api(`/tenders/${bidId}/help`).catch(() => null);
        if (kept) return done(kept);
      }
      setErr(e.network ? "The connection dropped before your help came back. Try again: if it was finished, it opens without using another token."
        : e.message);
      setState("ask");
    });
  };
  useEffect(() => {
    if (started.current) return;
    started.current = true;
    api(`/tenders/${bidId}/help`).then(setH).catch((e) => {
      if (e.status !== 404) { setErr(e.message); setState("ask"); } else if (asked && row) build(); else setState("ask");
    });
  }, [bidId]); // eslint-disable-line react-hooks/exhaustive-deps
  const back = <button className="btn sm ghost" onClick={onBack}>← All tenders</button>;
  if (!h) {
    return (
      <div className="stack">
        {back}
        {state === "ask" ? (
          <>
            <FormError msg={err} />
            {row ? (
              <div className="row">
                <Busy className="btn primary ai" run={build}>Help me apply · {cost} token</Busy>
                <ApplyLink url={row.url} />
              </div>
            ) : <p className="muted">This tender isn't in your results any more. Go back and search again.</p>}
          </>
        ) : <p className="muted">{state === "building" ? "Reading the tender's document on GeM and checking it against your business…" : "Loading…"}</p>}
      </div>
    );
  }
  const fit = FIT[h.help.fit.verdict] || FIT.maybe;
  const facts = FACTS.filter(([k]) => h.facts[k]);
  return (
    <div className="stack">
      <div className="row between">{back}<ApplyLink url={h.url} className="btn primary" /></div>
      <div>
        <h2 style={{ margin: 0 }}>{h.title}</h2>
        <span className="small muted">GeM bid {h.bid_number} <Copy text={h.bid_number} label="Copy bid number" className="btn sm ghost" /></span>
      </div>

      <section className="panel tf-help">
        <div className="row"><Pill kind={fit[1]}>{fit[0]}</Pill><span className="small">{h.help.fit.why}</span></div>
        <p>{h.help.summary}</p>
      </section>

      {facts.length > 0 && (
        <section className="panel">
          <span className="label">From the tender</span>
          <dl className="tf-facts">
            {facts.map(([k, label]) => <div key={k}><dt>{label}</dt><dd>{h.facts[k]}</dd></div>)}
          </dl>
          {h.facts.documents && <p className="small"><b>Documents asked from the seller:</b> {h.facts.documents}</p>}
          <p className="small muted">Read from the tender's own document on GeM. Check the full document before you bid.</p>
        </section>
      )}

      {h.help.requirements.length > 0 && (
        <section className="panel">
          <span className="label">Can you apply?</span>
          <div className="tf-needs">
            {h.help.requirements.map((r) => (
              <div key={r.need} className="tf-need">
                <Pill kind={(NEED[r.status] || NEED.check)[1]}>{(NEED[r.status] || NEED.check)[0]}</Pill>
                <div><b>{r.need}</b>{r.note && <div className="small muted">{r.note}</div>}</div>
              </div>
            ))}
          </div>
        </section>
      )}

      {h.help.documents.length > 0 && (
        <section className="panel">
          <span className="label">Keep these ready</span>
          <ul className="tf-list">{h.help.documents.map((d) => <li key={d}>{d}</li>)}</ul>
        </section>
      )}

      <section className="panel">
        <span className="label">How to apply on GeM</span>
        <ol className="tf-list">{h.steps.map((s) => <li key={s}>{s}</li>)}</ol>
        <div className="row">
          <ApplyLink url={h.url} className="btn primary" />
          {h.links.map((l) => <a key={l.url} className="btn ghost sm" href={l.url} target="_blank" rel="noopener noreferrer">{l.label} ↗</a>)}
        </div>
      </section>

      {(h.help.questions.length > 0 || h.help.watch_out.length > 0) && (
        <div className="grid2">
          {h.help.questions.length > 0 && (
            <section className="panel">
              <span className="label">Ask the buyer</span>
              <ul className="tf-list">{h.help.questions.map((q) => <li key={q}>{q}</li>)}</ul>
              {h.buyer_emails.length > 0 && <p className="small muted">The tender gives the buyer's email: {h.buyer_emails.join(", ")}. On GeM, questions usually go through the bid's own clarification step.</p>}
            </section>
          )}
          {h.help.watch_out.length > 0 && (
            <section className="panel">
              <span className="label">Watch out</span>
              <ul className="tf-list">{h.help.watch_out.map((w) => <li key={w}>{w}</li>)}</ul>
            </section>
          )}
        </div>
      )}

      <FormError msg={err} />
      <div className="row">
        <span className="small muted">Built {ago(h.at)}.</span>
        <Busy className="btn sm ghost ai" onError={(e) => setErr(e.message)}
          run={async () => { setErr(""); setH(await api(`/tenders/${bidId}/help`, { method: "POST", body: { again: true } })); refresh(); }}>
          Build it again · {h.costs.help} token</Busy>
      </div>
    </div>
  );
}

export default function Tenders() {
  const { refresh } = useHub();
  const [params, setParams] = useSearchParams();
  const open = Number(params.get("bid")) || null;
  const [loaded, reload, loadErr] = useLoad("/tenders");
  const [data, setData] = useState(null);
  const [err, setErr] = useState("");
  const [searching, setSearching] = useState(false);
  const auto = useRef(false);
  useEffect(() => { if (loaded) setData(loaded); }, [loaded]);
  const set = (d) => { setData(d); refresh(); };

  // the first visit (or a day later): check GeM once by itself
  useEffect(() => {
    if (!data || auto.current || !data.stale || data.error || !data.words.length || open) return;
    auto.current = true;
    setSearching(true);
    api("/tenders/search", { method: "POST", body: {} }).then(setData).catch((e) => setErr(e.message)).finally(() => setSearching(false));
  }, [data, open]);

  const head = { kicker: "Data · Tender finder", id: "DA-03", pillar: "Data" };
  const title = <>Government tenders that <em>fit</em> your business.</>;
  const sub = "Open tenders on GeM, the Government e-Marketplace, found with the words a government buyer uses for what you sell. Apply on GeM, or get help to apply first.";
  const go = (id, build = false) => setParams(id ? { bid: String(id) } : {}, { state: { build } });
  if (loadErr) return <Sheet {...head} title={title} sub={sub}><LoadError err={loadErr} /></Sheet>;
  if (!data) return <Sheet {...head} title={title} sub={sub}><Loading /></Sheet>;
  if (open) {
    return (
      <Sheet {...head} title={title} sub={sub}>
        <Help key={open} bidId={open} row={data.results.find((r) => r.bid_id === open)} cost={data.costs.help} onBack={() => go(null)} onBuilt={reload} />
      </Sheet>
    );
  }
  return (
    <Sheet {...head} title={title} sub={sub}>
      <Words data={data} set={set} />
      <Ready ready={data.ready} />
      {(err || data.error) && <FormError msg={err || data.error} />}
      <div className="row between">
        <span className="small muted">
          {searching ? "Checking GeM…" : data.checked_at ? `${data.results.length} open tender${data.results.length === 1 ? "" : "s"} · checked on GeM ${ago(data.checked_at)}` : ""}
        </span>
        <span className="small muted">{data.tokens_left} AI tokens left</span>
      </div>
      {searching && !data.results.length ? <Loading /> : data.results.length ? (
        <div className="tf-rows">
          {data.results.map((t) => <TenderRow key={t.bid_id} t={t} cost={data.costs.help} open={go} />)}
        </div>
      ) : data.checked_at ? (
        <Empty>No open tenders on GeM for these words right now. Try broader words, like the category a buyer would use (“dental consumables”, “housekeeping services”), or press “Suggest better words”.</Empty>
      ) : null}
      <p className="small muted">You apply on GeM itself, from your GeM seller account. Tenders here are GeM's own public listing; the hub doesn't apply for you or share your details with GeM.</p>
    </Sheet>
  );
}
