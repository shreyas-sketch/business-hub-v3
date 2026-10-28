import React, { useState } from "react";
import { ago, api } from "../api.js";
import { Busy, Field, FormError, LoadError, Sheet, useHub, useLoad } from "../ui.jsx";
import { Empty, Loading, Pill } from "../v2.jsx";
import "./competitors.css";

/* Competitor Watch (v2.2, Membership): what changed on competitors' websites and Google Maps ratings, every Monday.
   The report only says what the hub saw change. Social pages are never read: "See their running ads" opens Meta's public
   Ad Library for the owner to look at themselves. */

const HEAD = { kicker: "Scale · Competitor Watch", id: "SC-14", pillar: "Scale" };
const SUB = "Every Monday the hub reads your competitors' websites, compares them with the last check, and tells you what changed. Only what it saw — no guessing.";
const weekOf = (iso) => (iso ? new Date(`${iso}T00:00:00`).toLocaleDateString("en-IN", { day: "numeric", month: "short" }) : "");
const host = (url) => { try { return new URL(url).host.replace(/^www\./, ""); } catch { return url || ""; } };
const plural = (n, w) => `${n} ${w}${n === 1 ? "" : "s"}`;
const signed = (n, digits = 0) => (n > 0 ? `+${n.toFixed(digits)}` : n < 0 ? `−${Math.abs(n).toFixed(digits)}` : "no change");
const changed = (it) => !!(it.website_changes?.any || it.google?.rating_change || it.google?.count_change);

/* ───────────── one competitor in a report ───────────── */
function Lines({ title, items, sign }) {
  if (!items?.length) return null;
  return (
    <div className="cw-block">
      <span className="label">{title}</span>
      <ul className="cw-lines">{items.map((x, i) => <li key={i}><span className={`cw-sign ${sign === "+" ? "add" : "del"}`}>{sign}</span><span>{x.line}<small className="muted"> · {x.page}</small></span></li>)}</ul>
    </div>
  );
}

function Changes({ w }) {
  if (!w) return null;
  const textCount = (w.lines_added?.length || 0) + (w.lines_removed?.length || 0) + (w.more_added || 0) + (w.more_removed || 0);
  return (
    <div className="stack" style={{ gap: 14 }}>
      {w.prices_changed?.length > 0 && (
        <div className="cw-block">
          <span className="label">Prices changed</span>
          {w.prices_changed.map((x, i) => (
            <div key={i} className="cw-price">
              <span>{x.item}<small className="muted"> · {x.page}</small></span>
              <span className="money cw-nowrap"><s>{x.before}</s> → {x.after}</span>
            </div>
          ))}
        </div>
      )}
      {w.offers_new?.length > 0 && (
        <div className="cw-block">
          <span className="label">New offer lines</span>
          {w.offers_new.map((x, i) => <blockquote key={i} className="cw-quote">“{x.line}”<small className="muted"> · {x.page}</small></blockquote>)}
        </div>
      )}
      {(w.prices_added?.length > 0 || w.prices_removed?.length > 0) && (
        <div className="cw-block">
          <span className="label">Prices shown or taken down</span>
          {w.prices_added?.map((x, i) => <div key={`a${i}`} className="cw-price"><span>New: {x.item}<small className="muted"> · {x.page}</small></span><span className="money cw-nowrap">{x.price}</span></div>)}
          {w.prices_removed?.map((x, i) => <div key={`r${i}`} className="cw-price"><span>Gone: {x.item}<small className="muted"> · {x.page}</small></span><span className="muted cw-nowrap"><s>{x.price}</s></span></div>)}
        </div>
      )}
      {(w.pages_added?.length > 0 || w.pages_removed?.length > 0) && (
        <div className="cw-block">
          <span className="label">Pages</span>
          <ul className="read-list">
            {w.pages_added?.map((p) => <li key={`a${p.path}`}>New page: <b>{p.path}</b>{p.title ? <small className="muted"> · {p.title}</small> : null}</li>)}
            {w.pages_removed?.map((p) => <li key={`r${p.path}`}>Not found this time: <b>{p.path}</b>{p.title ? <small className="muted"> · {p.title}</small> : null}</li>)}
          </ul>
        </div>
      )}
      {textCount > 0 && (
        <details className="cw-more">
          <summary>Other text that changed ({textCount} {textCount === 1 ? "line" : "lines"})</summary>
          <div className="stack" style={{ gap: 12, paddingTop: 10 }}>
            <Lines title="Added" items={w.lines_added} sign="+" />
            {w.more_added > 0 && <span className="small muted">…and {w.more_added} more added</span>}
            <Lines title="Taken out" items={w.lines_removed} sign="−" />
            {w.more_removed > 0 && <span className="small muted">…and {w.more_removed} more taken out</span>}
          </div>
        </details>
      )}
      {w.skipped?.length > 0 && <span className="small muted">Not read, because their robots.txt asks not to: {w.skipped.join(", ")}</span>}
      {w.kept?.length > 0 && <span className="small muted">Not read this time, kept as last seen: {w.kept.join(", ")}</span>}
    </div>
  );
}

