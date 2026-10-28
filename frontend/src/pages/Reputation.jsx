import React, { useState } from "react";
import { Link } from "react-router-dom";
import { ago, api, localPhone } from "../api.js";
import { Busy, Copy, Field, FormError, LoadError, Sheet, Switch, canAct, useHub, useLoad } from "../ui.jsx";
import { Empty, Loading, Pill, Tabs } from "../v2.jsx";
import "./reputation.css";

/* Reviews & reputation (v2.2): every review answered. Google reviews come in on their own once the hub's Google key is set
   and the owner picked their listing; any other review is pasted. The hub drafts the reply, the owner approves it, copies
   it and posts it themselves — the hub never posts, never writes a review and never offers anything for one. */

const todayIso = () => new Date().toLocaleDateString("en-CA", { timeZone: "Asia/Kolkata" });
const fmt = (iso) => (iso ? new Date(`${iso}T00:00:00`).toLocaleDateString("en-IN", { day: "numeric", month: "short", year: "numeric" }) : "");
const plural = (n, one, many) => `${n} ${n === 1 ? one : many || `${one}s`}`;

/** Opens WhatsApp in a new tab after the server answers (the blank tab is opened first, so phones don't block it). */
async function openWith(getLink) {
  const w = window.open("", "_blank");
  try {
    const link = await getLink();
    if (w) w.location = link; else window.location.href = link;
  } catch (e) { if (w) w.close(); throw e; }
}

function Stars({ n }) {
  if (!n) return <span className="small muted">No stars</span>;
  return <span className={`rep-stars ${n <= 3 ? "low" : ""}`} aria-label={`${n} out of 5 stars`}>{"★".repeat(n)}<i>{"★".repeat(5 - n)}</i></span>;
}

/* ───────────────────────── Google listing ───────────────────────── */
function Connect({ q0, onDone, onCancel }) {
  const { toast } = useHub();
  const [q, setQ] = useState(q0 || "");
  const [places, setPlaces] = useState(null);
  const [err, setErr] = useState("");
  return (
    <div className="stack">
      <Field label="Your business name and area" hint="As it shows on Google Maps.">
        <input className="input" value={q} maxLength={120} onChange={(e) => setQ(e.target.value)} placeholder="Shree Ganesh Interiors Thane" />
      </Field>
      <div className="row">
        <Busy className="btn primary" disabled={q.trim().length < 2} onError={setErr} run={async () => {
          const r = await api(`/reputation/google-search?q=${encodeURIComponent(q.trim())}`);
          setPlaces(r.places);
        }}>Find my business</Busy>
        {onCancel && <button className="btn ghost" onClick={onCancel}>Cancel</button>}
      </div>
      <FormError msg={err} />
      {places && !places.length && <p className="small muted">Nothing found. Try the name exactly as it shows on Google, with your area.</p>}
      {places?.map((p) => (
        <div key={p.place_id} className="rep-place">
          <div>
            <b>{p.name}</b>
            <div className="small muted">{p.address}</div>
            {p.rating ? <div className="small">Google Maps: {p.rating}★ · {plural(p.count || 0, "review")}</div> : <div className="small muted">No rating on Google Maps yet</div>}
          </div>
          <Busy className="btn sm primary" run={async () => {
            const r = await api("/reputation/settings", { method: "PUT", body: { google_place_id: p.place_id, google_name: p.name, google_address: p.address } });
            toast(r.note || "Connected");
            onDone();
          }}>This is my business</Busy>
        </div>
      ))}
    </div>
  );
}

