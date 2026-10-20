import React, { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api, waLink } from "../api.js";
import { Busy, Copy, Field, FormError, InviteLine, Locked, Sheet, useHub, useLoad } from "../ui.jsx";
import DomainPanel from "../components/DomainPanel.jsx";
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
        <Busy className="btn primary sm" disabled={dirty || text.trim().length < 3} onError={setErr} run={async () => {
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
      <Sheet kicker="Systems · Website" id="SY-01" pillar="Systems" step={2} title={<>Your website, written from your <em>Brain</em>.</>}
        sub="The AI uses your brand message, your products and why customers choose you. Nothing invented — you can edit every word before it goes live.">
        <div className="empty">
          <h2>{building ? "Writing your website…" : "Ready when you are."}</h2>
          <p className="muted">Uses 1 AI run. Takes about a minute.</p>
          <button className="btn primary" disabled={building} onClick={build}>{building ? "Building…" : "Build my website"}</button>
        </div>
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
        <Busy className="btn ghost sm" run={async () => { await save(); toast("Saved"); }}>Save</Busy>
        {live
          ? <Busy className="btn ghost sm" run={async () => { await api("/site/unpublish", { method: "POST" }); await reload(); await refresh(); }}>Take offline</Busy>
          : <Busy className="btn primary" run={async () => { await save(); await api("/site/publish", { method: "POST" }); await reload(); await refresh(); toast("Your website is live"); }}>Publish</Busy>}
      </>}>
      {live && (
        <div className="panel active">
          <span className="label">Your web address</span>
          <div className="row"><a className="mono" href={site.url} target="_blank" rel="noopener" style={{ wordBreak: "break-all" }}>{site.url}</a></div>
          <div className="row">
            <Copy text={site.url} label="Copy link" />
            <a className="btn sm" href={`https://wa.me/?text=${shareText}`} target="_blank" rel="noopener">Send to customers on WhatsApp</a>
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
          <div className="panel">
            <span className="label">Look</span>
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
            <p className="small">{site.badge ? "Free websites show a small “Built free with Business AI Action Hub” badge. When an owner signs up from your badge, you get rewards." : "Badge off — your website shows only your business."}</p>
            {site.badge && <Locked feature="badge_off" what="Remove the badge" />}
          </div>
          <div className="row">
            <Busy className="btn ghost sm" run={async () => { if (window.confirm("Rewrite the website with AI? This replaces your edits and uses 1 AI run.")) await build(); }}>Rewrite with AI</Busy>
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
