import React, { useEffect, useMemo, useRef, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { api, upload } from "../api.js";
import { Busy, Copy, Field, FormError, Sheet, canAct, useHub, useLoad } from "../ui.jsx";
import { Loading, Pill, Tabs } from "../v2.jsx";
import "./brandkit.css";
import Brochures from "./BrandBrochures.jsx";

/* Brand kit (v2.1, 3.11): the message system (one AI token, every line editable), the design system (colours, fonts,
   corners, spacing, logo) and designs drawn in the brand on the server. The primary colour is the website's accent,
   so the website, the launch kit and the next invoice all follow it. */

const LABELS = {
  "core.one_line": "Brand message (one line)", "core.pitch": "30-second pitch", "core.positioning.who": "Who it's for",
  "core.positioning.what": "What you do", "core.positioning.why": "Why you", "core.proof": "Proof points",
  "website.headline": "Headline", "website.subheadline": "Line under the headline", "website.about": "About us", "website.ctas": "Button texts",
  "marketing.ad_headlines": "Ad headlines", "marketing.festival_line": "Festival offer line", "marketing.tagline": "Tagline",
  "sales.elevator": "Elevator pitch", "sales.whatsapp_intro": "WhatsApp introduction", "sales.proposal_opener": "Proposal opening",
  "sales.email_signature": "Email signature",
  "social.instagram": "Instagram bio", "social.linkedin": "LinkedIn about", "social.facebook": "Facebook about",
  "social.whatsapp_business": "WhatsApp Business about", "social.hooks": "Caption hooks",
  "voice.tone": "Tone", "voice.dos": "Do", "voice.donts": "Don't", "voice.use": "Words we use", "voice.avoid": "Words we avoid",
};
const LIMITS = { "social.instagram": 150, "social.whatsapp_business": 139 };
const SECTION_ORDER = ["core", "website", "marketing", "sales", "social", "voice"];
const GROUPS = ["Social", "WhatsApp", "Print", "Email"];
const HEX = /^#[0-9A-Fa-f]{6}$/;

// ── contrast, the same maths as the server's check (WCAG) ──
const lin = (c) => { const x = c / 255; return x <= 0.03928 ? x / 12.92 : ((x + 0.055) / 1.055) ** 2.4; };
const lum = (hex) => { const [r, g, b] = [1, 3, 5].map((i) => parseInt(hex.slice(i, i + 2), 16)); return 0.2126 * lin(r) + 0.7152 * lin(g) + 0.0722 * lin(b); };
const ratio = (a, b) => { const [x, y] = [lum(a), lum(b)].sort((p, q) => q - p); return (x + 0.05) / (y + 0.05); };
const textOn = (bg) => (ratio(bg, "#FFFFFF") >= ratio(bg, "#14171C") ? "#FFFFFF" : "#14171C");

/** Every editable item as [path, value]: core.one_line, core.positioning.who … */
function paths(messages) {
  const out = [];
  for (const sec of SECTION_ORDER) {
    for (const [k, v] of Object.entries(messages?.[sec] || {})) {
      if (v && typeof v === "object" && !Array.isArray(v)) Object.entries(v).forEach(([a, b]) => out.push([`${sec}.${k}.${a}`, b]));
      else out.push([`${sec}.${k}`, v]);
    }
  }
  return out;
}

function MsgItem({ path, value, edited, canEdit, onKit }) {
  const isList = Array.isArray(value);
  const orig = isList ? value.join("\n") : value || "";
  const [v, setV] = useState(orig);
  useEffect(() => setV(orig), [orig]);
  const dirty = v !== orig;
  const limit = LIMITS[path];
  const label = LABELS[path] || path.split(".").pop().replace(/_/g, " ");
  const save = async () => {
    const value = isList ? v.split("\n").map((x) => x.trim()).filter(Boolean) : v.trim();
    onKit(await api("/brand-kit/messages", { method: "PUT", body: { path, value } }));
  };
  const reset = async () => onKit(await api("/brand-kit/messages/reset", { method: "POST", body: { path } }));
  return (
    <div className="bk-item">
      <div className="row between bk-item-head">
        <span className="label">{label}</span>
        <span className="row" style={{ gap: 6 }}>
          {edited && <Pill kind="on">Your words</Pill>}
          <Copy text={orig} className="btn sm ghost" />
        </span>
      </div>
      {canEdit ? (
        <textarea className="textarea" rows={isList ? Math.max(3, value.length + 1) : orig.length > 120 ? 4 : 2} value={v}
          placeholder={isList ? "One per line" : ""} onChange={(e) => setV(e.target.value)} aria-label={label} />
      ) : isList ? <ul className="read-list">{value.map((x, i) => <li key={i}>{x}</li>)}</ul> : <p className="bk-read">{orig || "—"}</p>}
      {(canEdit && (dirty || edited || limit)) && (
        <div className="row bk-item-foot">
          {dirty && <Busy className="btn sm primary" run={save}>Save</Busy>}
          {dirty && <button className="btn sm ghost" onClick={() => setV(orig)}>Cancel</button>}
          {!dirty && edited && <Busy className="btn sm ghost" run={reset}>Back to the AI version</Busy>}
          {limit && <span className={`small ${v.length > limit ? "bk-over" : "muted"}`}>{v.length} of {limit} characters</span>}
        </div>
      )}
    </div>
  );
}

function Messages({ kit, onKit, canEdit }) {
  const { refresh, toast } = useHub();
  const [err, setErr] = useState("");
  const write = async () => {
    onKit(await api("/brand-kit/generate", { method: "POST" }));
    refresh();
    toast(kit.exists ? "Rewritten from your Knowledge Hub. Your own words were kept." : "Your brand kit is written");
  };
  if (!kit.exists) {
    return (
      <div className="panel active">
        <h3>Write your brand kit</h3>
        <p className="muted">One AI token reads your Knowledge Hub and writes your brand message, and its versions for your website, ads, sales and social media, plus your tone of voice.</p>
        <p className="small muted">It uses only what's in your Knowledge Hub. Nothing is invented — change any line after.</p>
        <FormError msg={err} />
        {canEdit ? <div className="row"><Busy className="btn primary ai" run={write} onError={setErr}>Write my brand kit</Busy><span className="small muted">Uses one AI token.</span></div>
          : <p className="small muted">Ask the business owner to write it.</p>}
      </div>
    );
  }
  const items = paths(kit.messages);
  return (
    <div className="stack">
      {canEdit && (
        <div className="row between bk-bar">
          <span className="small muted">Every AI writer in your hub uses your tone of voice. Lines marked "Your words" stay as you wrote them.</span>
          <Busy className="btn sm ghost ai" run={write}>Rewrite from my Knowledge Hub</Busy>
        </div>
      )}
      {SECTION_ORDER.map((sec) => (
        <div key={sec} className="panel bk-sec">
          <h3>{kit.sections?.[sec] || sec}</h3>
          <div className="bk-items">
            {items.filter(([p]) => p.startsWith(`${sec}.`)).map(([p, v]) => (
              <MsgItem key={p} path={p} value={v} edited={!!kit.edited?.[p]} canEdit={canEdit} onKit={onKit} />
            ))}
          </div>
        </div>
      ))}
    </div>
  );
}

function ColourField({ label, value, onChange }) {
  const [txt, setTxt] = useState(value);
  useEffect(() => setTxt(value), [value]);
  const ok = HEX.test(value);
  const r = ok ? ratio(value, textOn(value)) : 0;
  return (
    <div className="bk-colour">
      <span className="label">{label}</span>
      <div className="row bk-colour-row">
        <input type="color" className="bk-swatch-in" aria-label={`${label} colour`} value={ok ? value.toLowerCase() : "#1f4e79"} onChange={(e) => onChange(e.target.value.toUpperCase())} />
        <input className="input mono bk-hex" maxLength={7} value={txt} aria-label={`${label} hex`}
          onChange={(e) => { const x = e.target.value.trim(); setTxt(x); if (HEX.test(x)) onChange(x.toUpperCase()); }} />
      </div>
      {ok && (
        <span className="bk-sample" style={{ background: value, color: textOn(value) }}>
          Text on it: {r.toFixed(1)}:1 {r >= 4.5 ? "· easy to read" : "· hard to read for small text"}
        </span>
      )}
    </div>
  );
}

function Design({ kit, onKit, canEdit }) {
  const { toast } = useHub();
  const d = kit.design || {};
  const [c, setC] = useState({ ...d.colors });
  const [fonts, setFonts] = useState({ ...d.fonts });
  const [corners, setCorners] = useState(d.corners || "soft");
  const [spacing, setSpacing] = useState(d.spacing || "comfortable");
  const [textLogo, setTextLogo] = useState(d.text_logo || "");
  const [suggested, setSuggested] = useState(null);
  const [err, setErr] = useState("");
  const fileRef = useRef(null);
  const font = (k) => (kit.fonts || []).find((f) => f.key === k);
  const radius = { square: 0, soft: 8, round: 20 }[corners] ?? 8;
  const valid = ["primary", "secondary", "accent"].every((k) => HEX.test(c[k] || ""));

  const save = async () => {
    const body = { colors: { primary: c.primary, secondary: c.secondary, accent: c.accent, background: c.background || "#FFFFFF", text: c.text || "#14171C" },
      fonts, corners, spacing, text_logo: textLogo.trim() };
    onKit(await api("/brand-kit/design", { method: "PUT", body }));
    toast("Saved. Your website, launch kit and next invoice use these colours now.");
  };
  const fromLogo = async (file) => {
    if (!file) return;
    const form = new FormData();
    form.append("file", file);
    const r = await upload("/brand-kit/logo", form);
    const { suggested_colors: s, ...rest } = r;
    onKit(rest);
    setSuggested(s);
    toast("Logo saved");
  };
  const fromSite = async () => {
    const s = await api("/brand-kit/colors/website");
    setC({ ...c, ...s });
    toast("Colours from your website — save to keep them");
  };

  return (
    <div className="stack">
      <div className="bk-design">
        <div className="panel">
          <h3>Colours</h3>
          <div className="bk-colours">
            <ColourField label="Main colour" value={c.primary || ""} onChange={(v) => setC({ ...c, primary: v })} />
            <ColourField label="Second colour" value={c.secondary || ""} onChange={(v) => setC({ ...c, secondary: v })} />
            <ColourField label="Highlight" value={c.accent || ""} onChange={(v) => setC({ ...c, accent: v })} />
          </div>
          {canEdit && (
            <div className="row">
              <button className="btn sm ghost" onClick={() => fileRef.current?.click()}>Pick from my logo</button>
              <Busy className="btn sm ghost" run={fromSite}>Use my website's colour</Busy>
              <input ref={fileRef} type="file" accept="image/png,image/jpeg" hidden
                onChange={async (e) => { const f = e.target.files?.[0]; e.target.value = ""; try { await fromLogo(f); } catch (x) { if (!x.handled) toast(x.message, "err"); } }} />
            </div>
          )}
          {suggested && (
            <div className="panel flat bk-suggest">
              <span className="label">From your logo</span>
              <div className="row" style={{ gap: 6 }}>
                {["primary", "secondary", "accent"].map((k) => <span key={k} className="bk-chip" style={{ background: suggested[k] }} title={suggested[k]} />)}
                <button className="btn sm" onClick={() => { setC({ ...c, ...suggested }); setSuggested(null); }}>Use these colours</button>
              </div>
            </div>
          )}
          {kit.has_logo && <img className="bk-logo" src={`/api/brand-kit/logo?v=${encodeURIComponent(kit.updated_at || "")}`} alt="Your logo" />}
        </div>

        <div className="panel">
          <h3>Fonts and shape</h3>
          <Field label="Headings">
            <select className="select" value={fonts.heading || ""} disabled={!canEdit} onChange={(e) => setFonts({ ...fonts, heading: e.target.value })}>
              {(kit.fonts || []).map((f) => <option key={f.key} value={f.key}>{f.label}</option>)}
            </select>
          </Field>
          <Field label="Text">
            <select className="select" value={fonts.body || ""} disabled={!canEdit} onChange={(e) => setFonts({ ...fonts, body: e.target.value })}>
              {(kit.fonts || []).map((f) => <option key={f.key} value={f.key}>{f.label}</option>)}
            </select>
          </Field>
          {[font(fonts.heading), font(fonts.body)].some((f) => f && !f.devanagari) && (
            <p className="small muted">English-only fonts can't draw Hindi or Marathi, so Hindi text in your designs uses Noto Sans Devanagari.</p>
          )}
          <div className="grid2">
            <Field label="Corners">
              <select className="select" value={corners} disabled={!canEdit} onChange={(e) => setCorners(e.target.value)}>
                {(kit.corners || []).map((x) => <option key={x} value={x}>{x[0].toUpperCase() + x.slice(1)}</option>)}
              </select>
            </Field>
            <Field label="Spacing">
              <select className="select" value={spacing} disabled={!canEdit} onChange={(e) => setSpacing(e.target.value)}>
                {(kit.spacing || []).map((x) => <option key={x} value={x}>{x[0].toUpperCase() + x.slice(1)}</option>)}
              </select>
            </Field>
          </div>
          <Field label="Text logo" hint="Used when there's no logo image.">
            <input className="input" maxLength={40} readOnly={!canEdit} value={textLogo} onChange={(e) => setTextLogo(e.target.value)} />
          </Field>
        </div>
      </div>

      {valid && (
        <div className="bk-preview" style={{ background: c.primary, color: textOn(c.primary), borderRadius: radius, padding: spacing === "compact" ? 18 : 28 }}>
          <span className="bk-preview-logo">{textLogo || "Your business"}</span>
          <span className="bk-preview-line">{kit.messages?.core?.one_line || "Your brand message shows here."}</span>
          <span className="bk-preview-btn" style={{ background: c.accent, color: textOn(c.accent), borderRadius: radius ? 999 : 0 }}>
            {kit.messages?.website?.ctas?.[0] || "Message us"}
          </span>
        </div>
      )}

      <FormError msg={err} />
      {canEdit && (
        <div className="row">
          <Busy className="btn primary" disabled={!valid} run={save} onError={setErr}>Save the design</Busy>
          <span className="small muted">Saving changes your website's colour, your launch kit and your next invoice and quotation.</span>
        </div>
      )}
    </div>
  );
}

function Designs({ kit, initial }) {
  const { toast } = useHub();
  const templates = kit.templates || [];
  const [key, setKey] = useState(initial?.key && templates.some((t) => t.key === initial.key) ? initial.key : templates[0]?.key);
  const t = templates.find((x) => x.key === key);
  const [fields, setFields] = useState(null);
  const [img, setImg] = useState(null);
  const [err, setErr] = useState("");
  const pending = useRef(initial?.fields || null);

  useEffect(() => {
    if (!key) return;
    let live = true;
    setFields(null); setImg(null); setErr("");
    api(`/brand-kit/designs/${key}`).then((r) => {
      if (!live) return;
      const known = new Set((t?.fields || []).map((f) => f.key));
      const deep = pending.current && initial?.key === key ? Object.fromEntries(Object.entries(pending.current).filter(([k]) => known.has(k))) : {};
      pending.current = null;
      setFields({ ...r.fields, ...deep });
    }).catch((e) => live && setErr(e.message));
    return () => { live = false; };
  }, [key]); // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => () => { if (img) URL.revokeObjectURL(img); }, [img]);

  const make = async (format) => {
    const res = await fetch(`/api/brand-kit/designs/${key}`, { method: "POST", credentials: "same-origin",
      headers: { "content-type": "application/json" }, body: JSON.stringify({ fields, format }) });
    if (!res.ok) {
      const d = await res.json().catch(() => ({}));
      throw new Error(typeof d.detail === "string" ? d.detail : d.detail?.message || "That design couldn't be made. Please try again.");
    }
    const url = URL.createObjectURL(await res.blob());
    if (format === "pdf") {
      const a = document.createElement("a");
      a.href = url; a.download = `${key}.pdf`; document.body.appendChild(a); a.click(); a.remove();
      setTimeout(() => URL.revokeObjectURL(url), 30000);
      toast("PDF downloaded");
    } else setImg(url);
  };

  return (
    <div className="bk-designs">
      <div className="stack bk-tpls">
        {GROUPS.map((g) => (
          <div key={g} className="stack" style={{ gap: 6 }}>
            <span className="label">{g}</span>
            <div className="listbox">
              {templates.filter((x) => x.group === g).map((x) => (
                <button key={x.key} className={x.key === key ? "on" : ""} onClick={() => setKey(x.key)}>
                  <span>{x.label}</span><span className="small muted">{x.size[0]}×{x.size[1]}{x.pdf ? " · PDF to print" : ""}</span>
                </button>
              ))}
            </div>
          </div>
        ))}
      </div>
      <div className="panel active">
        {!t ? <Loading /> : (
          <>
            <h3>{t.label}</h3>
            {t.note && <p className="small muted">{t.note}</p>}
            {!fields ? (err ? <FormError msg={err} /> : <Loading />) : (
              <>
                {t.fields.map((f) => (
                  <Field key={f.key} label={f.label} hint={`${(fields[f.key] || "").length} of ${f.max}`}>
                    {f.lines > 1 ? (
                      <textarea className="textarea" rows={f.lines} maxLength={f.max} value={fields[f.key] || ""} onChange={(e) => setFields({ ...fields, [f.key]: e.target.value })} />
                    ) : <input className="input" maxLength={f.max} value={fields[f.key] || ""} onChange={(e) => setFields({ ...fields, [f.key]: e.target.value })} />}
                  </Field>
                ))}
                <FormError msg={err} />
                <div className="row">
                  <Busy className="btn primary" run={() => make("png")} onError={setErr}>Make the design</Busy>
                  {t.pdf && <Busy className="btn" run={() => make("pdf")} onError={setErr}>Download PDF to print</Busy>}
                </div>
                <span className="small muted">Drawn in your brand colours and fonts. Hindi and Marathi work. No AI token used.</span>
              </>
            )}
            {img && (
              <div className="stack bk-out">
                <img src={img} alt={`${t.label} in your brand`} />
                <div className="row"><a className="btn sm" href={img} download={`${key}.png`}>Download PNG</a></div>
              </div>
            )}
          </>
        )}
      </div>
    </div>
  );
}

export default function BrandKit() {
  const { me } = useHub();
  const [params] = useSearchParams();
  const deep = useMemo(() => {
    const k = params.get("design");
    if (!k) return null;
    const fields = {};
    params.forEach((v, name) => { if (name !== "design") fields[name] = v; });
    return { key: k, fields };
  }, []); // eslint-disable-line react-hooks/exhaustive-deps
  const [tab, setTab] = useState(deep ? "designs" : params.get("tab") === "brochures" ? "brochures" : "messages");
  const [kitLoaded, , err] = useLoad("/brand-kit");
  const [kit, setKit] = useState(null);
  useEffect(() => { if (kitLoaded) setKit(kitLoaded); }, [kitLoaded]);
  const canEdit = canAct(me, "owner");
  const head = { kicker: "Brand · Brand kit", id: "BR-01", pillar: "Brand",
    title: <>One voice, one look, <em>everywhere</em>.</>,
    sub: "Your brand message for every place you speak, your colours and fonts, and ready designs for social media, WhatsApp and print." };
  if (!kit) return <Sheet {...head}>{err ? <FormError msg={err.message} /> : <Loading />}</Sheet>;
  return (
    <Sheet {...head} actions={kit.exists ? <a className="btn sm" href="/api/brand-kit/pdf">Download brand kit PDF</a> : null}>
      <Tabs tabs={[["messages", "Messages"], ["design", "Design"], ["designs", "Designs"], ["brochures", "Brochures"]]} value={tab} onChange={setTab} />
      {tab === "messages" && <Messages kit={kit} onKit={setKit} canEdit={canEdit} />}
      {tab === "design" && <Design kit={kit} onKit={setKit} canEdit={canEdit} />}
      {tab === "designs" && <Designs kit={kit} initial={deep} />}
      {tab === "brochures" && <Brochures />}
      {tab !== "designs" && tab !== "brochures" && me?.features?.social_media && (
        <p className="small muted">Want posts ready to go? Open <Link to="/social">Social media</Link> — images in your colours, with captions.</p>
      )}
      {tab !== "designs" && tab !== "brochures" && !me?.features?.social_media && me?.features?.content_calendar && (
        <p className="small muted">Want a post designed? Open <Link to="/calendar">your content calendar</Link> and press "Make the design" on any post.</p>
      )}
    </Sheet>
  );
}
