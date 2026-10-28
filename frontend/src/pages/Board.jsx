import React, { useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../api.js";
import { LoadError, Sheet, Stat, Switch, useHub, useLoad } from "../ui.jsx";
import { Empty, Loading, Pill } from "../v2.jsx";
import "./member.css";

const days = (n) => `${n} ${n === 1 ? "day" : "days"}`;

function Row({ r }) {
  return (
    <tr className={r.me ? "m-me" : ""}>
      <td data-l="Rank" style={{ width: 48 }}>{r.rank}</td>
      <td data-l="Business">
        <span>
          <b style={{ fontWeight: 500 }}>{r.business}</b>{r.city && <span className="small muted"> · {r.city}</span>}
          {r.me && <> <Pill kind="on">You</Pill></>}
        </span>
      </td>
      <td data-l="This week">
        <span className="m-score" title={`Weekly score ${r.score} of 100`}>
          <span className="bar" aria-hidden="true"><i style={{ width: `${r.score}%` }} /></span>{r.score}
        </span>
      </td>
      <td data-l="Streak"><span>{days(r.streak)} <span className="small muted">· best {r.best}</span></span></td>
    </tr>
  );
}

export default function Board() {
  const { toast } = useHub();
  const [d, reload, err] = useLoad("/board");
  const [busy, setBusy] = useState(false);
  const setShown = async (show) => {
    setBusy(true);
    try {
      await api("/board/visibility", { method: "POST", body: { hide: !show } });
      await reload();
      toast(show ? "Your business shows on the board" : "Your business is hidden from the board");
    } catch (e) { if (!e.handled) toast(e.message, "err"); }
    finally { setBusy(false); }
  };
  const rows = d?.rows || [];
  const meOutside = d?.me && !rows.some((r) => r.me);

  return (
    <Sheet kicker="Grow · Cohort board" id="MB-04" title={<>Who showed up <em>this</em> week.</>}
      sub="Owners from your workshop batch, ranked by this week's score, then by streak. Scores start again every Monday.">
      {!d ? (err ? <LoadError err={err} /> : <Loading />) : !d.cohort ? (
        <Empty>
          The cohort board shows owners from the same workshop batch. Your account isn't linked to a batch — that happens when you sign up
          from your workshop's link. Your streak and weekly score still count on <Link to="/week">This week</Link>.
        </Empty>
      ) : (
        <>
          <div className="row between">
            <span className="label">{d.cohort.name}</span>
            <Switch checked={!d.hidden} disabled={busy} onChange={setShown} label="Show my business on the board" />
          </div>
          {d.hidden && (
            <div className="notice"><span className="small">Your business is hidden. Others in your batch can't see your name, score or streak.</span></div>
          )}
          {d.me && (
            <div className="statgrid">
              <Stat label="Your place" value={`#${d.me.rank}`} note={rows.length < 30 ? `of ${rows.length} owners on the board` : "in your batch this week"} />
              <Stat label="Your score this week" value={d.me.score} note="out of 100" />
              <Stat label="Your streak" value={days(d.me.streak)} note={`best ${days(d.me.best)}`} />
            </div>
          )}
          {rows.length === 0 ? (
            <Empty>No one from your batch is on the board yet. Do today's action on <Link to="/week">This week</Link> to be first.</Empty>
          ) : (
            <table className="t cards">
              <thead><tr><th>#</th><th>Business</th><th>This week</th><th>Streak</th></tr></thead>
              <tbody>
                {rows.map((r) => <Row key={r.rank} r={r} />)}
                {meOutside && <Row r={d.me} />}
              </tbody>
            </table>
          )}
          <p className="small muted">
            The score: 40 for the week's 3 actions, 40 for doing the daily action, 20 for the Friday check-in. The streak counts days in a row with
            the daily action done. Only your business name, city, score and streak show — never your leads, sales or money.
          </p>
        </>
      )}
    </Sheet>
  );
}
