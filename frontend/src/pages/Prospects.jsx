import React, { useEffect, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { ago, api, localPhone, upload, waLink } from "../api.js";
import { Busy, Copy, Field, FormError, Modal, Sheet, Stat, useHub, useLoad } from "../ui.jsx";
import { Loading, Pill, Tabs } from "../v2.jsx";
import "./prospects.css";

/* v2.4 Find new customers (Lead engine). Businesses that fit, from Google Maps or the owner's own list; a first message for
   each that the owner sends from HIS OWN WhatsApp or mail app (the hub never sends these); day 3 and day 7 follow-ups; the
   Reply helper and "More like my best customers".
   Google's rules: the server keeps only place IDs. Names, addresses, phones and websites shown here are fetched live from
   Google for this page view and never saved — and every place they show carries the "Google Maps" attribution. */

const head = { kicker: "Lead engine · Find new customers", id: "LE-04", pillar: "Lead engine" };
const STATUS_KIND = { kept: "", drafted: "ai", contacted: "on", replied: "warn", deal: "money", not_interested: "off", dnc: "off" };
const FILTERS = [["", "All"], ["kept", "Kept"], ["drafted", "Drafted"], ["contacted", "Contacted"], ["replied", "Replied"],
  ["deal", "Became a deal"], ["not_interested", "Not interested"], ["dnc", "Do not contact"]];
const LEFT_OUT = { closed: "closed", known: "already in your contacts", own: "your own business", blocked: "marked Do not contact", far: "too far away" };

const plural = (n, one, many) => `${n} ${n === 1 ? one : many || `${one}s`}`;
const mailto = (to, subject, body) => `mailto:${to || ""}?subject=${encodeURIComponent(subject || "")}&body=${encodeURIComponent(body || "")}`;

/** WhatsApp opens in the tap itself (a blank tab first, so no pop-up blocker), then goes to the link the server builds —
    a Google business's number is looked up at that moment and never saved. */
async function openWhatsApp(run) {
  let w = null;
  try { w = window.open("about:blank", "_blank"); if (w) w.opener = null; } catch { w = null; }
  try {
    const r = await run();
    if (w && !w.closed) w.location.href = r.link; else window.location.href = r.link;
    return r;
  } catch (e) {
    if (w && !w.closed) w.close();
    throw e;
  }
}

const GoogleMark = () => <span className="pr-gm" translate="no">Google Maps</span>;

function Rule({ text }) {
  return <div className="pr-rule small" role="note">{text || "You decide who to contact. Do not message or call people who asked not to be contacted."}</div>;
}

/* ───────────── who to look for ───────────── */
function Kinds({ page, onChange }) {
  const { toast } = useHub();
  const [list, setList] = useState(page.kinds);
  const [word, setWord] = useState("");
  const [best, setBest] = useState(false);
  useEffect(() => setList(page.kinds), [page.kinds]);
  const dirty = JSON.stringify(list) !== JSON.stringify(page.kinds);
  const add = () => {
    const w = word.trim();
    if (w && list.length < page.max_kinds && !list.some((x) => x.toLowerCase() === w.toLowerCase())) setList([...list, w]);
    setWord("");
  };
  return (
    <div className="panel stack">
      <div className="row between"><span className="label">1 · Who to look for</span>
        <span className="small muted">{page.kinds_by === "ai" ? "Suggested by AI — change anything" : page.kinds_by === "best_customers" ? "From your best customers" : "3 to 6 kinds works best"}</span></div>
      <div className="pr-chips">
        {list.map((k) => (
          <span key={k} className="pr-chip">{k}<button type="button" aria-label={`Remove ${k}`} onClick={() => setList(list.filter((x) => x !== k))}>×</button></span>
        ))}
        {!list.length && <span className="small muted">No kinds yet. Type one, like “hardware wholesalers”, or let the AI suggest them from your Knowledge Hub.</span>}
      </div>
      {list.length < page.max_kinds && (
        <div className="pr-add">
          <input className="input" value={word} maxLength={60} placeholder="A kind of business, like garment exporters" aria-label="Kind of business"
            onChange={(e) => setWord(e.target.value)} onKeyDown={(e) => { if (e.key === "Enter") { e.preventDefault(); add(); } }} />
          <button className="btn sm" type="button" disabled={!word.trim()} onClick={add}>Add</button>
        </div>
      )}
      <div className="row">
        {dirty && <Busy className="btn sm primary" disabled={!list.length} run={async () => { onChange(await api("/prospects/kinds", { method: "PUT", body: { kinds: list } })); toast("Saved"); }}>Save this list</Busy>}
        <Busy className="btn sm ai" run={async () => {
          if (dirty && list.length) await api("/prospects/kinds", { method: "PUT", body: { kinds: list } });   // the AI adds to what you typed
          const r = await api("/prospects/kinds/suggest", { method: "POST" }); onChange(r); toast("Added from your Knowledge Hub");
        }}>Suggest with AI · 1 AI token</Busy>
        <button className="btn sm ghost" type="button" onClick={() => setBest((x) => !x)}>More like my best customers</button>
      </div>
      {best && <BestCustomers onFilled={(r) => { onChange(r); setBest(false); }} />}
    </div>
  );
}

function BestCustomers({ onFilled }) {
  const { toast } = useHub();
  const [data, reload] = useLoad("/prospects/best-customers");
  const [answers, setAnswers] = useState({});
  const [err, setErr] = useState("");
  useEffect(() => { if (data) setAnswers(Object.fromEntries(data.customers.map((c) => [c.key, c.kind || ""]))); }, [data]);
  if (!data) return <Loading />;
  if (!data.customers.length) return <p className="small muted">No sales yet to find your best customers. Add invoices or upload your sales sheet first.</p>;
  const changed = data.customers.some((c) => (answers[c.key] || "") !== (c.kind || ""));
  const allKnown = data.customers.every((c) => (answers[c.key] || "").trim());
  const save = async () => {
    await api("/prospects/best-customers", { method: "PUT", body: { answers: data.customers.map((c) => ({ key: c.key, kind: answers[c.key] || "" })) } });
    await reload();
  };
  const fill = async (useAi) => {
    if (changed) await save();
    const r = await api("/prospects/best-customers/fill", { method: "POST", body: { use_ai: useAi } });
    toast(useAi ? "Worded by AI from your best customers" : "Filled from your best customers");
    onFilled(r);
  };
  return (
    <div className="pr-best stack">
      <span className="small">Your top {data.customers.length} customers by sales. What kind of business is each one? You only answer this once.</span>
      {data.customers.map((c) => (
        <div key={c.key} className="pr-best-row">
          <b className="small">{c.name}</b>
          <input className="input" value={answers[c.key] || ""} maxLength={60} placeholder="Like: hotel kitchens" aria-label={`Kind of business for ${c.name}`}
            onChange={(e) => setAnswers({ ...answers, [c.key]: e.target.value })} />
        </div>
      ))}
      <FormError msg={err} />
      <div className="row">
        <Busy className="btn sm primary" onError={setErr} disabled={!Object.values(answers).some((v) => v.trim())} run={() => fill(false)}>Use these · 0 tokens</Busy>
        <Busy className="btn sm ai" onError={setErr} disabled={!allKnown && !Object.values(answers).some((v) => v.trim())} run={() => fill(true)}>Word them with AI · 1 AI token</Busy>
      </div>
    </div>
  );
}

/* ───────────── where ───────────── */
function Where({ page, onChange }) {
  const { toast } = useHub();
  const [mode, setMode] = useState(page.area_mode);
  const [area, setArea] = useState(page.area);
  const [km, setKm] = useState(page.radius_km);
  const [found, setFound] = useState(null);
  useEffect(() => { setMode(page.area_mode); setArea(page.area); setKm(page.radius_km); }, [page.area_mode, page.area, page.radius_km]);
  const save = async (m = mode, a = area, k = km) => onChange(await api("/prospects/area", { method: "PUT", body: { mode: m, area: a, radius_km: k } }));
  return (
    <div className="panel stack">
      <span className="label">2 · Where</span>
      <div className="pr-seg" role="group" aria-label="Where to look">
        {[["city", page.city ? `In ${page.city}` : "My city"], ["area", "In an area"], ["radius", "Near my business"]].map(([k, l]) => (
          <button key={k} type="button" className={mode === k ? "on" : ""} aria-pressed={mode === k}
            onClick={() => { setMode(k); if (k !== "area") save(k, area, km).catch(() => {}); }}>{l}</button>
        ))}
      </div>
      {mode === "city" && !page.city && <span className="small muted">Add your city in the Knowledge Hub, or pick “In an area”.</span>}
      {mode === "area" && (
        <div className="pr-add">
          <input className="input" value={area} maxLength={60} placeholder="Like: Bhiwandi, or Andheri East" aria-label="Area" onChange={(e) => setArea(e.target.value)} />
          <Busy className="btn sm" disabled={area.trim().length < 2 || (area === page.area && page.area_mode === "area")} run={async () => { await save(); toast("Saved"); }}>Save</Busy>
        </div>
      )}
      {mode === "radius" && (
        <div className="stack">
          <div className="row">
            <span className="small">Within</span>
            <select className="select pr-km" value={km} aria-label="Distance" onChange={(e) => { const k = Number(e.target.value); setKm(k); save("radius", area, k).catch(() => {}); }}>
              {[5, 10, 20, 30, 50].map((k) => <option key={k} value={k}>{k} km</option>)}
            </select>
            <span className="small">of your business</span>
          </div>
          {page.home_set ? (
            <span className="small muted">Your business is picked on Google Maps. <button type="button" className="pr-link" onClick={async () => onChange(await api("/prospects/home", { method: "DELETE" }))}>Pick again</button></span>
          ) : page.places_ready ? (
            <div className="stack">
              <span className="small muted">First find your own business on Google Maps, so the hub knows where “near” is. Only its Google ID is kept.</span>
              <Busy className="btn sm pr-fit" run={async () => setFound((await api("/prospects/home/find", { method: "POST" })).results)}>Find my business on Google Maps</Busy>
              {found && (found.length ? (
                <div className="pr-rows">
                  {found.map((f) => (
                    <button key={f.place_id} type="button" className="pr-pick" onClick={async () => { onChange(await api("/prospects/home", { method: "PUT", body: { place_id: f.place_id } })); setFound(null); toast("Your business is picked"); }}>
                      <b>{f.name}</b><span className="small muted">{f.address}</span>
                    </button>
                  ))}
                  <span className="pr-attr">Results from <GoogleMark /></span>
                </div>
              ) : <span className="small muted">Google Maps didn't find your business by its name and city. Use “In my city” or “In an area”.</span>)}
            </div>
          ) : null}
        </div>
      )}
    </div>
  );
}

/* ───────────── search results ───────────── */
function Results({ res, onKept }) {
  const { toast } = useHub();
  const [ticked, setTicked] = useState({});
  const [rows, setRows] = useState(res.results);
  const [outWhy, setOutWhy] = useState({});
  useEffect(() => { setRows(res.results); setTicked({}); setOutWhy({}); }, [res]);
  const pick = rows.filter((r) => ticked[r.place_id]);
  const left = Object.entries(res.skipped || {}).filter(([, n]) => n).map(([k, n]) => `${n} ${LEFT_OUT[k]}`);
  const keep = async () => {
    const r = await api("/prospects/keep", { method: "POST", body: { search_id: res.search_id, items: pick.map((x) => ({ place_id: x.place_id, kind: x.kind })) } });
    const ids = r.prospect_ids || {};
    setRows(rows.map((x) => (ids[x.place_id] ? { ...x, kept: true, prospect_id: ids[x.place_id], status_label: "Kept" } : x)));
    setOutWhy({ ...outWhy, ...Object.fromEntries((r.left_out || []).map((o) => [o.place_id, o.why])) });
    setTicked({});
    toast(`Kept ${plural(r.kept, "business", "businesses")}${r.left_out?.length ? ` · ${r.left_out.length} left out` : ""}`);
    onKept(r);
  };
  const keepBar = () => (
    <div className="pr-keepbar">
      <Busy className="btn primary" disabled={!pick.length} run={keep}>{pick.length ? `Keep ${pick.length}` : "Tick the ones worth contacting"}</Busy>
      <span className="small muted">Kept businesses go to My list, where the hub writes a first message for each.</span>
    </div>
  );
  return (
    <div className="panel stack">
      <div className="row between">
        <span className="label">{plural(res.new, "new business", "new businesses")} · {res.area}</span>
        <span className="pr-attr">Results from <GoogleMark /></span>
      </div>
      {left.length > 0 && <span className="small muted">Left out: {left.join(", ")}.</span>}
      {res.note && <div className="notice small">{res.note}</div>}
      {rows.length > 6 && keepBar()}
      {rows.length ? (
        <div className="pr-rows">
          {rows.map((x) => {
            const why = outWhy[x.place_id];
            const can = !x.kept && !why;
            return (
              <label key={x.place_id} className={`pr-row ${can ? "" : "done"}`}>
                <input type="checkbox" disabled={!can} checked={!!ticked[x.place_id]} aria-label={`Keep ${x.name}`}
                  onChange={(e) => setTicked({ ...ticked, [x.place_id]: e.target.checked })} />
                <span className="pr-who">
                  <b>{x.name}</b>
                  <span className="small muted">{[x.type, x.address].filter(Boolean).join(" · ")}</span>
                  <span className="small">
                    <span className="pr-kind">{x.kind}</span>
                    {x.distance_km != null && <span className="muted"> · {x.distance_km} km away</span>}
                    {x.maps_url && <> · <a href={x.maps_url} target="_blank" rel="noopener noreferrer">Open in Google Maps</a></>}
                  </span>
                </span>
                {x.kept ? <Link className="pr-state" to={`/prospects/${x.prospect_id}`}><Pill kind="on">{x.status_label || "Kept"}</Pill></Link>
                  : why ? <span className="pr-state small muted">{why}</span> : null}
              </label>
            );
          })}
        </div>
      ) : <span className="small muted">Nothing new here. Try another kind of business or a wider area.</span>}
      {keepBar()}
    </div>
  );
}

function Find({ page, setPage, onKept }) {
  const [res, setRes] = useState(null);
  const [err, setErr] = useState("");
  const s = page.limits.searches;
  return (
    <div className="stack">
      <Kinds page={page} onChange={setPage} />
      <Where page={page} onChange={setPage} />
      <div className="panel stack">
        <span className="label">3 · Find</span>
        {page.places_ready ? (
          <>
            <div className="row">
              <Busy className="btn primary" onError={setErr} disabled={!page.kinds.length || !s.left} run={async () => {
                const r = await api("/prospects/search", { method: "POST", body: {} });
                setRes(r);
                setPage({ ...page, limits: r.limits });
              }}>Find businesses</Busy>
              <span className="small">{s.left} of {s.limit} searches left this month</span>
            </div>
            <span className="small muted">One search looks up each kind on your list (up to 20 businesses each). Closed businesses, your own and people already in your contacts are left out.</span>
          </>
        ) : (
          <div className="notice small"><span>{page.no_key}</span></div>
        )}
        <FormError msg={err} />
      </div>
      {res && <Results res={res} onKept={onKept} />}
    </div>
  );
}

/* ───────────── my list ───────────── */
function UploadBox({ onDone }) {
  const { toast } = useHub();
  const [err, setErr] = useState("");
  const [busy, setBusy] = useState(false);
  const pick = async (e) => {
    const file = e.target.files?.[0];
    e.target.value = "";
    if (!file) return;
    setBusy(true); setErr("");
    try {
      const fd = new FormData();
      fd.append("file", file);
      const r = await upload("/prospects/upload", fd);
      const sk = r.skipped || {};
      const parts = [sk.known && `${sk.known} already in your contacts`, sk.duplicate && `${sk.duplicate} already on your list`,
        sk.no_contact && `${sk.no_contact} without a number or email`, sk.blocked && `${sk.blocked} marked Do not contact`,
        sk.over_limit && `${sk.over_limit} over this month's limit`].filter(Boolean);
      toast(`${plural(r.added, "business", "businesses")} added${parts.length ? ` · left out: ${parts.join(", ")}` : ""}`);
      onDone();
    } catch (x) { if (!x.handled) setErr(x.message); }
    finally { setBusy(false); }
  };
  return (
    <div className="panel stack">
      <span className="label">Upload your own list</span>
      <span className="small muted">An exhibition list, an association directory or leads you bought — Excel (.xlsx) or CSV. First row: <b>name</b> and <b>phone</b> or <b>email</b>; <b>website</b>, <b>city</b> and <b>kind</b> are read when there. People already in your contacts or on this list are left out.</span>
      <label className={`btn sm pr-fit ${busy ? "is-busy" : ""}`}>{busy ? "Reading…" : "Choose a file"}<input type="file" accept=".xlsx,.csv" hidden onChange={pick} disabled={busy} /></label>
      <FormError msg={err} />
    </div>
  );
}

function MyList({ page, setPage }) {
  const { toast, refresh } = useHub();
  const nav = useNavigate();
  const [status, setStatus] = useState("");
  const [n, setN] = useState(1);
  const [ticked, setTicked] = useState({});
  const [uploading, setUploading] = useState(false);
  const [data, reload] = useLoad(`/prospects/list?page=${n}${status ? `&status=${status}` : ""}`);
  const after = async () => { await reload(); setPage(await api("/prospects")); refresh?.(); };
  if (!data) return <Loading />;
  const picks = data.items.filter((p) => ticked[p.id]);
  const canDraft = (p) => ["kept", "drafted"].includes(p.status) && !p.waiting.includes("first");
  const anyGoogle = data.items.some((p) => p.from_google);
  const setStatusOf = async (p, s) => {
    if (s === "dnc" && !window.confirm("Do not contact is permanent. The hub will never write to this business again. Go ahead?")) return;
    await api(`/prospects/${p.id}/status`, { method: "POST", body: { status: s } });
    toast(s === "dnc" ? "Marked Do not contact" : "Marked Not interested");
    await after();
  };
  const sendFirst = (p) => openWhatsApp(async () => {
    const r = await api(`/prospects/${p.id}/whatsapp`, { method: "POST", body: { step: "first" } });
    toast("Marked as sent. Follow-ups come on day 3 and day 7 if they don't reply.");
    await after();
    return r;
  }).catch((e) => { if (!e.handled) toast(e.message, "err"); });
  return (
    <div className="stack">
      <div className="pr-bar">
        <div className="pr-chips pr-filters" role="group" aria-label="Show">
          {FILTERS.map(([k, l]) => {
            const c = k ? data.counts[k] : Object.values(data.counts).reduce((a, b) => a + b, 0);
            return <button key={k || "all"} type="button" className={`pr-chip f ${status === k ? "on" : ""}`} aria-pressed={status === k}
              onClick={() => { setStatus(k); setN(1); setTicked({}); }}>{l} · {c}</button>;
          })}
        </div>
        <button className="btn sm ghost" type="button" onClick={() => setUploading((x) => !x)}>Upload your own list</button>
      </div>
      {uploading && <UploadBox onDone={async () => { setUploading(false); await after(); }} />}
      {data.items.length > 0 && (
        <div className="pr-draftbar">
          <Busy className="btn primary ai" disabled={!picks.length} run={async () => {
            const r = await api("/prospects/draft", { method: "POST", body: { ids: picks.map((p) => p.id) } });
            setTicked({});
            toast(`${plural(r.drafted, "first message")} written${r.token_used ? " · 1 AI token" : " · no token used"}. They're in your Send list too.`);
            await after();
          }}>{picks.length ? `Write first messages (${picks.length})` : "Write first messages"}</Busy>
          <span className="small muted">Tick up to 5 kept businesses: 1 AI token writes a WhatsApp message, an email and a call opener for each.</span>
        </div>
      )}
      {data.items.length ? (
        <div className="pr-rows">
          {data.items.map((p) => {
            const tickable = canDraft(p);
            return (
              <div key={p.id} className="pr-row">
                <input type="checkbox" disabled={!tickable || (!ticked[p.id] && picks.length >= 5)} checked={!!ticked[p.id]}
                  aria-label={`Write a first message for ${p.name || p.kind}`} onChange={(e) => setTicked({ ...ticked, [p.id]: e.target.checked })} />
                <button type="button" className="pr-who pr-open" onClick={() => nav(`/prospects/${p.id}`)}>
                  <b>{p.name || (p.details_note ? p.kind : "—")}</b>
                  <span className="small muted">{p.from_google ? (p.address || p.details_note) : [p.city, p.phone && localPhone(p.phone), !p.phone && p.email].filter(Boolean).join(" · ")}</span>
                  <span className="small"><span className="pr-kind">{p.kind}</span>{p.from_google ? <span className="muted"> · Google Maps</span> : <span className="muted"> · your list</span>}</span>
                </button>
                <div className="pr-acts">
                  <Pill kind={STATUS_KIND[p.status]}>{p.status_label}</Pill>
                  {p.waiting.includes("first") && <button className="btn sm primary" type="button" onClick={() => sendFirst(p)}>Send on WhatsApp</button>}
                  {p.waiting.length > 0 && !p.waiting.includes("first") && <Link className="btn sm" to={`/prospects/${p.id}`}>Follow-up waiting</Link>}
                  {["drafted", "contacted"].includes(p.status) && <Link className="btn sm ghost" to={`/prospects/${p.id}#email`}>Email</Link>}
                  {!["not_interested", "dnc", "deal"].includes(p.status) && (
                    <details className="pr-more">
                      <summary className="btn sm ghost" aria-label="More">⋯</summary>
                      <div className="pr-menu">
                        <button type="button" onClick={() => setStatusOf(p, "not_interested")}>Not interested</button>
                        <button type="button" onClick={() => setStatusOf(p, "dnc")}>Do not contact</button>
                      </div>
                    </details>
                  )}
                </div>
              </div>
            );
          })}
        </div>
      ) : (
        <div className="empty"><h2>{status ? "Nothing here." : "Your list is empty."}</h2>
          <p className="muted">{status ? "Pick another filter." : "Find businesses on the Find tab and keep the ones worth contacting, or upload your own list."}</p></div>
      )}
      {data.pages > 1 && (
        <div className="row pr-pager">
          <button className="btn sm ghost" type="button" disabled={data.page <= 1} onClick={() => setN(data.page - 1)}>← Previous</button>
          <span className="small">Page {data.page} of {data.pages}</span>
          <button className="btn sm ghost" type="button" disabled={data.page >= data.pages} onClick={() => setN(data.page + 1)}>Next →</button>
        </div>
      )}
      {anyGoogle && <span className="pr-attr">Business details from <GoogleMark /></span>}
    </div>
  );
}

function ListPage() {
  const [page, setPage] = useState(null);
  const [tab, setTab] = useState("find");
  const { toast } = useHub();
  useEffect(() => { api("/prospects").then((p) => { setPage(p); if (p.total) setTab("list"); }).catch((e) => { if (!e.handled) toast(e.message, "err"); }); }, []);  // eslint-disable-line react-hooks/exhaustive-deps
  if (!page) return <Sheet {...head} title={<>Find businesses that could <em>buy</em> from you.</>}><Loading /></Sheet>;
  const l = page.limits;
  const c = page.counts;
  return (
    <Sheet {...head}
      title={page.total ? <>{plural(page.total, "business", "businesses")} on your list. You decide who to <em>contact</em>.</> : <>Find businesses that could <em>buy</em> from you.</>}
      sub="Look up businesses that fit on Google Maps, or upload your own list. The hub writes a first message for each. You send it from your own WhatsApp — nothing goes out by itself.">
      <div className="statgrid">
        <Stat label="Searches left" value={`${l.searches.left} of ${l.searches.limit}`} note="This month" />
        <Stat label="Can keep" value={`${l.kept.left} of ${l.kept.limit}`} note="Businesses this month" />
        <Stat label="First messages left" value={`${l.first_messages.left} of ${l.first_messages.limit}`} note="Today" />
        <Stat label="Contacted · replied" value={`${c.contacted + c.replied + c.deal} · ${c.replied + c.deal}`} note={c.deal ? `${plural(c.deal, "deal")} so far` : "From your list"} />
      </div>
      <Rule text={page.line} />
      <Tabs tabs={[["find", "Find"], ["list", `My list${page.total ? ` · ${page.total}` : ""}`]]} value={tab} onChange={setTab} />
      {tab === "find" ? <Find page={page} setPage={setPage} onKept={async () => setPage(await api("/prospects"))} />
        : <MyList page={page} setPage={setPage} />}
      {!page.role_on && <p className="small muted">The outreach writer is switched off, so no follow-ups are written. Switch it on in <Link to="/agents">Automations</Link>.</p>}
    </Sheet>
  );
}

/* ───────────── one business ───────────── */
function Message({ m, p, onDone }) {
  const { toast, refresh } = useHub();
  const [text, setText] = useState(m.text);
  const pending = m.status === "pending";
  const send = () => openWhatsApp(async () => {
    const r = await api(`/prospects/${p.id}/whatsapp`, { method: "POST", body: { step: m.step, text: text.trim() !== m.text ? text.trim() : null } });
    toast(m.step === "first" ? "Marked as sent. Follow-ups come on day 3 and day 7 if they don't reply." : "Marked as sent");
    onDone(r.prospect);
    refresh?.();
    return r;
  }).catch((e) => { if (!e.handled) toast(e.message, "err"); });
  return (
    <div className="pr-msg stack">
      <div className="row between">
        <b className="small">{m.label}</b>
        <span className="small muted">{pending ? `Written ${ago(m.created_at)}` : m.status === "sent" ? `Sent ${ago(m.decided_at)} from your WhatsApp` : m.note || m.status}</span>
      </div>
      {pending ? (
        <>
          <textarea className="textarea" rows={5} maxLength={900} value={text} aria-label={m.label} onChange={(e) => setText(e.target.value)} />
          <div className="row">
            <button className="btn primary" type="button" disabled={text.trim().length < 2} onClick={send}>Send from my WhatsApp</button>
            <span className="small muted">Opens your own WhatsApp with this typed. You press send.</span>
          </div>
        </>
      ) : <p className="small pr-sent">{m.text}</p>}
    </div>
  );
}

function DealBox({ p, onClose }) {
  const nav = useNavigate();
  const { toast } = useHub();
  const [f, setF] = useState({ name: p.name || "", phone: p.phone ? localPhone(p.phone) : "", email: p.email || "", value: "" });
  const [err, setErr] = useState("");
  const set = (k) => (e) => setF({ ...f, [k]: e.target.value });
  return (
    <Modal onClose={onClose}>
      <span className="kicker">Find new customers</span>
      <h2>Make this a <em>deal</em>.</h2>
      <div className="stack">
        <span className="small muted">Adds them to your contacts and a deal at the first stage of your pipeline. Check the name and number — they're your customer's now.</span>
        <Field label="Business or person"><input className="input" value={f.name} maxLength={80} onChange={set("name")} autoFocus /></Field>
        <Field label="Mobile"><input className="input" inputMode="tel" value={f.phone} maxLength={24} onChange={set("phone")} /></Field>
        <Field label="Email (optional)"><input className="input" type="email" value={f.email} maxLength={254} onChange={set("email")} /></Field>
        <Field label="Deal value ₹ (optional)"><input className="input money" type="number" min="0" inputMode="decimal" value={f.value} onChange={set("value")} /></Field>
        <FormError msg={err} />
        <div className="row">
          <Busy className="btn primary" onError={setErr} disabled={f.name.trim().length < 2 || (!f.phone.trim() && !f.email.trim())} run={async () => {
            const r = await api(`/prospects/${p.id}/deal`, { method: "POST", body: { name: f.name, phone: f.phone, email: f.email, value: f.value === "" ? null : Number(f.value) } });
            toast("Added to your pipeline");
            nav(`/pipeline?deal=${r.lead_id}`);
          }}>Add the deal</Busy>
          <button className="btn ghost" type="button" onClick={onClose}>Cancel</button>
        </div>
      </div>
    </Modal>
  );
}

function ProspectPage({ id }) {
  const { toast } = useHub();
  const nav = useNavigate();
  const [p, setP] = useState(null);
  const [notes, setNotes] = useState("");
  const [reply, setReply] = useState("");
  const [answer, setAnswer] = useState("");
  const [deal, setDeal] = useState(false);
  const [mail, setMail] = useState(null);
  const fill = (x) => { setP(x); setNotes(x.notes || ""); setAnswer(x.reply_help?.answer || ""); if (x.draft) setMail({ subject: x.draft.email_subject, body: x.draft.email_body }); };
  useEffect(() => { api(`/prospects/${id}`).then(fill).catch((e) => { if (!e.handled) toast(e.message, "err"); nav("/prospects"); }); }, [id]);  // eslint-disable-line react-hooks/exhaustive-deps
  if (!p) return <Sheet {...head} title={<>A business on your list</>}><Loading /></Sheet>;
  const closed = ["not_interested", "dnc", "deal"].includes(p.status);
  const status = async (s) => {
    if (s === "dnc" && !window.confirm("Do not contact is permanent. The hub will never write to this business again. Go ahead?")) return;
    fill(await api(`/prospects/${id}/status`, { method: "POST", body: { status: s } }));
    toast(s === "dnc" ? "Marked Do not contact" : s === "replied" ? "Marked Replied — follow-ups stopped" : "Marked Not interested");
  };
  const canWrite = ["kept", "drafted"].includes(p.status) && !p.waiting.includes("first");
  return (
    <Sheet {...head} title={<>{p.name || p.kind}</>}
      sub={[p.kind, p.status_label, p.from_google ? "found on Google Maps" : "from your own list", p.kept_at && `kept ${ago(p.kept_at)}`].filter(Boolean).join(" · ")}
      actions={<Link className="btn sm ghost" to="/prospects">← My list</Link>}>
      <Rule text={p.line} />
      <div className="pr-side">
        <div className="stack">
          <div className="panel stack">
            <div className="row between"><span className="label">Details</span><Pill kind={STATUS_KIND[p.status]}>{p.status_label}</Pill></div>
            {p.details_note ? <span className="small muted">{p.details_note}</span> : (
              <dl className="pr-dl">
                {p.address && <><dt>Address</dt><dd>{p.address}</dd></>}
                {p.city && <><dt>City</dt><dd>{p.city}</dd></>}
                {p.phone && <><dt>Phone</dt><dd><a href={`tel:${p.phone.replace(/\s/g, "")}`}>{localPhone(p.phone)}</a></dd></>}
                {p.email && <><dt>Email</dt><dd>{p.email}</dd></>}
                {p.website && <><dt>Website</dt><dd><a href={p.website} target="_blank" rel="noopener noreferrer">{p.website.replace(/^https?:\/\//, "").replace(/\/$/, "")}</a></dd></>}
              </dl>
            )}
            {p.closed && <div className="notice small">Google Maps shows this business as closed.</div>}
            {p.maps_url && <a className="btn sm ghost pr-fit" href={p.maps_url} target="_blank" rel="noopener noreferrer">Open in Google Maps</a>}
            {p.from_google && <span className="pr-attr">Details from <GoogleMark /> · shown now, not saved</span>}
          </div>

          <div className="panel stack">
            <span className="label">WhatsApp</span>
            {(p.messages || []).map((m) => <Message key={m.id} m={m} p={p} onDone={fill} />)}
            {!(p.messages || []).length && <span className="small muted">No message written yet.</span>}
            {canWrite && !closed && (
              <Busy className="btn ai pr-fit" run={async () => { const r = await api("/prospects/draft", { method: "POST", body: { ids: [id] } }); if (r.items?.[0]) fill({ ...p, ...r.items[0], attribution: p.attribution, line: p.line }); toast(r.token_used ? "Written · 1 AI token" : "Written without AI · no token used"); }}>
                {(p.messages || []).length ? "Write it again" : "Write the first message"} · 1 AI token
              </Busy>
            )}
            {p.status === "contacted" && <span className="small muted">{p.next_followup_on ? `A follow-up is written on ${new Date(`${p.next_followup_on}T00:00:00`).toLocaleDateString("en-IN", { day: "numeric", month: "short" })} if they don't reply.` : "No more follow-ups planned."}</span>}
          </div>

          {p.draft && mail && (
            <div className="panel stack" id="email">
              <span className="label">Email</span>
              <Field label="Subject"><input className="input" value={mail.subject} maxLength={160} onChange={(e) => setMail({ ...mail, subject: e.target.value })} /></Field>
              <Field label="Message"><textarea className="textarea" rows={8} value={mail.body} onChange={(e) => setMail({ ...mail, body: e.target.value })} /></Field>
              <div className="row">
                <a className="btn" href={mailto(p.email, mail.subject, mail.body)}>Open in my email app</a>
                {!closed && ["kept", "drafted"].includes(p.status) && (
                  <Busy className="btn ghost" run={async () => { fill(await api(`/prospects/${id}/email-sent`, { method: "POST" })); toast("Marked as contacted"); }}>I sent the email</Busy>
                )}
              </div>
              <span className="small muted">The hub never sends these emails. They go from your own email{p.email ? "" : " — add their address from their website"}.</span>
            </div>
          )}

          {p.draft?.call_opener && (
            <div className="panel stack">
              <div className="row between"><span className="label">If you call</span><Copy text={p.draft.call_opener} /></div>
              <p className="small">{p.draft.call_opener}</p>
              {p.phone && <a className="btn sm ghost pr-fit" href={`tel:${p.phone.replace(/\s/g, "")}`}>Call {localPhone(p.phone)}</a>}
            </div>
          )}
        </div>

        <div className="stack">
          {p.status === "deal" ? (
            <div className="panel active stack">
              <span className="label">Became a deal</span>
              <span className="small">It's in your pipeline at the first stage.</span>
              <Link className="btn primary pr-fit" to={`/pipeline?deal=${p.lead_id}`}>Open the deal</Link>
            </div>
          ) : p.status !== "dnc" && (
            <div className="panel stack">
              <span className="label">They replied?</span>
              <span className="small muted">Paste their reply. You get a suggested answer, and the follow-ups stop.</span>
              <textarea className="textarea" rows={4} maxLength={3000} value={reply} aria-label="Their reply" placeholder="Paste their WhatsApp or email reply here" onChange={(e) => setReply(e.target.value)} />
              <Busy className="btn ai pr-fit" disabled={reply.trim().length < 2} run={async () => { const r = await api(`/prospects/${id}/reply`, { method: "POST", body: { text: reply.trim() } }); fill(r); setReply(""); toast(r.token_used ? "Answer suggested · 1 AI token" : "Answer suggested without AI · no token used"); }}>Suggest an answer · 1 AI token</Busy>
              {p.reply_help && (
                <div className="stack">
                  <span className="small muted">They wrote: “{p.reply_help.reply.slice(0, 300)}”</span>
                  {p.reply_help.said_no && <div className="notice small">It sounds like they said no. Mark them Not interested and the hub won't write to them again.</div>}
                  <textarea className="textarea prompt" rows={4} maxLength={900} value={answer} aria-label="Suggested answer" onChange={(e) => setAnswer(e.target.value)} />
                  <div className="row">
                    {p.phone && <a className="btn sm" href={waLink(answer, p.phone.replace(/\D/g, "").replace(/^0/, ""))} target="_blank" rel="noopener">Send on WhatsApp</a>}
                    <Copy text={answer} />
                  </div>
                </div>
              )}
              {["contacted", "replied", "drafted"].includes(p.status) && <button className="btn primary pr-fit" type="button" onClick={() => setDeal(true)}>Make this a deal</button>}
            </div>
          )}

          <div className="panel stack">
            <span className="label">Notes</span>
            <textarea className="textarea" rows={3} maxLength={2000} value={notes} aria-label="Notes" placeholder="Who you spoke to, what they need…" onChange={(e) => setNotes(e.target.value)} />
            {notes !== (p.notes || "") && <Busy className="btn sm pr-fit" run={async () => { fill(await api(`/prospects/${id}`, { method: "PATCH", body: { notes } })); toast("Saved"); }}>Save notes</Busy>}
          </div>

          {!closed && (
            <div className="panel stack">
              <span className="label">Mark as</span>
              <div className="row">
                {["kept", "drafted", "contacted"].includes(p.status) && <button className="btn sm ghost" type="button" onClick={() => status("replied")}>Replied</button>}
                <button className="btn sm ghost" type="button" onClick={() => status("not_interested")}>Not interested</button>
                <button className="btn sm ghost pr-dnc" type="button" onClick={() => status("dnc")}>Do not contact</button>
              </div>
              <span className="small muted">Do not contact is permanent: the hub never writes to this business again, even if it shows up in a later search or list.</span>
            </div>
          )}
        </div>
      </div>
      {deal && <DealBox p={p} onClose={() => setDeal(false)} />}
    </Sheet>
  );
}

export default function Prospects() {
  const { id } = useParams();
  return id ? <ProspectPage key={id} id={id} /> : <ListPage />;
}
