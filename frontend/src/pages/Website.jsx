import React, { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api, upload, waLink } from "../api.js";
import { Busy, Copy, Field, FormError, InviteLine, Locked, Modal, Sheet, useHub, useLoad } from "../ui.jsx";
import DomainPanel from "../components/DomainPanel.jsx";
import SharedInputs, { CompetitorNote, FourQuestions, answersOf } from "../components/SharedInputs.jsx";
import SitePreview from "../components/SitePreview.jsx";
import "./website21.css";
import "./lead23.css";

const SECTION_LABEL = { headline: "Headline", subheadline: "Line under the headline", cta: "Button text", about: "About us",
  offers: "What you offer", why: "Why customers choose you", steps: "How it works", faq: "Questions customers ask" };

/** One section's value, as the customer will read it. */
function SectionValue({ k, v }) {
  if (!Array.isArray(v)) return <p className="wp-text">{v || <span className="muted">(empty)</span>}</p>;
  if (!v.length) return <p className="wp-text muted">(empty)</p>;
  const line = (x) => (k === "offers" ? [x.name, x.price].filter(Boolean).join(" — ") + (x.description ? `: ${x.description}` : "")
    : k === "faq" ? `${x.q} — ${x.a}` : [x.title, x.text].filter(Boolean).join(": "));
  return <ul className="wp-list">{v.map((x, i) => <li key={i}>{line(x)}</li>)}</ul>;
}