function GoogleCard({ g, owner, q0, reload }) {
  const [changing, setChanging] = useState(false);
  if (!g.available) {
    return <div className="panel flat"><span className="label">Google</span><p className="small">{g.message}</p></div>;
  }
  if (!g.place_id || changing) {
    return (
      <div className="panel active">
        <span className="label">Connect your Google listing</span>
        {owner ? (
          <>
            <p className="small muted">Pick your business once. Every morning the hub checks it for new reviews and drafts a reply to each for you to approve.</p>
            <Connect q0={q0} onDone={() => { setChanging(false); reload(); }} onCancel={changing ? () => setChanging(false) : null} />
          </>
        ) : <p className="small">Ask the business owner to connect your Google listing. Until then, paste reviews below.</p>}
      </div>
    );
  }
  const t = g.trend || [];
  const first = t.length > 1 ? t[0] : null;
  const moved = first && g.count != null && first.count != null ? g.count - first.count : 0;
  return (
    <div className="panel">
      <div className="row between">
        <span className="label">On Google Maps{g.name ? ` · ${g.name}` : ""}</span>
        {owner && <button className="btn sm ghost" onClick={() => setChanging(true)}>Change</button>}
      </div>
      {g.rating != null ? (
        <div className="rep-google">
          <span className="rep-big">{g.rating}<small>★</small></span>
          <div className="stack" style={{ gap: 4 }}>
            <span>{plural(g.count || 0, "review")}{moved > 0 ? <span className="ok-text"> · {moved} new since {fmt(first.day)}</span> : null}</span>
            {first && first.rating != null && first.rating !== g.rating && (
              <span className="small muted">Was {first.rating}★ on {fmt(first.day)}</span>
            )}
            <span className="small muted">Checked {ago(g.checked_at)}{g.address ? ` · ${g.address}` : ""}</span>
          </div>
        </div>
      ) : <p className="small muted">Your rating shows here after the first check, every morning at 9:30.</p>}
      {g.maps_link && <div><a className="btn sm ghost" href={g.maps_link} target="_blank" rel="noreferrer">Open on Google Maps</a></div>}
    </div>
  );
}

/* ───────────────────────── one review ───────────────────────── */
function Referral({ r, reload }) {
  const { toast } = useHub();
  const [ask, setAsk] = useState(null);
  if (r.referral_status === "sent") return <p className="small ok-text">Referral asked {ago(r.referral_sent_at)}.</p>;
  if (r.referral_status === "skipped") return null;
  const sent = async () => { await api(`/reputation/reviews/${r.id}/referral/sent`, { method: "POST" }); reload(); };
  if (!ask) {
    return (
      <div className="row rep-ref">
        <span className="small">Happy customer — ask them for a referral.</span>
        <Busy className="btn sm" run={async () => setAsk(await api(`/reputation/reviews/${r.id}/referral`, { method: "POST" }))}>Ask for a referral</Busy>
      </div>
    );
  }
  return (
    <div className="stack rep-ref">
      {ask.note && <p className="small">{ask.note}</p>}
      <div className="prompt">{ask.text}</div>
      <div className="row">
        {ask.link ? (
          <a className="btn sm primary" href={ask.link} target="_blank" rel="noreferrer" onClick={() => { sent().catch((e) => toast(e.message, "err")); }}>
            Send to {ask.to_name} on WhatsApp
          </a>
        ) : (
          <>
            <Copy text={ask.text} label="Copy message" className="btn sm primary" />
            <Busy className="btn sm" run={sent}>I've sent it</Busy>
          </>
        )}
        <Busy className="btn sm ghost" run={async () => { await api(`/reputation/reviews/${r.id}/referral/skip`, { method: "POST" }); reload(); }}>Not this one</Busy>
      </div>
    </div>
  );
}

