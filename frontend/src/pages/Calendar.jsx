import React, { useMemo, useState } from "react";
import { api } from "../api.js";
import { Busy, Field, FormError, Sheet, canAct, useHub, useLoad } from "../ui.jsx";
import { Loading, Pill, Tabs } from "../v2.jsx";
import PostActions, { PostingSummary, usePostedRefs } from "./PostActions.jsx";
import { PostCard, dateLabel } from "./Week.jsx";
import "./member.css";

const DOW = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];
const istToday = () => new Date().toLocaleDateString("en-CA", { timeZone: "Asia/Kolkata" });

/** This month and the next two (India time), as [YYYY-MM, tab label]. */
function monthOptions() {
  const [y, m] = istToday().split("-").map(Number);
  return [0, 1, 2].map((i) => {
    const d = new Date(y, m - 1 + i, 1);
    const key = `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}`;
    return [key, d.toLocaleDateString("en-IN", d.getFullYear() === y ? { month: "long" } : { month: "long", year: "numeric" })];
  });
}

/** Mark as posted (with the platform and a link) and Make the design. The calendar's own tick follows the posting log. */
function Posted({ month, post, refs, onChange }) {
  const ref = `cal:${month}:${post.date}`;
  return <PostActions postRef={ref} post={post} posted={post.posted ? (refs[ref] || {}) : null} onChange={onChange} />;
}

function PostHead({ post, today }) {
  return (
    <div className="row between">
      <span className="label">{dateLabel(post.date, { weekday: "long", day: "numeric", month: "short" })}</span>
      <span className="row" style={{ gap: 6 }}>
        {post.date === today && <Pill kind="on">Today</Pill>}
        {post.occasion && <Pill kind="on">{post.occasion}</Pill>}
      </span>
    </div>
  );
}

function MonthGrid({ month, posts, fests, sel, onSel, today }) {
  const [y, m] = month.split("-").map(Number);
  const lead = (new Date(y, m - 1, 1).getDay() + 6) % 7;
  const n = new Date(y, m, 0).getDate();
  const cells = [...Array(lead).fill(null), ...Array.from({ length: n }, (_, i) => `${month}-${String(i + 1).padStart(2, "0")}`)];
  while (cells.length % 7) cells.push(null);
  const byDate = Object.fromEntries(posts.map((p) => [p.date, p]));
  return (
    <div className="m-cal-grid">
      {DOW.map((d) => <div key={d} className="dow">{d}</div>)}
      {cells.map((iso, i) => {
        if (!iso) return <div key={`b${i}`} className="m-cell blank" />;
        const p = byDate[iso];
        const occasion = p?.occasion || fests[iso];
        const cls = `m-cell ${iso === today ? "today" : ""} ${p?.posted ? "posted" : ""} ${sel === iso ? "on" : ""}`;
        const inner = (
          <>
            <span className="d"><span>{Number(iso.slice(8))}</span>{p?.posted && <span className="m-tick" title="Posted" />}</span>
            {occasion && <Pill kind="on">{occasion}</Pill>}
            {p?.hook && <span className="h">{p.hook}</span>}
          </>
        );
        return p ? (
          <button key={iso} className={cls} aria-pressed={sel === iso}
            aria-label={`${dateLabel(iso, { weekday: "long", day: "numeric", month: "long" })}${occasion ? `, ${occasion}` : ""}${p.posted ? ", posted" : ""}: ${p.hook}`}
            onClick={() => onSel(iso)}>{inner}</button>
        ) : <div key={iso} className={cls}>{inner}</div>;
      })}
    </div>
  );
}

function WriteForm({ label, slots, exists, month, onDone }) {
  const [focus, setFocus] = useState("");
  const [err, setErr] = useState("");
  const write = async () => {
    if (exists && !window.confirm(`Write all of ${label}'s posts again? The Posted marks are cleared, and it uses one AI run.`)) return;
    await api("/calendar", { method: "POST", body: { month, focus: focus.trim() } });
    await onDone();
  };
  return (
    <div className="stack">
      <Field label="Focus for the month (optional)" hint={`${slots} posts: every Monday, Wednesday and Friday, plus festival days. Uses one AI run.`}>
        <input className="input" maxLength={160} placeholder="e.g. Diwali home makeover offer" value={focus} onChange={(e) => setFocus(e.target.value)} />
      </Field>
      <FormError msg={err} />
      <div className="row"><Busy className="btn primary ai" run={write} onError={setErr}>{exists ? `Rewrite ${label}'s posts` : `Write ${label}'s posts`}</Busy></div>
    </div>
  );
}

