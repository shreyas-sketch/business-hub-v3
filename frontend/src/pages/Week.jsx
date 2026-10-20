import React, { useRef, useState } from "react";
import { Link } from "react-router-dom";
import { api, rs, waLink } from "../api.js";
import { Busy, Copy, Field, FormError, Sheet, useHub, useLoad } from "../ui.jsx";
import { Empty, Loading, Num, Pill } from "../v2.jsx";
import PostActions, { usePostedRefs } from "./PostActions.jsx";
import "./member.css";

/* ── Small pieces shared by this group's pages (Content calendar, Gaps scan) ── */
export const tag = (h) => { const s = String(h).trim(); return s.startsWith("#") ? s : `#${s.replace(/\s+/g, "")}`; };
export const postText = (p) => [p.hook, p.caption, (p.hashtags || []).map(tag).join(" ")].filter(Boolean).join("\n\n");
/** "2026-10-12" → "Mon, 12 Oct" (date-only strings, no time-zone shift). */
export const dateLabel = (iso, opts = { weekday: "short", day: "numeric", month: "short" }) =>
  new Date(`${String(iso).slice(0, 10)}T00:00:00`).toLocaleDateString("en-IN", opts);
export const daysBetween = (a, b) => Math.round((Date.parse(String(b).slice(0, 10)) - Date.parse(String(a).slice(0, 10))) / 86400000);
const inDays = (n) => (n <= 0 ? "Today" : n === 1 ? "Tomorrow" : `In ${n} days`);
const plural = (n, one, many) => `${n} ${n === 1 ? one : many}`;

/** One social post: hook, caption (long ones fold), hashtags and Copy. Extra buttons go in `children`. */
export function PostCard({ post, head, children }) {
  const [open, setOpen] = useState(false);
  const long = (post.caption || "").length > 280;
  return (
    <div className="m-post">
      {head}
      {post.hook && <p className="m-hook">{post.hook}</p>}
      {post.caption && <p className={`m-caption ${long && !open ? "clamp" : ""}`}>{post.caption}</p>}
      {long && <button className="linkbtn small" style={{ justifySelf: "start" }} onClick={() => setOpen(!open)}>{open ? "Show less" : "Show the whole caption"}</button>}
      {post.hashtags?.length > 0 && <p className="m-tags">{post.hashtags.map(tag).join(" ")}</p>}
      <div className="row"><Copy text={postText(post)} label="Copy post" />{children}</div>
    </div>
  );
}

/** A list of 0–100 scores as thin bars, each labelled with its number (the number is the data; the bar is the shape). */
export function ScoreBars({ rows, max = 100 }) {
  return (
    <div className="m-bars">
      {rows.map((r) => (
        <div key={r.key} className={`m-barrow ${r.dim ? "dim" : ""}`} title={`${r.label}: ${r.value} of ${max}`}>
          <span className="l">{r.label}</span>
          <span className="bar" aria-hidden="true"><i style={{ width: `${Math.max(0, Math.min(100, (r.value / max) * 100))}%` }} /></span>
          <span className="v">{r.value}</span>
          {r.note && <span className="note">{r.note}</span>}
        </div>
      ))}
    </div>
  );
}

const pageName = (me, path) => (me?.menu || []).flatMap((g) => g.items).find((i) => i.path === path)?.label;

function Checkin({ week, onSaved }) {
  const c = week.checkin || {};
  const [f, setF] = useState({ leads: c.leads ?? "", sales: c.sales ?? "", revenue: c.revenue ?? "", wins: c.wins || "" });
  const [err, setErr] = useState("");
  const set = (k) => (v) => setF({ ...f, [k]: v });
  const save = async () => {
    const body = { leads: Math.round(Number(f.leads) || 0), sales: Math.round(Number(f.sales) || 0), revenue: Number(f.revenue) || 0, wins: f.wins.trim() };
    await onSaved(await api("/week/checkin", { method: "POST", body }));
  };
  return (
    <div className="stack">
      <span className="label">Friday check-in · 2 minutes</span>
      <div className="grid3">
        <Field label="New inquiries"><Num min="0" step="1" value={f.leads} onChange={set("leads")} /></Field>
        <Field label="Sales closed"><Num min="0" step="1" value={f.sales} onChange={set("sales")} /></Field>
        <Field label="Sales value (₹)" hint={Number(f.revenue) > 0 ? <span className="money">{rs(f.revenue)}</span> : null}>
          <Num min="0" value={f.revenue} onChange={set("revenue")} />
        </Field>
      </div>
      <Field label="This week's wins">
        <textarea className="textarea" maxLength={600} placeholder="e.g. Closed the Shah kitchen; 3 people asked about the Diwali offer" value={f.wins} onChange={(e) => set("wins")(e.target.value)} />
      </Field>
      <FormError msg={err} />
      <div className="row">
        <Busy className="btn primary" run={save} onError={setErr}>{week.checkin ? "Update my check-in" : "Save my check-in"}</Busy>
        {week.checkin && <span className="m-done">Checked in</span>}
      </div>
    </div>
  );
}