function Reply({ r, reload }) {
  const { toast } = useHub();
  const [text, setText] = useState(r.reply_draft || "");
  const [writing, setWriting] = useState(false);
  const [err, setErr] = useState("");
  const changed = text.trim() !== (r.reply_draft || "").trim();
  const save = () => api(`/reputation/reviews/${r.id}`, { method: "PATCH", body: { reply_draft: text } });
  const draft = (
    <Busy className="btn sm ghost ai" onError={setErr} run={async () => {
      const x = await api(`/reputation/reviews/${r.id}/draft`, { method: "POST" });
      setText(x.reply_draft); reload();
    }}>{r.reply_draft ? "Draft again" : "Draft a reply"}</Busy>
  );

  if (r.reply_status === "replied") {
    return (
      <details className="rep-posted">
        <summary className="small ok-text">Reply posted {ago(r.replied_at)}</summary>
        <div className="prompt">{r.reply_draft}</div>
      </details>
    );
  }
  if (r.reply_status === "approved" && !writing) {
    const google = r.platform === "Google";
    return (
      <div className="stack">
        <span className="label">Your reply · approved</span>
        <div className="prompt">{r.reply_draft}</div>
        <p className="small muted">
          {google ? "Copy it, open the review on Google, and paste it as your reply. Then come back and tap I've posted it."
            : `Copy it, open the review on ${r.platform}, and paste it as your reply. Then come back and tap I've posted it.`}
        </p>
        <div className="row">
          <Copy text={r.reply_draft} label="Copy reply" className="btn sm primary" />
          {r.open_link && <a className="btn sm" href={r.open_link} target="_blank" rel="noreferrer">Open Google</a>}
          <Busy className="btn sm" run={async () => { await api(`/reputation/reviews/${r.id}/replied`, { method: "POST" }); toast("Marked as posted"); reload(); }}>I've posted it</Busy>
          <button className="btn sm ghost" onClick={() => setWriting(true)}>Edit</button>
        </div>
      </div>
    );
  }
  if (r.reply_status === "none" && !writing && !text) {
    return (
      <div className="stack">
        <div className="row">
          <Busy className="btn sm primary ai" onError={setErr} run={async () => {
            const x = await api(`/reputation/reviews/${r.id}/draft`, { method: "POST" });
            setText(x.reply_draft); reload();
          }}>Draft a reply</Busy>
          <button className="btn sm ghost" onClick={() => setWriting(true)}>Write my own</button>
          <span className="small muted">Drafting uses 1 AI token.</span>
        </div>
        <FormError msg={err} />
      </div>
    );
  }
  return (
    <div className="stack">
      <span className="label">Your reply{r.draft_by === "ai" && !changed ? " · drafted for you — read it before you approve" : ""}</span>
      <textarea className="input rep-reply" rows={Math.min(14, Math.max(4, Math.ceil(text.length / 30) + 1))} maxLength={1500} value={text}
        onChange={(e) => setText(e.target.value)}
        placeholder="Thank them by name. If something went wrong, say sorry and give them a number to call." />
      <FormError msg={err} />
      <div className="row">
        <Busy className="btn sm primary" disabled={!text.trim()} onError={setErr} run={async () => {
          if (changed) await save();
          await api(`/reputation/reviews/${r.id}/approve`, { method: "POST" });
          setWriting(false); toast("Approved — now copy it and post it"); reload();
        }}>Approve</Busy>
        {changed && <Busy className="btn sm" onError={setErr} run={async () => { await save(); setWriting(false); toast("Saved"); reload(); }}>Save</Busy>}
        {draft}
        {writing && <button className="btn sm ghost" onClick={() => { setText(r.reply_draft || ""); setWriting(false); }}>Cancel</button>}
      </div>
    </div>
  );
}