export default function Calendar() {
  const { me, refresh, toast } = useHub();
  const opts = useMemo(monthOptions, []);
  const [month, setMonth] = useState(opts[0][0]);
  const [d, reload, err] = useLoad(`/calendar/${month}`);
  const [log, reloadLog] = useLoad("/social/log");
  const [refs, reloadRefs] = usePostedRefs([`cal:${month}`]);
  const [sel, setSel] = useState(null);
  const [rewrite, setRewrite] = useState(false);
  const [showPast, setShowPast] = useState(false);
  const today = istToday();
  const ready = d && d.month === month;
  const cal = ready ? d.calendar : null;
  const posts = cal?.posts || [];
  const fests = ready ? Object.fromEntries(d.festivals.map((f) => [f.date, f.name])) : {};
  const canWrite = canAct(me, "manager");
  const label = ready ? d.label : opts.find(([k]) => k === month)?.[1];
  const selected = posts.find((p) => p.date === sel) || posts.find((p) => p.date >= today) || posts[0];
  const posted = posts.filter((p) => p.posted).length;
  const earlier = posts.filter((p) => p.date < today).length;
  const listed = showPast ? posts : posts.filter((p) => p.date >= today);

  const pick = (k) => { setMonth(k); setSel(null); setRewrite(false); setShowPast(false); };
  const changed = async () => { await Promise.all([reload(), reloadRefs(), reloadLog()]); };
  const written = async () => { await reload(); refresh(); setRewrite(false); toast(`${label}'s posts are ready`); };

  return (
    <Sheet kicker="Scale · Content calendar" id="MB-02" title={<>A month of posts, <em>ready</em>.</>}
      sub="Posts for every Monday, Wednesday and Friday, plus Indian festivals and occasions. Next month's posts are written for you on the 25th at 9am.">
      <PostingSummary summary={log?.summary} />
      <Tabs tabs={opts} value={month} onChange={pick} />
      {!ready ? (err ? <FormError msg={err.message} /> : <Loading />) : (
        <>
          {d.festivals.length > 0 && (
            <div className="stack" style={{ gap: 8 }}>
              <span className="label">Festivals and occasions in {d.label}</span>
              <div className="row m-fests" style={{ gap: 8 }}>
                {d.festivals.map((f) => <Pill key={f.date} kind="on">{dateLabel(f.date, { day: "numeric", month: "short" })} · {f.name}</Pill>)}
              </div>
            </div>
          )}

          {!cal ? (
            <div className="panel">
              <h3>No posts for {d.label} yet</h3>
              {canWrite ? <WriteForm key={month} label={d.label} slots={d.slots} month={month} exists={false} onDone={written} />
                : <p className="muted">Ask the owner or a manager to write {d.label}'s posts. Next month's are written on the 25th at 9am.</p>}
            </div>
          ) : (
            <>
              <div className="row between">
                <span className="small muted">
                  {posted} of {posts.length} posted{cal.focus ? ` · Focus: ${cal.focus}` : ""}
                </span>
                {canWrite && !rewrite && <button className="btn sm ghost" onClick={() => setRewrite(true)}>Rewrite with a new focus</button>}
              </div>
              {rewrite && (
                <div className="panel">
                  <WriteForm key={month} label={d.label} slots={d.slots} month={month} exists onDone={written} />
                  <button className="btn sm ghost" style={{ justifySelf: "start" }} onClick={() => setRewrite(false)}>Cancel</button>
                </div>
              )}

              <div className="m-cal-wide">
                <MonthGrid month={month} posts={posts} fests={fests} sel={selected?.date} onSel={setSel} today={today} />
                {selected && (
                  <div className="panel active">
                    <PostCard post={selected} head={<PostHead post={selected} today={today} />}>
                      <Posted month={month} post={selected} refs={refs} onChange={changed} />
                    </PostCard>
                  </div>
                )}
              </div>

              <div className="m-cal-list">
                {earlier > 0 && !showPast && (
                  <button className="btn sm ghost" style={{ justifySelf: "start" }} onClick={() => setShowPast(true)}>
                    Show the {earlier} earlier {earlier === 1 ? "post" : "posts"}
                  </button>
                )}
                {listed.map((p) => (
                  <div key={p.date} className={`panel ${p.date === today ? "active" : ""} ${p.date < today && p.posted ? "m-faded" : ""}`}>
                    <PostCard post={p} head={<PostHead post={p} today={today} />}>
                      <Posted month={month} post={p} refs={refs} onChange={changed} />
                    </PostCard>
                  </div>
                ))}
              </div>
            </>
          )}
        </>
      )}
    </Sheet>
  );
}
