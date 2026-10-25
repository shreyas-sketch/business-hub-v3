import React, { useEffect, useState } from "react";
import "./fullpreview.css";

/**
 * "Preview full screen": the page as customers see it, over the whole screen, on a phone or a laptop width.
 * `src` is reloaded each time it opens, so it always shows the last save.
 */
export default function FullPreview({ src, title = "Preview", label = "Preview full screen", className = "btn primary" }) {
  const [open, setOpen] = useState(false);
  return (
    <>
      <button type="button" className={className} onClick={() => setOpen(true)}>{label}</button>
      {open && <Overlay src={src} title={title} onClose={() => setOpen(false)} />}
    </>
  );
}

function Overlay({ src, title, onClose }) {
  const [width, setWidth] = useState(() => (window.innerWidth < 760 ? "fill" : "laptop"));
  const [stamp] = useState(() => Date.now());
  useEffect(() => {
    const k = (e) => e.key === "Escape" && onClose();
    const html = document.documentElement, was = html.style.overflow;
    html.style.overflow = "hidden";
    window.addEventListener("keydown", k);
    return () => { window.removeEventListener("keydown", k); html.style.overflow = was; };
  }, [onClose]);
  const url = `${src}${src.includes("?") ? "&" : "?"}t=${stamp}`;
  return (
    <div className="fp" role="dialog" aria-modal="true" aria-label={title}>
      <div className="fp-bar">
        <b className="fp-title">{title}</b>
        <div className="fp-seg hide-sm" role="group" aria-label="Screen size">
          <button type="button" aria-pressed={width === "phone"} className={width === "phone" ? "on" : ""} onClick={() => setWidth("phone")}>Phone</button>
          <button type="button" aria-pressed={width === "laptop"} className={width === "laptop" ? "on" : ""} onClick={() => setWidth("laptop")}>Laptop</button>
        </div>
        <a className="btn sm ghost" href={src} target="_blank" rel="noopener"><span className="hide-sm">Open in a new tab</span><span className="show-sm">New tab</span></a>
        <button type="button" className="btn sm" onClick={onClose} autoFocus>Close</button>
      </div>
      <div className={`fp-stage ${width}`}>
        <iframe title={title} src={url} />
      </div>
    </div>
  );
}