function Review({ r, reload }) {
  const { toast } = useHub();
  const [more, setMore] = useState(false);
  const long = (r.text || "").length > 280;
  return (
    <div className={`panel stack rep-item ${r.reply_status === "none" ? "todo" : ""}`}>
      <div className="row between">
        <div className="stack" style={{ gap: 2 }}>
          {!r.google_expired && <Stars n={r.rating} />}
          <b>{r.author_uri ? <a href={r.author_uri} target="_blank" rel="noreferrer">{r.author || "A customer"}</a> : (r.author || "A customer")}</b>
        </div>
        <div className="row" style={{ gap: 6 }}>
          <Pill kind={r.platform === "Google" ? "on" : ""}>{r.platform}</Pill>
          <span className="small muted">{fmt(r.review_date)}</span>
        </div>
      </div>
      {r.text ? (
        <p className="rep-text">{long && !more ? `${r.text.slice(0, 280)}…` : r.text}
          {long && <button className="btn sm ghost rep-more" onClick={() => setMore(!more)}>{more ? "Less" : "Read all"}</button>}
        </p>
      ) : r.google_expired ? <p className="small muted">This review is over 30 days old. Google's rules don't let the hub keep its words — open it on Google to read it.</p>
        : <p className="small muted">Stars only, no words.</p>}
      <Reply key={`${r.reply_status}-${r.reply_draft}`} r={r} reload={reload} />
      {r.happy && <Referral r={r} reload={reload} />}
      <div className="row between">
        <span className="small muted">{r.source === "google" ? "From Google" : "Pasted"}{r.match_name ? ` · matched to your customer ${r.match_name}` : ""}</span>
        <Busy className="btn sm ghost" run={async () => {
          if (!window.confirm("Remove this review from the hub? It stays on the website it came from.")) return;
          await api(`/reputation/reviews/${r.id}`, { method: "DELETE" }); toast("Removed"); reload();
        }}>Remove</Busy>
      </div>
    </div>
  );
}

/* ───────────────────────── paste a review ───────────────────────── */
function Paste({ platforms, onDone }) {
  const { toast } = useHub();
  const blank = { platform: "Google", author: "", rating: null, text: "", date: todayIso(), draft: true };
  const [f, setF] = useState(blank);
  const [err, setErr] = useState("");
  const set = (k) => (e) => setF({ ...f, [k]: e.target.value });
  return (
    <div className="panel active stack">
      <span className="label">Paste a review</span>
      <p className="small muted">From Google, Facebook, Justdial, IndiaMART, Practo or anywhere else. Copy the review there and paste it here.</p>
      <div className="grid2 rep-grid">
        <Field label="Where is it?">
          <select className="select" value={f.platform} onChange={set("platform")}>{(platforms || []).map((p) => <option key={p}>{p}</option>)}</select>
        </Field>
        <Field label="Their name, as shown"><input className="input" maxLength={80} value={f.author} onChange={set("author")} /></Field>
      </div>
      <div className="field">
        <span className="label">Stars</span>
        <div className="row rep-pick" role="group" aria-label="Stars">
          {[1, 2, 3, 4, 5].map((n) => (
            <button key={n} type="button" className={`btn sm ${f.rating === n ? "primary" : "ghost"}`} aria-pressed={f.rating === n}
              onClick={() => setF({ ...f, rating: n })}>{n}★</button>
          ))}
          <button type="button" className={`btn sm ${f.rating === null ? "primary" : "ghost"}`} aria-pressed={f.rating === null}
            onClick={() => setF({ ...f, rating: null })}>No stars</button>
        </div>
      </div>
      <Field label="What they wrote"><textarea className="input" rows={4} maxLength={3000} value={f.text} onChange={set("text")} /></Field>
      <div className="grid2 rep-grid">
        <Field label="Date of the review"><input className="input" type="date" max={todayIso()} value={f.date} onChange={set("date")} /></Field>
        <div className="field"><span className="label">Reply</span>
          <Switch checked={f.draft} onChange={(v) => setF({ ...f, draft: v })} label="Draft a reply now (1 AI token)" />
        </div>
      </div>
      <FormError msg={err} />
      <div className="row">
        <Busy className="btn primary" disabled={!f.text.trim() && !f.rating} onError={setErr} run={async () => {
          const r = await api("/reputation/reviews", { method: "POST", body: { ...f, author: f.author.trim(), text: f.text.trim() } });
          if (r.draft_error) toast(r.draft_error, "err"); else toast(f.draft ? "Saved, and a reply is drafted" : "Saved");
          setF(blank);
          onDone();
        }}>Save review</Busy>
      </div>
    </div>
  );
}