/** Edit the page by describing the change. The AI touches only the sections the request is about; nothing changes until Apply. */
function PromptEditor({ dirty, onApplied }) {
  const { toast } = useHub();
  const [state, reload] = useLoad("/site/prompt");
  const [text, setText] = useState("");
  const [pv, setPv] = useState(null);
  const [facts, setFacts] = useState([]);
  const [err, setErr] = useState("");
  useEffect(() => { if (state) { setPv(state.preview); setFacts(state.preview?.new_facts || []); } }, [state]);
  const show = (p) => { setPv(p); setFacts(p?.new_facts || []); };
  return (
    <div className="panel wp">
      <div className="row between"><span className="label">Edit with a prompt</span><span className="small muted">1 AI token</span></div>
      <p className="small muted">Say what to change, in your own words. For example: “make the about text warmer” or “add a question about delivery areas”.
        Only the sections you mention change, and only facts from your Knowledge Hub are used.</p>
      <textarea className="textarea" style={{ minHeight: 72 }} maxLength={500} value={text} placeholder="What should change on your page?"
        onChange={(e) => setText(e.target.value)} aria-label="What should change on your page?" />
      {dirty && <span className="hint">Save your own changes first — a prompt works on the saved page.</span>}
      <FormError msg={err} />
      <div className="row">
        <Busy className="btn primary sm ai" disabled={dirty || text.trim().length < 3} onError={setErr} run={async () => {
          show(await api("/site/prompt", { method: "POST", body: { instruction: text.trim() } }));
        }}>Show me the change</Busy>
        {state?.can_undo && !pv && (
          <Busy className="btn ghost sm" onError={setErr} run={async () => {
            await api("/site/prompt/undo", { method: "POST" });
            toast("Back to how the page was before the last prompt edit");
            await reload(); onApplied();
          }}>Undo the last prompt edit</Busy>
        )}
      </div>

      {pv && (
        <div className="stack wp-preview">
          <span className="label">Preview · “{pv.instruction}”</span>
          {!pv.changed.length && (
            <p className="small">{pv.new_facts?.length ? "Nothing changed yet: your request needs a fact your Knowledge Hub doesn't have — see below."
              : pv.invented_left_out ? "Nothing changed: the only change the AI suggested used a number that isn't in your Knowledge Hub, so we left it out."
              : "Nothing on the page needs to change for this."}</p>
          )}
          {pv.changed.map((k) => (
            <div key={k} className="wp-change">
              <span className="small muted">{SECTION_LABEL[k] || k}</span>
              <div className="wp-cols">
                <div><span className="wp-tag">Now</span><SectionValue k={k} v={pv.before[k]} /></div>
                <div><span className="wp-tag new">After</span><SectionValue k={k} v={pv.after[k]} /></div>
              </div>
            </div>
          ))}
          {(pv.left_out || []).filter((x) => x.reason === "invented").map((x) => (
            <p key={x.section} className="small" style={{ borderLeft: "2px solid var(--amber)", paddingLeft: 10 }}>We left out a number the AI added that isn't in your Knowledge Hub ({SECTION_LABEL[x.section] || x.section}: {x.numbers.join(", ")}), so that section stays as it is.</p>
          ))}
          {(pv.notes || []).map((n, i) => <p key={i} className="small muted">{n}</p>)}
          <div className="row">
            {pv.changed.length > 0 && (
              <Busy className="btn primary sm" onError={setErr} run={async () => {
                await api("/site/prompt/apply", { method: "POST" });
                toast("Applied to your page");
                setText(""); await reload(); onApplied();
              }}>Apply</Busy>
            )}
            <Busy className="btn ghost sm" onError={setErr} run={async () => {
              await api("/site/prompt/discard", { method: "POST" });
              show(null); await reload();
            }}>Discard</Busy>
          </div>
        </div>
      )}

      {facts.length > 0 && (
        <div className="stack wp-facts">
          <span className="label">Add this to your Knowledge Hub?</span>
          <p className="small muted">You mentioned {facts.length === 1 ? "this" : "these"}, but your Knowledge Hub doesn't say {facts.length === 1 ? "it" : "them"} yet, so {facts.length === 1 ? "it wasn't" : "they weren't"} put on the page. Add {facts.length === 1 ? "it" : "them"} once and every AI writer in the hub can use {facts.length === 1 ? "it" : "them"} — then ask again.</p>
          {facts.map((f) => (
            <div key={f} className="row between wp-fact">
              <span>{f}</span>
              <Busy className="btn sm" onError={setErr} run={async () => {
                await api("/knowledge/facts", { method: "POST", body: { text: f } });
                setFacts(facts.filter((x) => x !== f));
                toast("Added to your Knowledge Hub");
              }}>Add it</Busy>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

function ListEditor({ label, items, setItems, fields, max, blank }) {
  return (
    <div className="stack">
      <span className="label">{label}</span>
      {items.map((it, i) => (
        <div key={i} className="stack" style={{ borderLeft: "1px solid var(--ink-line)", paddingLeft: 12 }}>
          {fields.map(([k, ph, long]) => long
            ? <textarea key={k} className="textarea" style={{ minHeight: 64 }} placeholder={ph} value={it[k] || ""} onChange={(e) => setItems(items.map((x, j) => (j === i ? { ...x, [k]: e.target.value } : x)))} />
            : <input key={k} className="input" placeholder={ph} value={it[k] || ""} onChange={(e) => setItems(items.map((x, j) => (j === i ? { ...x, [k]: e.target.value } : x)))} />)}
          <button className="btn sm ghost" style={{ justifySelf: "start" }} onClick={() => setItems(items.filter((_, j) => j !== i))}>Remove</button>
        </div>
      ))}
      {items.length < max && <button className="btn sm ghost" style={{ justifySelf: "start" }} onClick={() => setItems([...items, blank])}>Add</button>}
    </div>
  );
}

/** v2.3: the first website — from their own website, or (no website) from 4 quick answers, made to stand out against a
    competitor. Prompt-to-website: answer, press Build, preview, then publish. */
function Builder({ onBuilt }) {
  const { refresh, toast } = useHub();
  const [d, setD] = useState(null);
  const [way, setWay] = useState(null);           // "website" | "answers"
  const [site, setSite] = useState("");
  const [rival, setRival] = useState("");
  const [ans, setAns] = useState({});
  const [err, setErr] = useState("");
  const [busy, setBusy] = useState(false);
  useEffect(() => {
    api("/inputs").then((v) => {
      setD(v); setSite(v.inputs.website_url || ""); setRival(v.inputs.competitor_url || ""); setAns(answersOf(v.inputs));
      setWay(v.inputs.has_website === false ? "answers" : v.inputs.website_url ? "website" : null);
    }).catch((e) => setErr(e.message));
  }, []);
  if (!d) return err ? <FormError msg={err} /> : null;
  const firstRead = rival.trim() && !(d.competitor && d.competitor.matches && d.competitor.status === "ok");
  const build = async () => {
    setBusy(true); setErr("");
    try {
      const body = { competitors: rival.trim() ? [rival.trim()] : [] };
      if (way === "website") Object.assign(body, { website_url: site.trim(), has_website: true });
      else Object.assign(body, { website_url: "", has_website: false, ...ans });
      await api("/inputs", { method: "PUT", body });
      await api("/inputs/website/build", { method: "POST", body: { source: way } });
      await refresh(); toast("Your website is ready to check"); onBuilt();
    } catch (e) { if (!e.handled) setErr(e.message); } finally { setBusy(false); }
  };
  const ready = way === "website" ? site.trim().length > 3 : (ans.about || "").trim().length > 2 && ((ans.offers || "").trim() || (ans.audience || "").trim());
  return (
    <div className="stack">
      <div className="ws-ways" role="radiogroup" aria-label="How should we build it?">
        <button type="button" role="radio" aria-checked={way === "website"} className={`ws-way ${way === "website" ? "on" : ""}`} onClick={() => setWay("website")}>
          <b>I have a website</b><span className="small muted">We read it and build you a better, faster one, with an inquiry form and WhatsApp.</span>
        </button>
        <button type="button" role="radio" aria-checked={way === "answers"} className={`ws-way ${way === "answers" ? "on" : ""}`} onClick={() => setWay("answers")}>
          <b>I don't have one yet</b><span className="small muted">Answer 4 quick questions and we build it for you in about a minute.</span>
        </button>
      </div>
      {way && (
        <div className="panel">
          {way === "website" && (
            <Field label="Your website" hint="We read your home, about, services and contact pages.">
              <input className="input" inputMode="url" placeholder="www.yourbusiness.in" value={site} onChange={(e) => setSite(e.target.value)} />
            </Field>
          )}
          {way === "answers" && <FourQuestions data={d} value={ans} onChange={setAns} />}
          <Field label="A competitor's website (optional, recommended)" hint="So your page stands out. We read their public pages and never copy their words.">
            <input className="input" inputMode="url" placeholder="www.theirbusiness.in" value={rival} onChange={(e) => setRival(e.target.value)} />
          </Field>
          {d.competitor && d.competitor.matches && <CompetitorNote comp={d.competitor} />}
          <FormError msg={err} />
          <div className="row">
            <button className="btn primary ai" disabled={!ready || busy} onClick={build}>{busy ? "Building your website…" : "Build my website"}</button>
            <span className="small muted">Uses 1 AI token{firstRead ? " (plus 1 to read your competitor's website the first time)" : ""}. Takes about a minute. Nothing goes live until you publish.</span>
          </div>
        </div>
      )}
    </div>
  );
}

/** Phone photos are 5–10 MB: shrink them in the browser (longest side 1800 px, JPEG) before uploading. */
async function shrink(file) {
  const img = await new Promise((ok, bad) => {
    const i = new Image();
    i.onload = () => ok(i); i.onerror = () => bad(new Error("That file isn't a photo we can read. Try a JPG or PNG."));
    i.src = URL.createObjectURL(file);
  });
  const scale = Math.min(1, 1800 / Math.max(img.naturalWidth, img.naturalHeight));
  const canvas = document.createElement("canvas");
  canvas.width = Math.round(img.naturalWidth * scale); canvas.height = Math.round(img.naturalHeight * scale);
  canvas.getContext("2d").drawImage(img, 0, 0, canvas.width, canvas.height);
  URL.revokeObjectURL(img.src);
  return new Promise((ok) => canvas.toBlob(ok, "image/jpeg", 0.85));
}

function PhotoThumb({ p, onRemove, tall }) {
  return (
    <div style={{ position: "relative", border: "1px solid var(--ink-line)", aspectRatio: tall ? "4 / 3" : "1", overflow: "hidden", background: "var(--ink)" }}>
      <img src={p.thumb || p.src} alt="" style={{ width: "100%", height: "100%", objectFit: "cover", display: "block" }} />
      <span className="small" style={{ position: "absolute", left: 6, bottom: 6, background: "rgba(10,24,38,.8)", padding: "2px 6px", fontSize: 11 }}>
        {p.kind === "upload" ? "Your photo" : `Stock · ${p.credit}`}
      </span>
      <button className="btn sm ghost" style={{ position: "absolute", right: 6, top: 6, minHeight: 28, padding: "0 10px", background: "rgba(10,24,38,.85)" }}
        onClick={onRemove} aria-label="Remove photo">Remove</button>
    </div>
  );
}

function PhotosPanel({ site, onChange }) {
  const { toast } = useHub();
  const [busy, setBusy] = useState("");
  const ph = site.photos || { hero: null, gallery: [] };
  const run = async (label, fn) => {
    setBusy(label);
    try { await fn(); await onChange(); } catch (e) { if (!e.handled) toast(e.message, "err"); } finally { setBusy(""); }
  };
  const send = (slot, files) => run(slot, async () => {
    for (const f of files) {
      const form = new FormData();
      form.append("slot", slot);
      form.append("file", await shrink(f), "photo.jpg");
      await upload("/site/photos", form);
    }
  });
  const remove = (slot, index = 0) => run("remove", () => api("/site/photos/remove", { method: "POST", body: { slot, index } }));
  const room = 6 - ph.gallery.filter((p) => p.kind === "upload").length;
  return (
    <div className="panel">
      <span className="label">Photos</span>
      <p className="small muted">Real photos of your work, shop or team make the biggest difference. {site.stock_photos ? "Until you add your own, the website uses professional stock photos that match your business." : "Without photos, the website uses designed artwork in your colour."}</p>
      <span className="label" style={{ marginTop: 6 }}>Main photo</span>
      {ph.hero ? <PhotoThumb p={ph.hero} tall onRemove={() => remove("hero")} /> : <p className="small muted">No main photo — your website shows designed artwork instead.</p>}
      <label className="btn sm" style={{ justifySelf: "start", cursor: "pointer" }}>
        {busy === "hero" ? "Uploading…" : ph.hero?.kind === "upload" ? "Replace main photo" : "Upload main photo"}
        <input type="file" accept="image/*" hidden disabled={!!busy} onChange={(e) => { const f = [...e.target.files]; e.target.value = ""; if (f.length) send("hero", f.slice(0, 1)); }} />
      </label>
      <span className="label" style={{ marginTop: 10 }}>Gallery and about-us photos · up to 6</span>
      {ph.gallery.length > 0 && (
        <div style={{ display: "grid", gridTemplateColumns: "repeat(3, minmax(0, 1fr))", gap: 8 }}>
          {ph.gallery.map((p, i) => <PhotoThumb key={(p.id || "") + i} p={p} onRemove={() => remove("gallery", i)} />)}
        </div>
      )}
      <div className="row">
        {room > 0 && (
          <label className="btn sm" style={{ cursor: "pointer" }}>
            {busy === "gallery" ? "Uploading…" : "Add photos"}
            <input type="file" accept="image/*" multiple hidden disabled={!!busy} onChange={(e) => { const f = [...e.target.files].slice(0, room); e.target.value = ""; if (f.length) send("gallery", f); }} />
          </label>
        )}
        {site.stock_photos && (
          <button className="btn sm ghost" disabled={!!busy} onClick={() => run("stock", async () => { await api("/site/photos/stock", { method: "POST" }); toast("New stock photos found. Your own photos stay."); })}>
            {busy === "stock" ? "Finding…" : "Find stock photos"}
          </button>
        )}
      </div>
      <p className="small muted">JPG or PNG. Photos are resized automatically, so phone photos are fine.</p>
    </div>
  );
}

export default function Website() {
  const { me, refresh, toast } = useHub();
  const [site, reload] = useLoad("/site");
  const [c, setC] = useState(null);
  const [accent, setAccent] = useState(null);
  const [slug, setSlug] = useState("");
  const [showcase, setShowcase] = useState(false);
  const [frame, setFrame] = useState(0);
  const [building, setBuilding] = useState(false);
  const [guideUrl, setGuideUrl] = useState(null);
  const [qr, setQr] = useState(false);
  const [preview, setPreview] = useState(false);
  const [inputsOpen, setInputsOpen] = useState(false);

  useEffect(() => {
    if (site?.content) { setC(site.content); setAccent(site.accent); setSlug(site.slug); setShowcase(!!site.showcase); }
  }, [site]);
  // The free guide's link, once the lead magnet is published (Action Program).
  useEffect(() => {
    if (!me.features?.lead_magnet || site?.status !== "live") { setGuideUrl(null); return; }
    api("/lead-magnet").then((d) => setGuideUrl(d.guide?.published ? d.url : null)).catch(() => setGuideUrl(null));
  }, [me.features?.lead_magnet, site?.status, site?.slug]);

  const build = async () => {
    setBuilding(true);
    try { await api("/site/generate", { method: "POST" }); await reload(); await refresh(); setFrame((f) => f + 1); }
    catch (e) { if (!e.handled) toast(e.message, "err"); } finally { setBuilding(false); }
  };
  const save = async () => {
    const r = await api("/site", { method: "PUT", body: { content: c, accent, slug, showcase } });
    await reload(); setFrame((f) => f + 1); await refresh(); return r;
  };

  if (site === null) return null;
  if (!site.content) {
    return (
      <Sheet kicker="Systems · Website" id="SY-01" pillar="Systems" step={2} title={<>Your website, built in a <em>minute</em>.</>}
        sub="From your own website, or from 4 quick answers. Nothing invented — you preview it on a computer and a phone, edit every word, then publish.">
        <Builder onBuilt={async () => { await reload(); setFrame((f) => f + 1); setPreview(true); }} />
        <p className="small muted">Or <button className="linkbtn small" disabled={building} onClick={build}>{building ? "writing it from your Knowledge Hub…" : "write it straight from your Knowledge Hub"}</button> (1 AI token).</p>
      </Sheet>
    );
  }
  if (!c) return null;
  const live = site.status === "live";
  const shareText = encodeURIComponent(`Here's our website — you can send an inquiry directly: ${site.url}`);
  return (
    <Sheet kicker="Systems · Website" id="SY-01" pillar="Systems" step={2}
      title={live ? <>Your website is <em>live</em>.</> : <>Check it, then go <em>live</em>.</>}
      sub={live ? "Every inquiry from the form lands in Leads and alerts you on WhatsApp." : "Edit anything on the left. The preview on the right is exactly what customers will see."}
      actions={<>
        <Busy className="btn ghost sm" run={async () => { await save(); setPreview(true); }}>Preview</Busy>
        <Busy className="btn ghost sm" run={async () => { await save(); toast("Saved"); }}>Save</Busy>
        {live
          ? <Busy className="btn ghost sm" run={async () => { await api("/site/unpublish", { method: "POST" }); await reload(); await refresh(); }}>Take offline</Busy>
          : <Busy className="btn primary" run={async () => { await save(); await api("/site/publish", { method: "POST" }); await reload(); await refresh(); toast("Your website is live"); }}>Publish</Busy>}
      </>}>
      {live && qr && (
        <Modal onClose={() => setQr(false)}>
          <div className="stack" style={{ justifyItems: "center", textAlign: "center" }}>
            <span className="kicker">Scan to send me an inquiry</span>
            <img src="/api/site/qr.png" alt={`QR code for ${site.url}`} style={{ width: "min(70vw, 360px)", height: "auto", background: "#fff", padding: 10 }} />
            <span className="mono" style={{ wordBreak: "break-all" }}>{site.url.replace(/^https?:\/\//, "")}</span>
            <button className="btn ghost" onClick={() => setQr(false)}>Close</button>
          </div>
        </Modal>
      )}
      {live && (
        <div className="panel active">
          <span className="label">Your web address</span>
          <div className="row"><a className="mono" href={site.url} target="_blank" rel="noopener" style={{ wordBreak: "break-all" }}>{site.url}</a></div>
          <div className="row">
            <Copy text={site.url} label="Copy link" />
            <a className="btn sm" href={`https://wa.me/?text=${shareText}`} target="_blank" rel="noopener">Send to customers on WhatsApp</a>
            <button className="btn sm" onClick={() => setQr(true)}>Show the QR</button>
          </div>
          <p className="small muted">Put this link on your WhatsApp Business profile, Google Business listing and visiting card.</p>
          {site.card_url && (
            <>
              <hr className="rule" />
              <span className="label">Your digital visiting card</span>
              <a className="mono small" href={site.card_url} target="_blank" rel="noopener" style={{ wordBreak: "break-all" }}>{site.card_url}</a>
              <div className="row">
                <Copy text={site.card_url} label="Copy link" />
                <a className="btn sm" href={waLink(`Here's my visiting card — save my number in one tap: ${site.card_url}`)} target="_blank" rel="noopener">Share on WhatsApp</a>
              </div>
            </>
          )}
          {guideUrl && (
            <>
              <hr className="rule" />
              <span className="label">Your free guide</span>
              <a className="mono small" href={guideUrl} target="_blank" rel="noopener" style={{ wordBreak: "break-all" }}>{guideUrl}</a>
              <div className="row">
                <Copy text={guideUrl} label="Copy link" />
                <a className="btn sm" href={waLink(`Here's a free guide for you: ${guideUrl}`)} target="_blank" rel="noopener">Share on WhatsApp</a>
                <Link className="btn sm ghost" to="/lead-magnet">Edit the guide</Link>
              </div>
            </>
          )}
        </div>
      )}
      {preview && (
        <SitePreview slug={site.slug} live={live} frame={frame} onClose={() => setPreview(false)}
          actions={!live && <Busy className="btn primary sm" run={async () => { await api("/site/publish", { method: "POST" }); await reload(); await refresh(); setPreview(false); toast("Your website is live"); }}>Publish</Busy>} />
      )}
      <DomainPanel />
      <PromptEditor dirty={JSON.stringify(c) !== JSON.stringify(site.content)} onApplied={async () => { await reload(); setFrame((f) => f + 1); }} />
      <div className="grid2" style={{ alignItems: "start" }}>
        <div className="stack">
          <div className="panel">
            <Field label="Headline"><input className="input" value={c.headline} maxLength={120} onChange={(e) => setC({ ...c, headline: e.target.value })} /></Field>
            <Field label="Line under the headline"><textarea className="textarea" style={{ minHeight: 64 }} value={c.subheadline} maxLength={300} onChange={(e) => setC({ ...c, subheadline: e.target.value })} /></Field>
            <Field label="Button text"><input className="input" value={c.cta} maxLength={40} onChange={(e) => setC({ ...c, cta: e.target.value })} /></Field>
            <Field label="About us"><textarea className="textarea" value={c.about} maxLength={1200} onChange={(e) => setC({ ...c, about: e.target.value })} /></Field>
          </div>
          <div className="panel">
            <ListEditor label="What you offer" items={c.offers} setItems={(offers) => setC({ ...c, offers })} max={12} blank={{ name: "", description: "", price: "" }}
              fields={[["name", "Product or service"], ["description", "One line about it", true], ["price", "Price (leave empty to quote)"]]} />
          </div>
          <div className="panel">
            <ListEditor label="Why customers choose you" items={c.why} setItems={(why) => setC({ ...c, why })} max={6} blank={{ title: "", text: "" }} fields={[["title", "Reason"], ["text", "One line (optional)"]]} />
          </div>
          <div className="panel">
            <ListEditor label="How it works" items={c.steps} setItems={(steps) => setC({ ...c, steps })} max={6} blank={{ title: "", text: "" }} fields={[["title", "Step"], ["text", "What happens"]]} />
          </div>
          <div className="panel">
            <ListEditor label="Questions customers ask" items={c.faq} setItems={(faq) => setC({ ...c, faq })} max={10} blank={{ q: "", a: "" }} fields={[["q", "Question"], ["a", "Answer", true]]} />
          </div>
          <PhotosPanel site={site} onChange={async () => { await reload(); setFrame((f) => f + 1); }} />
          <div className="panel">
            <span className="small muted">Colour</span>
            <div className="row">{site.accents.map((a) => <button key={a} className={`swatch ${a === accent ? "on" : ""}`} style={{ background: a }} aria-label={`Colour ${a}`} onClick={() => setAccent(a)} />)}</div>
            <Field label="Web address" hint="Lowercase letters, numbers and dashes. Changing it changes your link everywhere.">
              <div className="row" style={{ flexWrap: "nowrap", gap: 6 }}>
                {!site.sites_domain && <span className="small muted hide-sm">{site.url.replace(/[^/]+$/, "")}</span>}
                <input className="input mono" style={{ minWidth: 0 }} aria-label="Web address" value={slug} onChange={(e) => setSlug(e.target.value.toLowerCase())} />
                {site.sites_domain && <span className="small muted mono" style={{ flex: "none" }}>.{site.sites_domain}</span>}
              </div>
            </Field>
            <label className="row small" style={{ cursor: "pointer" }}><input type="checkbox" checked={showcase} onChange={(e) => setShowcase(e.target.checked)} /> Show my website in the public showcase</label>
          </div>
          <div className="panel">
            <span className="label">Badge</span>
            <p className="small">{site.badge ? "Free websites show a small “Built free with OwnerDesk” badge. When an owner signs up from your badge, you get rewards." : "Badge off — your website shows only your business."}</p>
            {site.badge && <Locked feature="badge_off" what="Remove the badge" />}
          </div>
          <div className="panel">
            <div className="row between">
              <span className="label">Build it again</span>
              <button className="btn sm ghost" onClick={() => setInputsOpen(!inputsOpen)}>{inputsOpen ? "Hide" : "Your website, competitor and answers"}</button>
            </div>
            {inputsOpen && <SharedInputs title="What your website is built from" showQuestions="always" />}
            <div className="row">
              <Busy className="btn ghost sm ai" run={async () => {
                if (!window.confirm("Build the website again from your answers? This replaces your edits and uses 1 AI token.")) return;
                await api("/inputs/website/build", { method: "POST", body: { source: site.built_from === "website" ? "website" : "answers" } });
                await reload(); setFrame((f) => f + 1); await refresh(); setPreview(true);
              }}>Build again{site.built_from === "website" ? " from my website" : " from my answers"}</Busy>
              <Busy className="btn ghost sm" run={async () => { if (window.confirm("Rewrite the website from your Knowledge Hub? This replaces your edits and uses 1 AI token.")) await build(); }}>Rewrite from my Knowledge Hub</Busy>
            </div>
          </div>
          <InviteLine />
        </div>
        <div style={{ position: "sticky", top: 20 }}>
          <span className="label">Preview{live ? " · live" : " · only you can see this"}</span>
          <iframe key={frame} title="Website preview" className="preview-frame" style={{ marginTop: 10 }} src={`/s/${site.slug}?preview=1`} />
        </div>
      </div>
    </Sheet>
  );
}