function Google({ it }) {
  if (it.google_note) return <span className="small muted">{it.google_note}</span>;
  const g = it.google;
  if (!g) return null;
  return (
    <div className="cw-google">
      <span className="label">Google Maps</span>
      <span><b>{g.rating ?? "–"}</b> rating{g.rating_change ? <span className={g.rating_change < 0 ? "cw-down" : "cw-up"}> ({signed(g.rating_change, 1)})</span> : ""}</span>
      <span><b>{g.count ?? "–"}</b> reviews{g.count_change ? <span className="cw-up"> ({signed(g.count_change)})</span> : ""}</span>
      {g.rating_change == null && g.count_change == null && <span className="small muted">First reading — the change shows next time.</span>}
    </div>
  );
}

function AdsLink({ href }) {
  return (
    <div className="cw-ads">
      <a className="btn sm" href={href} target="_blank" rel="noopener noreferrer">See their running ads</a>
      <span className="small muted">Opens Meta's public Ad Library — the hub doesn't read it for you.</span>
    </div>
  );
}

function ReportItem({ it }) {
  const w = it.website_changes;
  const looked = !!(w || it.google);
  const state = !looked ? ["Ads link only", "off"] : it.first_check ? ["First look", ""] : changed(it) ? ["Changed", "on"] : ["No change", "off"];
  return (
    <div className={`cw-item ${changed(it) ? "is-changed" : ""}`}>
      <div className="cw-item-head">
        <div style={{ minWidth: 0 }}>
          <h3>{it.name}</h3>
          {it.website && <a className="small" href={it.website} target="_blank" rel="noopener noreferrer">{host(it.website)}</a>}
        </div>
        <Pill kind={state[1]}>{state[0]}</Pill>
      </div>
      {it.errors?.map((e, i) => <p key={i} className="small cw-err">{e}</p>)}
      {w && w.first_check && <p className="small muted">Read {plural(w.checked.length, "page")}: {w.checked.join(", ")}. Changes show from the next check.</p>}
      {w && !w.first_check && !w.any && <p className="small muted">Nothing changed on the {plural(w.checked.length, "page")} the hub read.</p>}
      {w && !w.first_check && !w.any && w.kept?.length > 0 && <p className="small muted">Not read this time, kept as last seen: {w.kept.join(", ")}</p>}
      {w && !w.first_check && w.any && <Changes w={w} />}
      {!it.website && !it.google && !it.google_note && <p className="small muted">No website added — only the ads link.</p>}
      <Google it={it} />
      <AdsLink href={it.ad_library_link} />
    </div>
  );
}

function Report({ r }) {
  const sorted = [...r.items].sort((a, b) => Number(changed(b)) - Number(changed(a)));
  return (
    <div className="stack">
      {r.summary ? (
        <div className="cw-summary">
          <span className="label">What it could mean for you</span>
          <p>{r.summary}</p>
          <span className="small muted">Written by AI from the changes below — nothing else.</span>
        </div>
      ) : r.note ? <div className="cw-note">{r.note}</div> : null}
      {sorted.map((it) => <ReportItem key={it.competitor_id} it={it} />)}
    </div>
  );
}

