import React, { useEffect, useMemo } from "react";
import { Link, useNavigate, useParams, useSearchParams } from "react-router-dom";
import { api, day } from "../api.js";
import { Busy, Sheet, canAct, useHub, useLoad } from "../ui.jsx";

const URL_RE = /((?:https?:\/\/|www\.)[^\s<>"']+)/g;
const TRAIL = /[.,;:!?)\]}'"]+$/;

/** Notes keep their line breaks; web addresses in them become links (a full stop after one stays outside it). */
export function Notes({ text }) {
  if (!text) return null;
  const parts = text.split(URL_RE);
  return (
    <div className="rec-notes">
      {parts.map((p, i) => {
        if (i % 2 === 0) return <React.Fragment key={i}>{p}</React.Fragment>;
        const tail = (p.match(TRAIL) || [""])[0];
        const url = tail ? p.slice(0, -tail.length) : p;
        return (
          <React.Fragment key={i}>
            <a href={url.startsWith("www.") ? `https://${url}` : url} target="_blank" rel="noopener noreferrer">{url.replace(/^https?:\/\//, "")}</a>{tail}
          </React.Fragment>
        );
      })}
    </div>
  );
}

/** Streams from the service that hosts the video; the hub only shows its player. */
export function Player({ video, title }) {
  if (!video) return null;
  if (video.kind === "iframe") {
    return (
      <div className="player">
        <iframe key={video.embed} src={video.embed} title={title} loading="lazy" referrerPolicy="strict-origin-when-cross-origin"
          allow="autoplay; fullscreen; picture-in-picture; encrypted-media; clipboard-write" allowFullScreen />
      </div>
    );
  }
  if (video.kind === "video") {
    return <div className="player"><video key={video.embed} src={video.embed} controls preload="metadata" playsInline controlsList="nodownload" aria-label={title} /></div>;
  }
  let host = "";
  try { host = new URL(video.url).hostname.replace(/^www\./, ""); } catch { /* shown without a host */ }
  return (
    <div className="player link">
      <div className="stack" style={{ justifyItems: "center", textAlign: "center" }}>
        <span className="label">This recording opens {host ? `on ${host}` : "in a new tab"}</span>
        <a className="btn primary" href={video.url} target="_blank" rel="noopener noreferrer">Open the recording</a>
      </div>
    </div>
  );
}

const minutes = (m) => (m ? (m >= 60 ? `${Math.floor(m / 60)} h ${m % 60 ? `${m % 60} min` : ""}`.trim() : `${m} min`) : "");

function ProgramList() {
  const { me, upgrade } = useHub();
  const [list] = useLoad("/recordings");
  const owner = canAct(me, "owner");
  return (
    <Sheet kicker="Learn · Recordings" id="RC-01" pillar="Learn" title={<>Every call, ready to <em>rewatch</em>.</>}
      sub="Recordings of your program's calls, with notes and resources, in the order they happened. Pick up where you left off.">
      {!list ? null : !list.length ? (
        <div className="empty"><p className="muted">{owner ? "No recordings yet. When your program's calls are recorded, they appear here." : "No recordings have been shared with your team yet."}</p></div>
      ) : (
        <div className="grid2">
          {list.filter((p) => !(p.locked && me.ui?.menu === "engines" && !["free", "lite"].includes(p.tier))).map((p) => {   // v2.3: no older plans on sale
            const pct = p.recordings ? Math.round((p.watched / p.recordings) * 100) : 0;
            return (
              <div key={p.id} className={`panel ${p.locked ? "flat" : ""}`}>
                <div className="row between">
                  <span className={`token ${p.locked ? "money" : "grey"}`}>{p.locked ? `Part of ${p.tier_name}` : p.added ? "Added for you" : p.tier === "free" ? "Free for everyone" : p.tier_name}</span>
                  <span className="small muted">{p.recordings} recording{p.recordings === 1 ? "" : "s"}</span>
                </div>
                <h2>{p.title}</h2>
                {p.description && <p className="muted">{p.description}</p>}
                {p.locked ? (
                  owner && <div><button className="btn money" onClick={() => upgrade(p.tier, "recordings")}>See {p.tier_name}</button></div>
                ) : (
                  <>
                    <div className="meter" role="img" aria-label={`${p.watched} of ${p.recordings} watched`}><i style={{ width: `${pct}%` }} /></div>
                    <div className="row between">
                      <span className="small muted">{p.watched} of {p.recordings} watched</span>
                      <Link className="btn primary" to={`/recordings/${p.id}`}>{p.watched ? "Continue" : "Start watching"}</Link>
                    </div>
                  </>
                )}
              </div>
            );
          })}
        </div>
      )}
    </Sheet>
  );
}

function ProgramView({ id }) {
  const { toast } = useHub();
  const navigate = useNavigate();
  const [params, setParams] = useSearchParams();
  const [p, reload, error] = useLoad(`/recordings/${id}`);
  const flat = useMemo(() => (p ? p.sections.flatMap((s) => s.recordings.map((r) => ({ ...r, section: s.title }))) : []), [p]);
  const wanted = params.get("r");
  const current = flat.find((r) => r.id === wanted) || flat.find((r) => !r.watched) || flat[0];
  const idx = current ? flat.findIndex((r) => r.id === current.id) : -1;
  useEffect(() => { if (error?.status === 404 || error?.status === 403) navigate("/recordings", { replace: true }); }, [error, navigate]);
  const pick = (r) => { setParams({ r: r.id }, { replace: false }); window.scrollTo({ top: 0, behavior: "smooth" }); };
  if (!p) return null;
  return (
    <Sheet kicker="Learn · Recordings" id="RC-02" pillar="Learn" title={<>{p.title}</>}
      sub={p.description || `${p.recordings} recording${p.recordings === 1 ? "" : "s"}`}
      actions={<Link className="btn sm ghost" to="/recordings">All programs</Link>}>
      {!current ? <div className="empty"><p className="muted">No recordings in this program yet.</p></div> : (
        <div className="rec-layout">
          <div className="stack" style={{ gap: 16, minWidth: 0 }}>
            <Player video={current.video} title={current.title} />
            <div className="panel">
              <span className="label">{current.section}</span>
              <h2>{current.title}</h2>
              <div className="row small muted" style={{ gap: 14 }}>
                {current.recorded_on && <span>{day(current.recorded_on)}</span>}
                {current.duration_min && <span>{minutes(current.duration_min)}</span>}
                <span>{current.video.provider_name}</span>
              </div>
              <div className="row">
                <Busy className={`btn sm ${current.watched ? "ghost" : ""}`} run={async () => {
                  await api(`/recordings/item/${current.id}/watched`, { method: "POST", body: { watched: !current.watched } });
                  await reload();
                  if (!current.watched) toast("Marked as watched");
                }}>{current.watched ? "Watched ✓ — mark unwatched" : "Mark as watched"}</Busy>
                {idx > 0 && <button className="btn sm ghost" onClick={() => pick(flat[idx - 1])}>Previous</button>}
                {idx < flat.length - 1 && <button className="btn sm ghost" onClick={() => pick(flat[idx + 1])}>Next</button>}
              </div>
              {current.notes && <><span className="label">Notes</span><Notes text={current.notes} /></>}
              {current.resources?.length > 0 && (
                <>
                  <span className="label">Resources</span>
                  <div className="stack" style={{ gap: 6 }}>
                    {current.resources.map((x, i) => <a key={i} href={x.url} target="_blank" rel="noopener noreferrer">{x.label} ↗</a>)}
                  </div>
                </>
              )}
            </div>
          </div>
          <div className="stack rec-list">
            <div className="row between">
              <span className="label">{p.watched} of {p.recordings} watched</span>
            </div>
            <div className="meter"><i style={{ width: `${p.recordings ? Math.round((p.watched / p.recordings) * 100) : 0}%` }} /></div>
            {p.sections.map((s) => (
              <div key={s.id} className="stack" style={{ gap: 6 }}>
                <span className="label" style={{ marginTop: 8 }}>{s.title}</span>
                <div className="listbox">
                  {s.recordings.map((r) => (
                    <button key={r.id} className={r.id === current.id ? "on" : ""} onClick={() => pick(r)} aria-current={r.id === current.id ? "true" : undefined}>
                      <span className="row between" style={{ gap: 8, flexWrap: "nowrap" }}>
                        <span>{r.title}</span>
                        {r.watched && <span className="small" style={{ color: "var(--aqua)" }} aria-label="Watched">✓</span>}
                      </span>
                      <span className="small muted">{[r.recorded_on && day(r.recorded_on), minutes(r.duration_min)].filter(Boolean).join(" · ") || r.video.provider_name}</span>
                    </button>
                  ))}
                </div>
              </div>
            ))}
          </div>
        </div>
      )}
    </Sheet>
  );
}

export default function Recordings() {
  const { id } = useParams();
  return id ? <ProgramView id={id} /> : <ProgramList />;
}
