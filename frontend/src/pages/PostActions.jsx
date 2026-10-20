import React, { useCallback, useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../api.js";
import { Busy, FormError, canAct, useHub } from "../ui.jsx";
import { Pill } from "../v2.jsx";
import "./brandkit.css";

/* "Mark as posted" and "Make the design" for any post the hub wrote (v2.1, 3.12).
   A post's ref says where it came from:
     cal:{YYYY-MM}:{YYYY-MM-DD}   a content-calendar post
     week:{week}:{n}              a post in a weekly plan
     out:{output id}:{n}          a section of a Content Agent draft
   The posting log counts each post once; marking it again only changes the platform or the link. */

export const PLATFORMS = ["Instagram", "Facebook", "LinkedIn", "WhatsApp Status", "Google"];

/** Which posts under these prefixes are already marked: { ref: {platform, link} }. */
export function usePostedRefs(prefixes, enabled = true) {
  const key = (prefixes || []).filter(Boolean).join("|");
  const [map, setMap] = useState({});
  const reload = useCallback(async () => {
    if (!enabled || !key) { setMap({}); return {}; }
    const parts = await Promise.all(key.split("|").map((p) => api(`/social/refs?prefix=${encodeURIComponent(p)}`).catch(() => ({}))));
    const merged = Object.assign({}, ...parts);
    setMap(merged);
    return merged;
  }, [key, enabled]);
  useEffect(() => { reload(); }, [reload]);
  return [map, reload];
}

/** The Brand kit template for a post, with its words filled in. */
export function designLink(post) {
  const clip = (s, n) => String(s || "").replace(/\s+/g, " ").trim().slice(0, n);
  const q = new URLSearchParams();
  if (post.occasion) {
    q.set("design", "festival");
    q.set("greeting", clip(post.occasion, 60));
    q.set("message", clip(post.hook || post.caption, 200));
  } else {
    q.set("design", "social_square");
    q.set("headline", clip(post.hook, 90));
    q.set("body", clip(post.caption, 250));
  }
  return `/brand-kit?${q.toString()}`;
}

export default function PostActions({ postRef, post, posted, onChange }) {
  const { me, toast } = useHub();
  const [open, setOpen] = useState(false);
  const [platform, setPlatform] = useState("");
  const [link, setLink] = useState("");
  const [err, setErr] = useState("");
  const canDesign = !!me?.features?.brand_kit && canAct(me, "owner");

  const save = async () => {
    const l = link.trim();
    if (l && !/^https?:\/\/\S+$/.test(l)) throw new Error("The link should start with https://");
    await api("/social/posted", { method: "POST", body: { ref: postRef, platform, link: l } });
    setOpen(false); setPlatform(""); setLink("");
    toast("Marked as posted");
    await onChange?.();
  };
  const undo = async () => {
    await api(`/social/posted?ref=${encodeURIComponent(postRef)}`, { method: "DELETE" });
    toast("Not posted");
    await onChange?.();
  };

  return (
    <div className="pa">
      <div className="row pa-row">
        {posted ? (
          <>
            <Pill kind="on">Posted{posted.platform ? ` · ${posted.platform}` : ""}</Pill>
            {posted.link && <a className="small" href={posted.link} target="_blank" rel="noreferrer">See the post</a>}
            <Busy className="btn sm ghost" run={undo}>Undo</Busy>
          </>
        ) : !open && <button className="btn sm" onClick={() => setOpen(true)}>Mark as posted</button>}
        {canDesign && <Link className="btn sm ghost" to={designLink(post)}>Make the design</Link>}
      </div>
      {open && !posted && (
        <div className="pa-form">
          <label className="field"><span className="label">Where (optional)</span>
            <select className="select" value={platform} onChange={(e) => setPlatform(e.target.value)}>
              <option value="">Not saying</option>
              {PLATFORMS.map((p) => <option key={p} value={p}>{p}</option>)}
            </select>
          </label>
          <label className="field"><span className="label">Link to the post (optional)</span>
            <input className="input" inputMode="url" maxLength={500} placeholder="https://" value={link} onChange={(e) => setLink(e.target.value)} />
          </label>
          <FormError msg={err} />
          <div className="row">
            <Busy className="btn sm primary" run={save} onError={setErr}>Save</Busy>
            <button className="btn sm ghost" onClick={() => { setOpen(false); setErr(""); }}>Cancel</button>
          </div>
        </div>
      )}
    </div>
  );
}

/** The posting log in one line: this week, this month, by platform, weeks in a row. */
export function PostingSummary({ summary }) {
  if (!summary) return null;
  const plats = Object.entries(summary.by_platform || {});
  return (
    <div className="panel flat pa-sum">
      <div className="pa-stats">
        <div><span className="label">Posted this week</span><b>{summary.week}</b></div>
        <div><span className="label">Posted this month</span><b>{summary.month}</b></div>
        <div><span className="label">Weeks in a row</span><b>{summary.streak_weeks}</b></div>
      </div>
      {plats.length > 0 && (
        <div className="row" style={{ gap: 6 }}>
          {plats.map(([k, n]) => <Pill key={k}>{k} · {n}</Pill>)}
        </div>
      )}
      {summary.next && <span className="small muted">Next post: {summary.next.hook || summary.next.occasion} · {summary.next.date}</span>}
    </div>
  );
}