/* ───────────── adding and editing competitors ───────────── */
function GooglePick({ value, onPick }) {
  const [q, setQ] = useState("");
  const [found, setFound] = useState(null);
  const [err, setErr] = useState("");
  if (value.google_place_id) {
    return (
      <div className="cw-picked">
        <span className="small">Google Maps: <b>{value.google_name || "listing picked"}</b>{value.google_address ? ` · ${value.google_address}` : ""}</span>
        <button type="button" className="btn sm ghost" onClick={() => onPick({ google_place_id: "", google_name: "", google_address: "" })}>Change</button>
      </div>
    );
  }
  return (
    <div className="stack" style={{ gap: 8 }}>
      <div className="cw-search">
        <input className="input" value={q} maxLength={120} placeholder="Their name and city" onChange={(e) => setQ(e.target.value)} />
        <Busy className="btn sm" disabled={q.trim().length < 2} onError={setErr} run={async () => setFound((await api(`/competitors/google-search?q=${encodeURIComponent(q.trim())}`)).results)}>Find on Google Maps</Busy>
      </div>
      <FormError msg={err} />
      {found && !found.length && <span className="small muted">No match on Google Maps. Try the name as it shows there.</span>}
      {found?.map((p) => (
        <button type="button" key={p.place_id} className="cw-match" onClick={() => onPick({ google_place_id: p.place_id, google_name: p.name, google_address: p.address })}>
          <b>{p.name}</b>
          <span className="small muted">{p.address}{p.rating ? ` · rated ${p.rating} (${p.count ?? 0} reviews)` : ""}</span>
        </button>
      ))}
    </div>
  );
}

const blank = { name: "", website: "", facebook_name: "", notes: "", google_place_id: "", google_name: "", google_address: "" };

function CompetitorForm({ initial, google, onDone, onCancel }) {
  const { toast } = useHub();
  const [f, setF] = useState({ ...blank, ...(initial || {}) });
  const [err, setErr] = useState("");
  const set = (k) => (e) => setF({ ...f, [k]: e.target.value });
  return (
    <div className="stack">
      <div className="grid2">
        <Field label="Their business name"><input className="input" maxLength={80} value={f.name} onChange={set("name")} placeholder="Patel Kitchen Gallery" /></Field>
        <Field label="Their website" hint="Leave empty if they don't have one."><input className="input" maxLength={300} inputMode="url" value={f.website} onChange={set("website")} placeholder="www.theirbusiness.in" /></Field>
        <Field label="Their Facebook page name" hint="For the link to their running ads. Empty uses the business name.">
          <input className="input" maxLength={80} value={f.facebook_name} onChange={set("facebook_name")} />
        </Field>
        <Field label="Notes (only for you)"><input className="input" maxLength={500} value={f.notes} onChange={set("notes")} placeholder="Our main rival in Thane West" /></Field>
      </div>
      {google ? (
        <Field label="Their Google Maps listing (optional)" hint="Pick the right one, so the hub can follow their rating and review count.">
          <GooglePick value={f} onPick={(g) => setF({ ...f, ...g })} />
        </Field>
      ) : <span className="small muted">Google Maps ratings need the hub's Google key, which isn't set up yet. Websites and the ads link work without it.</span>}
      <FormError msg={err} />
      <div className="row">
        <Busy className="btn sm primary" disabled={f.name.trim().length < 2} onError={setErr} run={async () => {
          const body = { name: f.name.trim(), website: f.website.trim(), facebook_name: f.facebook_name.trim(), notes: f.notes.trim(),
            google_place_id: f.google_place_id, google_name: f.google_name, google_address: f.google_address };
          await api(initial?.id ? `/competitors/${initial.id}` : "/competitors", { method: initial?.id ? "PATCH" : "POST", body });
          toast(initial?.id ? "Saved" : "Added");
          onDone();
        }}>{initial?.id ? "Save" : "Add competitor"}</Busy>
        {onCancel && <button className="btn sm ghost" onClick={onCancel}>Cancel</button>}
      </div>
    </div>
  );
}

function Competitor({ c, google, onChange }) {
  const { toast } = useHub();
  const [edit, setEdit] = useState(false);
  if (edit) return <div className="cw-row"><CompetitorForm initial={c} google={google} onDone={() => { setEdit(false); onChange(); }} onCancel={() => setEdit(false)} /></div>;
  return (
    <div className="cw-row">
      <div style={{ minWidth: 0 }}>
        <b>{c.name}</b>
        <div className="small muted cw-wrap">
          {[c.website ? host(c.website) : "No website", c.google_place_id ? `Google Maps: ${c.google_name || "listing picked"}` : "", c.last_checked_at ? `checked ${ago(c.last_checked_at)}` : "not checked yet"].filter(Boolean).join(" · ")}
        </div>
        {c.notes && <div className="small cw-wrap">{c.notes}</div>}
      </div>
      <div className="row" style={{ gap: 8 }}>
        <a className="btn sm ghost" href={c.ad_library_link} target="_blank" rel="noopener noreferrer">Ads</a>
        <button className="btn sm ghost" onClick={() => setEdit(true)}>Edit</button>
        <Busy className="btn sm ghost" run={async () => {
          if (!window.confirm(`Stop watching “${c.name}”? Past reports stay.`)) return;
          await api(`/competitors/${c.id}`, { method: "DELETE" });
          toast("Removed");
          onChange();
        }}>Remove</Busy>
      </div>
    </div>
  );
}

