import React, { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api, upload, waLink } from "../api.js";
import { Busy, Copy, Field, FormError, InviteLine, Locked, Modal, Sheet, useHub, useLoad } from "../ui.jsx";
import DomainPanel from "../components/DomainPanel.jsx";
import CompetitorInputs, { cleanCompetitors } from "../components/Competitors.jsx";
import FullPreview from "../components/FullPreview.jsx";
import Section from "../components/Section.jsx";
import "./website21.css";

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
      <div className="row between"><span className="label">Edit with a prompt</span><span className="small muted">1 AI run</span></div>
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

const count = (n, one, many) => `${n} ${n === 1 ? one : many}`;

export default function Website() {
  const { me, refresh, toast } = useHub();
  const [site, reload] = useLoad("/site");
  const [c, setC] = useState(null);
  const [accent, setAccent] = useState(null);
  const [look, setLook] = useState("auto");
  const [slug, setSlug] = useState("");
  const [showcase, setShowcase] = useState(false);
  const [rivals, setRivals] = useState([""]);
  const [frame, setFrame] = useState(0);
  const [building, setBuilding] = useState(false);
  const [buildErr, setBuildErr] = useState("");
  const [guideUrl, setGuideUrl] = useState(null);
  const [qr, setQr] = useState(false);

  useEffect(() => {
    if (site?.content) { setC(site.content); setAccent(site.accent); setLook(site.look || "auto"); setSlug(site.slug); setShowcase(!!site.showcase); }
    if (site?.competitors?.length) setRivals(site.competitors.map((x) => x.url));
  }, [site]);
  // The free guide's link, once the lead magnet is published (Action Program).
  useEffect(() => {
    if (!me.features?.lead_magnet || site?.status !== "live") { setGuideUrl(null); return; }
    api("/lead-magnet").then((d) => setGuideUrl(d.guide?.published ? d.url : null)).catch(() => setGuideUrl(null));
  }, [me.features?.lead_magnet, site?.status, site?.slug]);

  const build = async () => {
    setBuilding(true); setBuildErr("");
    try {
      await api("/site/generate", { method: "POST", body: { competitors: cleanCompetitors(rivals) } });
      await reload(); await refresh(); setFrame((f) => f + 1);
    } catch (e) { if (!e.handled) setBuildErr(e.message); } finally { setBuilding(false); }
  };
  const save = async () => {
    const r = await api("/site", { method: "PUT", body: { content: c, accent, look, slug, showcase } });
    await reload(); setFrame((f) => f + 1); await refresh(); return r;
  };

  if (site === null) return null;
  if (!site.content) {
    return (
      <Sheet kicker="Systems · Website" id="SY-01" pillar="Systems" step={2} title={<>Your website, written from your <em>Brain</em>.</>}
        sub="The AI uses your brand message, your products and why customers choose you. Nothing invented — you can edit every word before it goes live.">
        <div className="panel stack ws-build">
          <div className="field">
            <span className="label">Competitor websites <span className="muted">(optional, up to 3)</span></span>
            <CompetitorInputs value={rivals} onChange={setRivals} />
            <span className="hint">Your website is written to stand out from them. Nothing is copied, and they're never named.</span>
          </div>
          <FormError msg={buildErr} />
          <div className="row">
            <button className="btn primary ai" disabled={building} onClick={build}>{building ? "Writing your website…" : "Build my website"}</button>
            <span className="small muted">Uses 1 AI run. Takes about a minute.</span>
          </div>
        </div>
      </Sheet>
    );
  }
  if (!c) return null;
  const live = site.status === "live";
  const shareText = encodeURIComponent(`Here's our website — you can send an inquiry directly: ${site.url}`);
  const dirty = JSON.stringify(c) !== JSON.stringify(site.content) || accent !== site.accent || look !== (site.look || "auto")
    || slug !== site.slug || showcase !== !!site.showcase;
  const undo = () => { setC(site.content); setAccent(site.accent); setLook(site.look || "auto"); setSlug(site.slug); setShowcase(!!site.showcase); };
  const lookName = look === "auto" ? `${site.look_looks?.find((l) => l.key === site.look_auto)?.name || ""} · automatic` : site.look_looks?.find((l) => l.key === look)?.name;
  const photoCount = (site.photos?.hero?.kind === "upload" ? 1 : 0) + (site.photos?.gallery || []).filter((p) => p.kind === "upload").length;
  const readRivals = (site.competitors || []).filter((x) => x.read);
  const publish = async () => { await save(); await api("/site/publish", { method: "POST" }); await reload(); await refresh(); toast("Your website is live"); };
  return (
    <Sheet kicker="Systems · Website" id="SY-01" pillar="Systems" step={2}
      title={live ? <>Your website is <em>live</em>.</> : <>Check it, then go <em>live</em>.</>}
      sub={live ? "Every inquiry from the form lands in Leads and alerts you on WhatsApp." : "Change anything below, preview it full screen, then publish."}
      actions={live
        ? <Busy className="btn ghost sm" run={async () => { await api("/site/unpublish", { method: "POST" }); await reload(); await refresh(); }}>Take offline</Busy>
        : <Busy className="btn primary" run={publish}>Publish</Busy>}>
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

      <div className={`panel ws-top ${live ? "active" : ""}`}>
        {live
          ? <a className="mono ws-url" href={site.url} target="_blank" rel="noopener">{site.url.replace(/^https?:\/\//, "")}</a>
          : <span className="small muted">Only you can see it until you publish.</span>}
        <div className="row">
          <FullPreview src={`/s/${site.slug}?preview=1`} title={`${me.business_name || "Your website"} · preview`} className={live ? "btn" : "btn primary"} />
          {live && <>
            <Copy text={site.url} label="Copy link" />
            <a className="btn sm" href={`https://wa.me/?text=${shareText}`} target="_blank" rel="noopener">Share on WhatsApp</a>
            <button className="btn sm ghost" onClick={() => setQr(true)}>QR code</button>
          </>}
        </div>
        {live && (site.card_url || guideUrl) && (
          <details className="ws-more">
            <summary>More to share: {[site.card_url && "visiting card", guideUrl && "free guide"].filter(Boolean).join(" and ")}</summary>
            {site.card_url && (
              <div className="stack" style={{ gap: 6 }}>
                <span className="label">Your digital visiting card</span>
                <a className="mono small" href={site.card_url} target="_blank" rel="noopener" style={{ wordBreak: "break-all" }}>{site.card_url}</a>
                <div className="row"><Copy text={site.card_url} label="Copy link" />
                  <a className="btn sm" href={waLink(`Here's my visiting card — save my number in one tap: ${site.card_url}`)} target="_blank" rel="noopener">Share on WhatsApp</a></div>
              </div>
            )}
            {guideUrl && (
              <div className="stack" style={{ gap: 6 }}>
                <span className="label">Your free guide</span>
                <a className="mono small" href={guideUrl} target="_blank" rel="noopener" style={{ wordBreak: "break-all" }}>{guideUrl}</a>
                <div className="row"><Copy text={guideUrl} label="Copy link" />
                  <a className="btn sm" href={waLink(`Here's a free guide for you: ${guideUrl}`)} target="_blank" rel="noopener">Share on WhatsApp</a>
                  <Link className="btn sm ghost" to="/lead-magnet">Edit the guide</Link></div>
              </div>
            )}
          </details>
        )}
      </div>

      <div className="ws-grid">
        <div className="stack ws-edit">
          <Section title="Main message" note={c.headline} open>
            <Field label="Headline"><input className="input" value={c.headline} maxLength={120} onChange={(e) => setC({ ...c, headline: e.target.value })} /></Field>
            <Field label="Line under the headline"><textarea className="textarea" style={{ minHeight: 64 }} value={c.subheadline} maxLength={300} onChange={(e) => setC({ ...c, subheadline: e.target.value })} /></Field>
            <Field label="Button text"><input className="input" value={c.cta} maxLength={40} onChange={(e) => setC({ ...c, cta: e.target.value })} /></Field>
            <Field label="About us"><textarea className="textarea" value={c.about} maxLength={1200} onChange={(e) => setC({ ...c, about: e.target.value })} /></Field>
          </Section>
          <Section title="What you offer" note={count(c.offers.length, "product or service", "products and services")}>
            <ListEditor label="Products and prices" items={c.offers} setItems={(offers) => setC({ ...c, offers })} max={12} blank={{ name: "", description: "", price: "" }}
              fields={[["name", "Product or service"], ["description", "One line about it", true], ["price", "Price (leave empty to quote)"]]} />
          </Section>
          <Section title="Photos" note={photoCount ? count(photoCount, "of your photos", "of your photos") : site.stock_photos ? "Stock photos for now" : "No photos yet"}>
            <PhotosPanel site={site} onChange={async () => { await reload(); setFrame((f) => f + 1); }} />
          </Section>
          <Section title="Look and colour" note={lookName}>
            {site.look_looks && (
              <div className="look-pick" role="radiogroup" aria-label="Website look">
                {[{ key: "auto", name: `Automatic · ${site.look_looks.find((l) => l.key === site.look_auto)?.name || ""}`, about: "Picked for your kind of business." }, ...site.look_looks].map((l) => (
                  <button key={l.key} type="button" role="radio" aria-checked={look === l.key} className={`look-opt ${look === l.key ? "on" : ""}`} onClick={() => setLook(l.key)}>
                    <b>{l.name}</b><span>{l.about}</span>
                  </button>
                ))}
              </div>
            )}
            <span className="label">Colour</span>
            <div className="row">{site.accents.map((a) => <button key={a} className={`swatch ${a === accent ? "on" : ""}`} style={{ background: a }} aria-label={`Colour ${a}`} onClick={() => setAccent(a)} />)}</div>
          </Section>
          <Section title="More sections" note={`${count(c.why.length, "reason", "reasons")} · ${count(c.steps.length, "step", "steps")} · ${count(c.faq.length, "question", "questions")}`}>
            <ListEditor label="Why customers choose you" items={c.why} setItems={(why) => setC({ ...c, why })} max={6} blank={{ title: "", text: "" }} fields={[["title", "Reason"], ["text", "One line (optional)"]]} />
            <hr className="rule" />
            <ListEditor label="How it works" items={c.steps} setItems={(steps) => setC({ ...c, steps })} max={6} blank={{ title: "", text: "" }} fields={[["title", "Step"], ["text", "What happens"]]} />
            <hr className="rule" />
            <ListEditor label="Questions customers ask" items={c.faq} setItems={(faq) => setC({ ...c, faq })} max={10} blank={{ q: "", a: "" }} fields={[["q", "Question"], ["a", "Answer", true]]} />
          </Section>
          <Section title="Change it with AI" note={readRivals.length ? `Standing out from ${readRivals.map((x) => x.host).join(", ")}` : "Describe a change, or rewrite it all"}>
            <PromptEditor dirty={dirty} onApplied={async () => { await reload(); setFrame((f) => f + 1); }} />
            <hr className="rule" />
            <div className="panel stack">
              <span className="label">Rewrite the whole website</span>
              <div className="field">
                <span className="small">Competitor websites <span className="muted">(optional, up to 3)</span></span>
                <CompetitorInputs value={rivals} onChange={setRivals} read={site.competitors || []} />
                <span className="hint">The new copy is written to stand out from them. Nothing is copied, and they're never named.</span>
              </div>
              <FormError msg={buildErr} />
              <div className="row">
                <Busy className="btn ai" disabled={building} run={async () => { if (window.confirm("Rewrite the website with AI? This replaces your edits and uses 1 AI run.")) await build(); }}>
                  {building ? "Rewriting…" : "Rewrite with AI"}</Busy>
                <span className="small muted">1 AI run · replaces your edits</span>
              </div>
            </div>
          </Section>
          <Section title="Web address and settings" note={site.sites_domain ? `${slug}.${site.sites_domain}` : `/s/${slug}`}>
            <Field label="Web address" hint="Lowercase letters, numbers and dashes. Changing it changes your link everywhere.">
              <div className="row" style={{ flexWrap: "nowrap", gap: 6 }}>
                {!site.sites_domain && <span className="small muted hide-sm">{site.url.replace(/[^/]+$/, "")}</span>}
                <input className="input mono" style={{ minWidth: 0 }} aria-label="Web address" value={slug} onChange={(e) => setSlug(e.target.value.toLowerCase())} />
                {site.sites_domain && <span className="small muted mono" style={{ flex: "none" }}>.{site.sites_domain}</span>}
              </div>
            </Field>
            <label className="row small" style={{ cursor: "pointer" }}><input type="checkbox" checked={showcase} onChange={(e) => setShowcase(e.target.checked)} /> Show my website in the public showcase</label>
            <hr className="rule" />
            <span className="label">Badge</span>
            <p className="small">{site.badge ? "Free websites show a small “Built free with OwnerDesk” badge. When an owner signs up from your badge, you get rewards." : "Badge off — your website shows only your business."}</p>
            {site.badge && <Locked feature="badge_off" what="Remove the badge" />}
            <hr className="rule" />
            <DomainPanel />
          </Section>
          <InviteLine />
        </div>
        <div className="ws-side hide-sm">
          <div className="row between"><span className="label">Preview{live ? " · live" : " · only you can see this"}</span>
            <FullPreview src={`/s/${site.slug}?preview=1`} title={`${me.business_name || "Your website"} · preview`} label="Full screen" className="btn sm ghost" /></div>
          <iframe key={frame} title="Website preview" className="preview-frame" style={{ marginTop: 10 }} src={`/s/${site.slug}?preview=1`} />
        </div>
      </div>

      {dirty && (
        <div className="ws-savebar" role="status">
          <span>You have changes that aren't saved.</span>
          <div className="row">
            <button className="btn sm ghost" onClick={undo}>Undo</button>
            <Busy className="btn sm primary" run={async () => { await save(); toast("Saved"); }}>Save changes</Busy>
          </div>
        </div>
      )}
    </Sheet>
  );
}