/* ───────────────────────── ask happy customers for a review ───────────────────────── */
function AskList({ link }) {
  const { toast } = useHub();
  const [h, reload, err] = useLoad("/reputation/happy");
  const [other, setOther] = useState({ name: "", phone: "" });
  const [ferr, setFerr] = useState("");
  const ask = (person) => openWith(async () => {
    const r = await api("/reputation/ask", { method: "POST", body: { name: person.name, phone: person.phone } });
    toast("WhatsApp opened with the message typed");
    reload();
    return r.link;
  });
  return (
    <div className="panel stack">
      <span className="label">Ask happy customers for a review</span>
      {!link ? (
        <p className="small">Add your review link below first{" "}— or connect your Google listing — so the message can carry it.</p>
      ) : (
        <p className="small muted">The message asks for honest feedback — good or bad — with your review link. Nothing is offered in return:
          Google doesn't allow that. It opens your own WhatsApp; you press send. Nobody is suggested twice in {h?.gap_days || 90} days.</p>
      )}
      {!h ? (err ? <LoadError err={err} /> : <Loading />) : !h.items.length ? (
        <p className="small muted">No recent customers to ask right now. Customers you mark won in Leads, or add in Customers, show up here.</p>
      ) : (
        <div className="rep-people">
          {h.items.map((p) => (
            <div key={p.phone} className="rep-person">
              <div><b>{p.name || "Customer"}</b><div className="small muted">{localPhone(p.phone)}{p.since ? ` · ${p.source === "lead" ? "won" : "last bought"} ${fmt(p.since)}` : ""}</div></div>
              <Busy className="btn sm" disabled={!link} run={() => ask(p)}>Ask on WhatsApp</Busy>
            </div>
          ))}
        </div>
      )}
      {link && (
        <details className="rep-other">
          <summary className="small">Ask someone else</summary>
          <div className="grid2 rep-grid">
            <Field label="Name"><input className="input" maxLength={80} value={other.name} onChange={(e) => setOther({ ...other, name: e.target.value })} /></Field>
            <Field label="WhatsApp number"><input className="input" inputMode="tel" maxLength={24} value={other.phone} onChange={(e) => setOther({ ...other, phone: e.target.value })} /></Field>
          </div>
          <FormError msg={ferr} />
          <Busy className="btn sm primary" disabled={other.phone.trim().length < 6} onError={setFerr} run={async () => { await ask(other); setOther({ name: "", phone: "" }); }}>
            Ask on WhatsApp</Busy>
        </details>
      )}
    </div>
  );
}

/* ───────────────────────── settings (owner) ───────────────────────── */
function Settings({ d, reload }) {
  const { toast } = useHub();
  const [link, setLink] = useState(d.review_link_from === "here" ? d.review_link : "");
  const [err, setErr] = useState("");
  const from = { knowledge: "from your Knowledge Hub", google: "Google's own link for your listing" }[d.review_link_from];
  return (
    <div className="panel flat stack">
      <span className="label">Settings</span>
      <Field label="Your review link" hint={d.review_link && d.review_link_from !== "here" ? `Using ${from}: ${d.review_link}. Paste another to use it instead.`
        : "Where customers write a review — your Google, Justdial or Practo review page."}>
        <input className="input" value={link} maxLength={300} onChange={(e) => setLink(e.target.value)} placeholder="https://g.page/r/your-business/review" />
      </Field>
      <FormError msg={err} />
      <div className="row">
        <Busy className="btn sm" onError={setErr} run={async () => { await api("/reputation/settings", { method: "PUT", body: { review_link: link.trim() } }); toast("Saved"); reload(); }}>
          Save link</Busy>
      </div>
      {d.google.available && d.google.place_id && (
        <>
          <Switch checked={d.auto_draft} label={`Draft a reply to every new Google review (up to ${d.drafts_per_day} a day)`}
            onChange={async (v) => { await api("/reputation/settings", { method: "PUT", body: { auto_draft: v } }); toast(v ? "New reviews get a draft" : "Drafts only when you ask"); reload(); }} />
          <span className="hint">Each draft uses 1 AI token. Nothing is posted — you approve, copy and post every reply yourself.</span>
        </>
      )}
    </div>
  );
}