/* ───────────── the page ───────────── */
export default function Competitors() {
  const { refresh, toast } = useHub();
  const [d, reload, err] = useLoad("/competitors");
  const [past, reloadPast] = useLoad("/competitors/reports");
  const [adding, setAdding] = useState(false);
  const [checkErr, setCheckErr] = useState("");
  const reloadAll = () => { reload(); reloadPast(); };

  if (!d) return <Sheet {...HEAD} title={<>Watch what your <em>competitors</em> change.</>} sub={SUB}>{err ? <LoadError err={err} /> : <Loading />}</Sheet>;

  const { competitors, latest, google_available: google, max } = d;
  const n = latest ? latest.items.filter(changed).length : 0;
  const title = !latest ? <>Watch what your <em>competitors</em> change.</>
    : n ? <>{n} competitor{n === 1 ? "" : "s"} changed <em>something</em>.</> : <>No changes <em>this week</em>.</>;
  const earlier = (past || []).filter((r) => r.id !== latest?.id);

  return (
    <Sheet {...HEAD} title={title} sub={SUB}>
      {competitors.length > 0 && (
        <div className="panel active">
          <div className="row between">
            <span className="label">{latest ? `Report · week of ${weekOf(latest.week)} · ${latest.source === "weekly" ? "Monday check" : "Check now"} · ${ago(latest.at)}` : "No report yet"}</span>
            <Busy className="btn sm primary ai" disabled={!d.can_check} onError={setCheckErr} run={async () => {
              await api("/competitors/check", { method: "POST" });
              toast("Checked");
              reloadAll();
              refresh();
            }}>Check now</Busy>
          </div>
          <span className="small muted">{d.can_check ? `${d.schedule} on its own. Check now reads each website once a day at most — it can take up to a minute.`
            : "Every competitor has been checked today. Check again tomorrow, or wait for Monday's report."}</span>
          <FormError msg={checkErr} />
          {latest ? <Report r={latest} /> : <Empty>Tap Check now for a first look, or wait for Monday morning.</Empty>}
        </div>
      )}

      <div className="panel">
        <div className="row between">
          <span className="label">Your competitors · {competitors.length} of {max}</span>
          {!adding && competitors.length < max && <button className="btn sm" onClick={() => setAdding(true)}>Add</button>}
        </div>
        {!competitors.length && !adding && (
          <div className="stack" style={{ gap: 10 }}>
            <p>Add up to {max} competitors your customers compare you with. The hub reads their website every Monday and tells you on WhatsApp what changed: prices, offers, new pages.</p>
            <div><button className="btn primary" onClick={() => setAdding(true)}>Add your first competitor</button></div>
          </div>
        )}
        {adding && <CompetitorForm google={google} onDone={() => { setAdding(false); reload(); }} onCancel={() => setAdding(false)} />}
        <div className="cw-list">{competitors.map((c) => <Competitor key={c.id} c={c} google={google} onChange={reload} />)}</div>
        <p className="small muted">The hub reads only their public website (at most 5 pages, and never pages their robots.txt asks it to skip) and, when set up, their Google Maps rating. It doesn't read Instagram, Facebook or LinkedIn.</p>
      </div>

      {earlier.length > 0 && (
        <div className="panel flat">
          <span className="label">Past reports</span>
          {earlier.map((r) => (
            <details key={r.id} className="cw-past">
              <summary>
                <span>Week of {weekOf(r.week)}</span>
                <span className="small muted">{r.changed ? `${plural(r.changed, "competitor")} changed` : r.note || "No changes"}</span>
              </summary>
              <div style={{ paddingTop: 12 }}><Report r={r} /></div>
            </details>
          ))}
        </div>
      )}
    </Sheet>
  );
}