export default function Week() {
  const { me, refresh, toast } = useHub();
  const [d, reload, err] = useLoad("/week");
  const [hist, reloadHist] = useLoad("/week/history");
  const [ticking, setTicking] = useState(null);
  const postRef = useRef(null);
  // which of this week's posts (and today's calendar post) are already marked as posted
  const wk = d?.week?.week;
  const tp = d?.today_post;
  const [refs, reloadRefs] = usePostedRefs([wk ? `week:${wk}` : null, tp?.date ? `cal:${String(tp.date).slice(0, 7)}` : null], !!d);
  const head = { kicker: "Start · This week", id: "MB-01", title: <>Five minutes, <em>every</em> day.</>,
    sub: "Today's one action, this week's 7 posts and 3 actions. Check in on Friday and see your weekly score." };
  if (!d) return <Sheet {...head}>{err ? <FormError msg={err.message} /> : <Loading />}</Sheet>;

  const w = d.week;
  const s = d.streak;
  const done = s.done_today || d.today?.done;
  const post = d.today_post;
  const todayIdx = (new Date(`${d.date}T00:00:00`).getDay() + 6) % 7;
  const both = async () => { await Promise.all([reload(), reloadHist()]); };

  const markDone = async () => {
    const r = await api("/week/today/done", { method: "POST" });
    toast(r.streak.current > 1 ? `Done. ${r.streak.current} days in a row.` : "Done. Your streak has started.");
    await both();
  };
  const plan = async () => {
    await api("/week/plan", { method: "POST" });
    await both();
    refresh();
    toast("Your week is ready");
  };
  const rewrite = async () => {
    if (!window.confirm("Write new posts and 3 new actions for this week? This week's ticks are cleared, and it uses one AI run.")) return;
    await plan();
  };
  const toggle = async (n) => {
    setTicking(n);
    try { await api(`/week/actions/${n}`, { method: "POST" }); await both(); }
    catch (e) { if (!e.handled) toast(e.message, "err"); }
    finally { setTicking(null); }
  };
  const linkName = d.today?.link && d.today.link !== "/week" ? pageName(me, d.today.link) : null;
  // today's post is the calendar's post for today when there is one, else this week's post for today
  const todayRef = post ? (post.date ? `cal:${String(post.date).slice(0, 7)}:${post.date}`
    : w?.posts?.length ? `week:${w.week}:${Math.min(todayIdx, w.posts.length - 1)}` : null) : null;
  const canMarkToday = todayRef && (todayRef.startsWith("cal:") ? me?.features?.content_calendar : true);
  const doneActs = (w?.actions || []).filter((a) => a.done).length;

  return (
    <Sheet {...head}>
      <div className="grid2">
        <div className={`panel ${done ? "" : "active"}`}>
          <span className="label">Today's 5-minute action · {dateLabel(d.date, { weekday: "long", day: "numeric", month: "long" })}</span>
          <p className="m-today">{d.today?.text}</p>
          <div className="row">
            {done ? <span className="m-done">Done today</span> : <Busy className="btn primary" run={markDone}>Done</Busy>}
            {linkName && <Link className="btn" to={d.today.link}>Open {linkName}</Link>}
            {d.today?.link === "/week" && post && (
              <button className="btn" onClick={() => postRef.current?.scrollIntoView({ behavior: "smooth", block: "start" })}>See today's post</button>
            )}
          </div>
        </div>
        <div className="panel">
          <span className="label">Your streak</span>
          <div className="m-big"><span className="bigscore">{s.current}</span><span className="muted">{s.current === 1 ? "day" : "days"} in a row</span></div>
          <span className="small muted">
            Best: {plural(s.best, "day", "days")}. {done ? "Today is done — come back tomorrow." : s.current ? "Do today's action to keep it going." : "Do today's action to start one."}
          </span>
        </div>
      </div>

      {!w && (
        <div className="panel">
          <h3>Your week isn't planned yet</h3>
          <p className="muted">One AI run writes 7 posts and 3 actions from your Magic Number, your gaps and your leads. It's also written for you every Monday at 8am.</p>
          <div className="row"><Busy className="btn primary" run={plan}>Plan my week</Busy><span className="small muted">Uses one AI run.</span></div>
        </div>
      )}

      {post && (
        <div className="panel" ref={postRef} style={{ scrollMarginTop: 80 }}>
          <PostCard post={post} head={<div className="row between"><span className="label">Today's post</span>{post.occasion && <Pill kind="on">{post.occasion}</Pill>}</div>}>
            <a className="btn sm" href={waLink(postText(post))} target="_blank" rel="noreferrer">Open in WhatsApp</a>
          </PostCard>
          {canMarkToday && <PostActions postRef={todayRef} post={post} posted={refs[todayRef] || null} onChange={reloadRefs} />}
        </div>
      )}

      {w && (
        <div className="grid2">
          <div className="panel">
            <div className="row between"><span className="label">This week's 3 actions</span><span className="small muted">{doneActs} of {w.actions.length} done</span></div>
            <div>
              {w.actions.map((a, i) => (
                <label key={i} className={`m-check ${a.done ? "done" : ""}`}>
                  <input type="checkbox" checked={!!a.done} disabled={ticking !== null} onChange={() => toggle(i)} />
                  <span className="stack" style={{ gap: 4 }}><span className="m-text">{a.text}</span>{a.why && <span className="small muted">{a.why}</span>}</span>
                </label>
              ))}
            </div>
          </div>
          <div className="panel">
            <span className="label">This week's score</span>
            <div className="m-big"><span className="bigscore">{w.score}</span><span className="muted">out of 100</span></div>
            <div className="bar" role="img" aria-label={`${w.score} out of 100`}><i style={{ width: `${w.score}%` }} /></div>
            <span className="small muted">
              40 for the 3 actions, 40 for doing your daily action (6 days is full marks), 20 for the Friday check-in.
              {d.last_week_score != null && ` Last week: ${d.last_week_score}.`}
            </span>
            {d.friday ? <Checkin key={w.checkin?.at || "new"} week={w} onSaved={async () => { await both(); toast("Checked in"); }} />
              : <p className="small">From Friday, check in here: your inquiries, sales and wins. It takes 2 minutes and adds 20 points.</p>}
          </div>
        </div>
      )}

      {w?.posts?.length > 0 && (
        <div className="stack">
          <div className="row between">
            <h2>This week's posts</h2>
            <Busy className="btn sm ghost" run={rewrite}>Rewrite my week</Busy>
          </div>
          <div className="grid3">
            {w.posts.map((p, i) => (
              <div key={i} className={`panel ${i === todayIdx ? "active" : ""}`}>
                <PostCard post={p} head={<div className="row between"><span className="label">{p.day || `Day ${i + 1}`}</span>{i === todayIdx && <Pill kind="on">Today</Pill>}</div>}>
                  <PostActions postRef={`week:${w.week}:${i}`} post={p} posted={refs[`week:${w.week}:${i}`] || null} onChange={reloadRefs} />
                </PostCard>
              </div>
            ))}
          </div>
        </div>
      )}

      <div className="grid2">
        <div className="panel">
          <div className="row between"><span className="label">Your weekly scores</span>{d.last_week_score != null && <span className="small muted">Last week: {d.last_week_score}</span>}</div>
          {!hist ? <Loading /> : hist.length === 0 ? <p className="small muted">Your score for each week shows here, starting with this one.</p> : (
            <ScoreBars rows={hist.map((h) => ({
              key: h.week, value: h.score, dim: h.week !== w?.week,
              label: h.week === w?.week ? "This week" : `Week of ${dateLabel(h.start, { day: "numeric", month: "short" })}`,
              note: h.checkin ? <>{plural(h.checkin.leads, "inquiry", "inquiries")} · {plural(h.checkin.sales, "sale", "sales")}{h.checkin.revenue ? <> · <span className="money">{rs(h.checkin.revenue)}</span></> : null}</> : null,
            }))} />
          )}
        </div>
        <div className="panel">
          <span className="label">Coming up</span>
          {d.upcoming?.length ? (
            <div className="stack" style={{ gap: 10 }}>
              {d.upcoming.map((f) => (
                <div key={f.date} className="row between">
                  <span><b style={{ fontWeight: 500 }}>{f.name}</b> <span className="small muted">· {dateLabel(f.date)}</span></span>
                  <span className="small muted">{inDays(daysBetween(d.date, f.date))}</span>
                </div>
              ))}
              {me?.features?.content_calendar && <Link to="/calendar" className="small">See these in your content calendar</Link>}
            </div>
          ) : <Empty>No festivals in the list for the coming weeks.</Empty>}
        </div>
      </div>
    </Sheet>
  );
}