/* ───────────────────────── the page ───────────────────────── */
export default function Reputation() {
  const { me } = useHub();
  const owner = canAct(me, "owner");
  const [d, reload, err] = useLoad("/reputation");
  const [tab, setTab] = useState("todo");
  const [pasting, setPasting] = useState(false);
  const head = { kicker: "Scale · Reviews & reputation", id: "SC-12", pillar: "Scale" };
  if (!d) return <Sheet {...head} title={<>Every review, <em>answered</em>.</>}>{err ? <LoadError err={err} /> : <Loading />}</Sheet>;

  const c = d.counts;
  const waiting = c.new + c.drafted;
  const shown = d.reviews.filter((r) => tab === "all" || (tab === "todo" ? ["none", "drafted"].includes(r.reply_status) : r.reply_status === tab));
  const q0 = [me?.business_name].filter(Boolean).join(" ");
  return (
    <Sheet {...head}
      title={waiting ? <>{plural(waiting, "review")} to <em>reply to</em>.</> : <>Every review, <em>answered</em>.</>}
      sub="Google reviews come in on their own every morning; paste any other. A reply is drafted in your voice — you approve it, copy it and post it."
      actions={<button className={`btn ${pasting ? "" : "primary"}`} onClick={() => setPasting(!pasting)}>{pasting ? "Close" : "Paste a review"}</button>}>

      <GoogleCard g={d.google} owner={owner} q0={q0} reload={reload} />

      {d.b2b_note && (
        <div className="panel flat rep-note">
          <span className="label">For a business that sells to businesses</span>
          <p className="small">Your buyers rarely choose you from Google reviews. A referral from a happy customer and a case study of a job well done
            usually win more work. Use <b>Ask for a referral</b> on your best reviews, and turn a good result into a case study
            {me?.features?.ai_tools ? <> with the <Link to="/tools">Case study writer</Link></> : null}.</p>
        </div>
      )}
      {!d.brain_ready && <p className="small rep-warn">Fill in your <Link to="/knowledge">Knowledge Hub</Link> first — replies are written in your voice from it.</p>}
      {d.brain_ready && !d.contact && (
        <p className="small rep-warn">Add your WhatsApp number in the <Link to="/knowledge">Knowledge Hub</Link>, so an unhappy reviewer is given a way to reach you.</p>
      )}

      {pasting && <Paste platforms={d.platforms} onDone={() => { setPasting(false); setTab("todo"); reload(); }} />}

      <Tabs value={tab} onChange={setTab} tabs={[["todo", `To reply${waiting ? ` · ${waiting}` : ""}`], ["approved", `To post${c.approved ? ` · ${c.approved}` : ""}`],
        ["replied", "Posted"], ["all", "All"]]} />
      {!shown.length ? (
        <Empty>{tab === "todo" ? (d.reviews.length ? "Nothing waiting. Every review has a reply." : "No reviews yet. Paste one with the button above.")
          : tab === "approved" ? "No approved replies waiting to be posted." : "Nothing here yet."}</Empty>
      ) : (
        <div className="stack">{shown.map((r) => <Review key={r.id} r={r} reload={reload} />)}</div>
      )}
      {d.reviews.length >= 50 && <p className="small muted">Showing your latest 50 reviews.</p>}

      <AskList link={d.review_link} />
      {owner && <Settings key={`${d.review_link}-${d.auto_draft}`} d={d} reload={reload} />}
    </Sheet>
  );
}
