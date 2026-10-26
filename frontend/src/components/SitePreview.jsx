import React, { useEffect, useRef, useState } from "react";

/* v2.3: the full website before it goes live — at desktop width and phone width, side by side (one at a time on a phone).
   It shows the saved page exactly as customers will see it. */
export default function SitePreview({ slug, live, onClose, actions, frame = 0 }) {
  const box = useRef(null);
  const [scale, setScale] = useState(0.6);
  const [only, setOnly] = useState(typeof window !== "undefined" && window.innerWidth < 900 ? "phone" : "desk");
  useEffect(() => {
    const k = (e) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", k);
    return () => window.removeEventListener("keydown", k);
  }, [onClose]);
  useEffect(() => {
    if (!box.current) return undefined;
    const fit = () => setScale(Math.min(1, (box.current?.clientWidth || 800) / 1280));
    fit();
    const ro = new ResizeObserver(fit);
    ro.observe(box.current);
    return () => ro.disconnect();
  }, []);
  const src = `/s/${slug}?preview=1&f=${frame}`;
  return (
    <>
      <div className="pv-scrim" onClick={onClose} />
      <div className="pv" role="dialog" aria-modal="true" aria-label="Website preview">
        <div className="pv-bar">
          <div className="stack" style={{ gap: 2 }}>
            <b>Preview{live ? " · live" : " · only you can see this"}</b>
            <span className="small muted">{live ? "This is what customers see now." : "Nothing is public until you publish."}</span>
          </div>
          <div className="row">
            <div className="row pv-toggle" role="radiogroup" aria-label="Screen size">
              {[["desk", "Desktop"], ["phone", "Phone"]].map(([k, l]) => (
                <button key={k} type="button" role="radio" aria-checked={only === k} className={`btn sm ${only === k ? "primary" : "ghost"}`} onClick={() => setOnly(k)}>{l}</button>
              ))}
            </div>
            {actions}
            <button className="btn sm ghost" onClick={onClose}>Close</button>
          </div>
        </div>
        <div className={`pv-stage only-${only}`}>
          <div className="pv-desk">
            <span className="pv-tag">Desktop</span>
            <div className="pv-desk-box" ref={box} style={{ height: 800 * scale }}>
              <iframe title="Your website on a computer" src={src} style={{ transform: `scale(${scale})` }} />
            </div>
          </div>
          <div className="pv-phone">
            <span className="pv-tag">Phone</span>
            <div className="pv-phone-box"><iframe title="Your website on a phone" src={src} /></div>
          </div>
        </div>
      </div>
    </>
  );
}
