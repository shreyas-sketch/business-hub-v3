import React, { useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { Link } from "react-router-dom";
import QRCode from "qrcode";
import { localPhone, waLink } from "../api.js";
import { Copy, Field, Sheet, useHub, useLoad } from "../ui.jsx";
import { Loading } from "../v2.jsx";
import "./free.css";

const W = 1080;
const H = 1920;
const HEAD = '"Manrope", system-ui, sans-serif';
const BODY = '"Manrope", system-ui, sans-serif';
const INK = "#14151A";
const LAYOUTS = [["bold", "Bold"], ["light", "Light"], ["dark", "Dark"]];
const bare = (u) => String(u || "").replace(/^https?:\/\//, "").replace(/\/$/, "");

/* ───────── drawing the WhatsApp Status poster (1080 × 1920) ───────── */
function wrap(ctx, text, maxW) {
  const lines = [];
  let line = "";
  for (const w of String(text || "").split(/\s+/).filter(Boolean)) {
    const t = line ? `${line} ${w}` : w;
    if (!line || ctx.measureText(t).width <= maxW) line = t;
    else { lines.push(line); line = w; }
  }
  if (line) lines.push(line);
  return lines;
}

/** Sets the font and returns the lines, shrinking the size until the text fits in `max` lines (and `maxH` pixels, if given). */
function fit(ctx, text, { maxW, max = 99, maxH = Infinity, size, min, weight = 500, family = HEAD, lh = 1.18 }) {
  let s = size;
  let lines;
  const fits = () => lines.length <= max && lines.length * s * lh <= maxH && lines.every((l) => ctx.measureText(l).width <= maxW);
  for (;;) {
    ctx.font = `${weight} ${s}px ${family}`;
    lines = wrap(ctx, text, maxW);
    if (fits() || s <= min) break;
    s -= 4;
  }
  const keep = Math.max(1, Math.min(max, Math.floor(maxH / (s * lh))));
  if (lines.length > keep) { lines = lines.slice(0, keep); lines[keep - 1] = `${lines[keep - 1].replace(/\s*\S*$/, "")}…`; }
  return { lines, size: s };
}

function block(ctx, text, x, y, opts, align = "left") {
  if (!text) return y;
  const { lines, size } = fit(ctx, text, opts);
  const lh = Math.round(size * (opts.lh || 1.18));
  ctx.textAlign = align;
  ctx.textBaseline = "top";
  lines.forEach((l, i) => ctx.fillText(l, x, y + i * lh));
  return y + lines.length * lh;
}

function box(ctx, x, y, w, h, r) {
  ctx.beginPath();
  if (ctx.roundRect) ctx.roundRect(x, y, w, h, r); else ctx.rect(x, y, w, h);
}

function spaced(ctx, px) { if ("letterSpacing" in ctx) ctx.letterSpacing = `${px}px`; }

function grid(ctx, colour) {
  ctx.strokeStyle = colour;
  ctx.lineWidth = 2;
  for (let x = 0; x <= W; x += 90) { ctx.beginPath(); ctx.moveTo(x, 0); ctx.lineTo(x, H); ctx.stroke(); }
  for (let y = 0; y <= H; y += 90) { ctx.beginPath(); ctx.moveTo(0, y); ctx.lineTo(W, y); ctx.stroke(); }
}

function drawPoster(ctx, p) {
  const { layout, accent, name, message, extra, city, wa, url } = p;
  const light = layout === "light";
  const centre = light;
  const X = centre ? W / 2 : 96;
  const align = centre ? "center" : "left";
  const maxW = W - 192;
  ctx.clearRect(0, 0, W, H);
  ctx.fillStyle = layout === "bold" ? accent : light ? "#FFFFFF" : INK;
  ctx.fillRect(0, 0, W, H);
  if (layout === "bold") grid(ctx, "rgba(255,255,255,0.07)");
  if (layout === "dark") { grid(ctx, "#13212E"); ctx.strokeStyle = accent; ctx.lineWidth = 12; ctx.strokeRect(48, 48, W - 96, H - 96); }
  if (light) { ctx.fillStyle = accent; ctx.fillRect(0, 0, W, 28); }

  const text = light ? INK : "#FFFFFF";
  const soft = light ? "#324251" : "rgba(255,255,255,0.86)";
  const top = 1400;                                   // the footer box starts here
  const bottom = city ? top - 120 : top - 40;         // the text above it ends here
  // kicker, name, rule, message and offer; drawn once invisibly to measure, then moved down into the spare room
  const content = (dy) => {
    ctx.fillStyle = light ? accent : soft;
    spaced(ctx, 8);
    let y = block(ctx, "NOW ONLINE", X, 200 + dy, { maxW, max: 1, size: 36, min: 36, family: BODY }, align);
    spaced(ctx, 0);
    ctx.fillStyle = light ? accent : text;
    y = block(ctx, name, X, y + 40, { maxW, max: 3, size: 132, min: 72, lh: 1.05 }, align);
    ctx.fillStyle = light || layout === "dark" ? accent : "#FFFFFF";
    ctx.fillRect(centre ? X - 70 : X, y + 36, 140, 10);
    y += 46;
    const limit = bottom - (extra ? 250 : 0);
    ctx.fillStyle = text;
    if (limit - (y + 50) > 60) y = block(ctx, message, X, y + 50, { maxW, maxH: limit - (y + 50), size: 64, min: 40, weight: 400, family: BODY, lh: 1.3 }, align);
    if (extra) {
      const { lines, size } = fit(ctx, extra, { maxW: maxW - 80, max: 2, size: 46, min: 34, family: BODY, lh: 1.3 });
      const h = Math.round(lines.length * size * 1.3 + 64);
      ctx.strokeStyle = light ? accent : "#FFFFFF";
      ctx.lineWidth = 4;
      box(ctx, 96, y + 60, W - 192, h, 24);
      ctx.stroke();
      ctx.fillStyle = light ? accent : "#FFFFFF";
      block(ctx, extra, centre ? X : X + 40, y + 60 + 36, { maxW: maxW - 80, max: 2, size, min: size, family: BODY, lh: 1.3 }, align);
      y += 60 + h;
    }
    return y;
  };
  ctx.save();
  ctx.globalAlpha = 0;
  const end = content(0);
  ctx.restore();
  content(Math.max(0, Math.round((bottom - end) * 0.4)));
  // footer: city, WhatsApp, website
  if (city) {
    ctx.fillStyle = soft;
    block(ctx, city, X, top - 90, { maxW, max: 1, size: 40, min: 30, weight: 400, family: BODY }, align);
  }
  if (!wa && !url) return;
  ctx.fillStyle = light || layout === "dark" ? accent : "#FFFFFF";
  box(ctx, 60, top, W - 120, 420, 36);
  ctx.fill();
  const inner = light || layout === "dark" ? "#FFFFFF" : accent;
  const ink = light || layout === "dark" ? "#FFFFFF" : INK;
  ctx.fillStyle = inner;
  spaced(ctx, 6);
  block(ctx, wa ? "CHAT ON WHATSAPP" : "VISIT US ONLINE", W / 2, top + 70, { maxW, max: 1, size: 36, min: 30, family: BODY }, "center");
  spaced(ctx, 0);
  ctx.fillStyle = ink;
  block(ctx, wa || bare(url), W / 2, top + 140, { maxW: W - 220, max: 1, size: wa ? 104 : 64, min: 40 }, "center");
  if (wa && url) {
    ctx.fillStyle = light || layout === "dark" ? "rgba(255,255,255,0.88)" : "#324251";
    block(ctx, bare(url), W / 2, top + 300, { maxW: W - 220, max: 1, size: 44, min: 28, weight: 400, family: BODY }, "center");
  }
}

function Poster({ biz, accent, url }) {
  const { toast } = useHub();
  const ref = useRef(null);
  const [layout, setLayout] = useState("bold");
  const [message, setMessage] = useState(biz.brand?.message || biz.industry || "");
  const [extra, setExtra] = useState("");
  const [fonts, setFonts] = useState(false);
  useEffect(() => {
    const f = document.fonts;
    if (!f?.load) { setFonts(true); return; }
    Promise.all([f.load(`500 100px ${HEAD}`), f.load(`400 40px ${BODY}`), f.load(`500 40px ${BODY}`)]).finally(() => setFonts(true));
  }, []);
  const wa = localPhone(biz.whatsapp);
  useEffect(() => {
    const ctx = ref.current?.getContext("2d");
    if (ctx) drawPoster(ctx, { layout, accent, name: biz.name, message, extra, city: biz.city, wa, url });
  }, [layout, accent, biz.name, biz.city, message, extra, wa, url, fonts]);

  const file = `${(biz.name || "poster").toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "") || "poster"}-status.png`;
  const blob = () => new Promise((res) => ref.current.toBlob(res, "image/png"));
  const download = async () => {
    const b = await blob();
    if (!b) { toast("Couldn't make the picture. Please try again.", "err"); return; }
    const a = document.createElement("a");
    a.href = URL.createObjectURL(b);
    a.download = file;
    document.body.appendChild(a);
    a.click();
    a.remove();
    setTimeout(() => URL.revokeObjectURL(a.href), 2000);
  };
  const canShare = typeof navigator !== "undefined" && !!navigator.canShare && navigator.canShare({ files: [new File([""], "x.png", { type: "image/png" })] });
  const share = async () => {
    const b = await blob();
    try { await navigator.share({ files: [new File([b], file, { type: "image/png" })] }); } catch { /* the owner closed the share sheet */ }
  };

  return (
    <div className="fr-kit">
      <canvas ref={ref} width={W} height={H} className="fr-poster" role="img" aria-label={`WhatsApp Status poster for ${biz.name}`} />
      <div className="stack">
        <div className="row"><span className="label" style={{ minWidth: 60 }}>Layout</span>
          <div className="fr-seg" role="group" aria-label="Poster layout">
            {LAYOUTS.map(([k, l]) => <button key={k} type="button" aria-pressed={layout === k} onClick={() => setLayout(k)}>{l}</button>)}
          </div>
        </div>
        <Field label="Main line" hint="Your brand message. Keep it short.">
          <textarea className="textarea" style={{ minHeight: 72 }} maxLength={160} value={message} onChange={(e) => setMessage(e.target.value)} /></Field>
        <Field label="Offer or note (optional)" hint="e.g. Free site visit this week · Diwali orders open">
          <input className="input" maxLength={70} value={extra} onChange={(e) => setExtra(e.target.value)} /></Field>
        {!wa && <p className="small muted">Add your WhatsApp number in your <Link to="/start">Business Brain</Link> to show it on the poster.</p>}
        <div className="row">
          <button className="btn primary" onClick={download}>Download PNG</button>
          {canShare && <button className="btn" onClick={share}>Share the poster</button>}
        </div>
        <p className="small muted">Post it on your WhatsApp Status, then in your groups. It's 1080 × 1920, the size Status uses.</p>
      </div>
    </div>
  );
}

/** The A5 counter stand. Sized in em: a small preview on screen, 148 × 210 mm on paper. */
function Stand({ name, message, qr, wa, url, accent }) {
  return (
    <div className="fr-stand">
      <span className="nm" style={{ color: accent }}>{name}</span>
      {message && <span className="msg">{message}</span>}
      <span className="scan">Scan to see our work</span>
      {qr && <img src={qr} alt={`QR code for ${bare(url)}`} />}
      {wa && <span className="wa">or chat on WhatsApp: <b>{wa}</b></span>}
      <span className="url">{bare(url)}</span>
      <span className="strip" style={{ background: accent }} aria-hidden="true" />
    </div>
  );
}

export default function LaunchKit() {
  const { me } = useHub();
  const [biz] = useLoad("/business");
  const [site] = useLoad("/site");
  const [qr, setQr] = useState("");
  const live = site?.status === "live";
  const url = live ? site.url : "";
  useEffect(() => {
    if (!url) return;
    QRCode.toDataURL(url, { width: 720, margin: 1, errorCorrectionLevel: "M", color: { dark: INK, light: "#FFFFFF" } }).then(setQr).catch(() => setQr(""));
  }, [url]);
  useEffect(() => () => document.body.classList.remove("fr-printing"), []);

  const print = () => {
    const page = document.createElement("style");
    page.textContent = "@page { size: A5 portrait; margin: 0; }";
    document.head.appendChild(page);
    document.body.classList.add("fr-printing");
    const done = () => { document.body.classList.remove("fr-printing"); page.remove(); window.removeEventListener("afterprint", done); };
    window.addEventListener("afterprint", done);
    window.print();
  };

  const head = { kicker: "Grow · Launch kit", id: "GR-01", pillar: "Grow", step: 3, title: <>Tell everyone you're <em>online</em>.</>,
    sub: "Three ready-made things to announce your business: a WhatsApp Status poster, a digital visiting card and a QR stand for your counter — in your colours." };
  if (!biz || !site) return <Sheet {...head}><Loading /></Sheet>;

  const accent = site.accent || "#1F4E79";
  const wa = localPhone(biz.whatsapp);
  const message = biz.brand?.message || "";
  const card = site.card_url || (me.site?.url ? `${me.site.url}/card` : "");
  return (
    <Sheet {...head}>
      {!live && (
        <div className="notice">
          <span>Your website isn't live yet. The poster works now; the visiting card and QR stand point to your website, so put it live first.</span>
          <Link className="btn sm" to="/website">Open website</Link>
        </div>
      )}

      <div className="panel">
        <span className="label">1 · WhatsApp Status poster</span>
        <Poster biz={biz} accent={accent} url={url} />
      </div>

      <div className="panel">
        <span className="label">2 · Digital visiting card</span>
        {live ? (
          <div className="fr-kit">
            <iframe className="fr-card-frame" src={`/s/${site.slug}/card`} title="Your digital visiting card" loading="lazy" />
            <div className="stack">
              <p>One link with your name, brand message, WhatsApp, email and website. Customers tap <b>Save contact</b> to add you to their phone.</p>
              <div className="inline-add"><input className="input mono" readOnly value={card} aria-label="Your visiting card link" /><Copy text={card} /></div>
              <div className="row">
                <a className="btn sm" href={waLink(`Here's our digital visiting card — save our number and see our work: ${card}`)} target="_blank" rel="noreferrer">Share on WhatsApp</a>
                <a className="btn sm ghost" href={card} target="_blank" rel="noopener">Open card</a>
                <a className="btn sm ghost" href={`/s/${site.slug}/card.vcf`}>Contact file (.vcf)</a>
              </div>
              <p className="small muted">Put the link in your WhatsApp Business profile and your email signature, and send it after every meeting.</p>
            </div>
          </div>
        ) : <div className="empty"><p className="muted">Your visiting card goes live with your website.</p><Link className="btn sm" to="/website">Put my website live</Link></div>}
      </div>

      <div className="panel">
        <span className="label">3 · QR stand for your counter</span>
        {live ? (
          <div className="fr-kit">
            <Stand name={biz.name} message={message} qr={qr} wa={wa} url={url} accent={accent} />
            <div className="stack">
              <p>Print it on A5, put it in a stand at your counter, shop or site office. Customers scan it to see your work and chat with you.</p>
              <div className="row">
                <button className="btn primary" disabled={!qr} onClick={print}>Print the stand</button>
                {qr && <a className="btn ghost" href={qr} download={`${site.slug}-qr.png`}>Download QR code</a>}
              </div>
              <p className="small muted">For best results print in colour on thick paper (250 gsm or more). Set the printer to A5, or print on A4 and trim.</p>
            </div>
            {qr && createPortal(<div className="fr-print-only"><Stand name={biz.name} message={message} qr={qr} wa={wa} url={url} accent={accent} /></div>, document.body)}
          </div>
        ) : <div className="empty"><p className="muted">Your QR stand points to your website. Put it live first.</p><Link className="btn sm" to="/website">Put my website live</Link></div>}
      </div>
    </Sheet>
  );
}
