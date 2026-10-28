import React, { useState } from "react";
import { Link } from "react-router-dom";
import { api, day } from "../api.js";
import { Busy, FormError, LoadError, Locked, Sheet, useHub, useLoad } from "../ui.jsx";
import { Loading } from "../v2.jsx";
import { ScoreBars } from "./Week.jsx";
import "./member.css";

const SCALE = [[1, "Never"], [2, "Rarely"], [3, "Sometimes"], [4, "Often"], [5, "Always"]];
const WORD = Object.fromEntries(SCALE);
const KILLERS = ["Strategic focus", "Margins", "Sales consistency", "Payments"];
const verdict = (s) => (s < 50 ? "Needs work" : s < 75 ? "Getting there" : "Strong");

/** Where a fix is done in the hub: its page in the menu, or the AI tools / Automations page for tools and automations. */
function whereTo(me, feature) {
  const items = (me?.menu || []).flatMap((g) => g.items);
  const label = me?.feature_info?.[feature]?.label || "this";
  let path = items.find((i) => i.key === feature)?.path;
  if (!path && feature.startsWith("tool_")) {
    const tools = items.find((i) => i.key === "ai_tools");
    if (tools) path = `${tools.path}?tool=${feature.slice(5)}`;
  }
  if (!path && feature.endsWith("_agent")) path = items.find((i) => i.key === "automations")?.path;
  return { label, path, locked: !me?.features?.[feature] };
}

function Scan({ questions, initial, onDone, onCancel }) {
  const [ans, setAns] = useState(() => (initial ? { ...initial } : {}));
  const [err, setErr] = useState("");
  const n = questions.filter((q) => ans[q.key]).length;
  const areas = [...new Set(questions.map((q) => q.area))];
  const submit = async () => { onDone(await api("/gaps", { method: "POST", body: { answers: ans } })); };
  return (
    <div className="stack">
      <p className="small muted">
        {initial ? "Your last answers are filled in — change what has moved since. " : ""}
        Answer for how things are today, not how you'd like them to be. 1 means never, 5 means always.
      </p>
      {areas.map((a) => (
        <div key={a} className="panel">
          <span className="label">{a}{KILLERS.includes(a) ? " · silent killer" : ""}</span>
          <div>
            {questions.filter((q) => q.area === a).map((q) => (
              <div key={q.key} className="m-q">
                <p id={`gq-${q.key}`}>{q.q}</p>
                <div className="stack" style={{ gap: 6 }}>
                  <div className="m-rate" role="group" aria-labelledby={`gq-${q.key}`}>
                    {SCALE.map(([v, w]) => (
                      <button key={v} type="button" aria-pressed={ans[q.key] === v} aria-label={`${v} — ${w}`} title={w}
                        onClick={() => setAns({ ...ans, [q.key]: v })}>{v}</button>
                    ))}
                  </div>
                  <div className="m-scale"><span>Never</span><span>{ans[q.key] > 1 && ans[q.key] < 5 ? WORD[ans[q.key]] : ""}</span><span>Always</span></div>
                </div>
              </div>
            ))}
          </div>
        </div>
      ))}
      <FormError msg={err} />
      <div className="row">
        <Busy className="btn primary" disabled={n < questions.length} run={submit} onError={setErr}>See my gaps</Busy>
        {onCancel && <button className="btn ghost" onClick={onCancel}>Cancel</button>}
        <span className="small muted">{n} of {questions.length} answered</span>
      </div>
    </div>
  );
}

function Result({ scan, history, onRetake }) {
  const { me } = useHub();
  const c = scan.change;
  const changeText = c == null ? "Your first scan. Take it again next month to see what moved."
    : c > 0 ? `Up ${c} points since your last scan.` : c < 0 ? `Down ${-c} points since your last scan.` : "The same as your last scan.";
  const others = Object.keys(scan.areas || {}).filter((a) => !KILLERS.includes(a));
  return (
    <>
      <div className="grid2">
        <div className="panel active">
          <span className="label">Your score · {day(scan.at)}</span>
          <div className="m-big"><span className="bigscore">{scan.score}</span><span className="muted">out of 100</span></div>
          <span className="small">{changeText}</span>
          <div className="row"><button className="btn" onClick={onRetake}>Take the scan again</button></div>
        </div>
        <div className="panel">
          <span className="label">Your scans</span>
          {history?.length > 0 ? (
            <ScoreBars rows={history.map((h, i) => ({ key: h.at, label: i === 0 ? `Latest · ${day(h.at)}` : day(h.at), value: h.score, dim: i > 0 }))} />
          ) : <p className="small muted">Each scan you take shows here.</p>}
        </div>
      </div>

      <div className="stack">
        <h2>The 4 silent killers</h2>
        <p className="sub">These quietly drain most small businesses. Fix the weakest first.</p>
        <div className="m-tiles">
          {KILLERS.map((a) => {
            const s = scan.silent_killers?.[a] ?? scan.areas?.[a] ?? 0;
            return (
              <div key={a}>
                <span className="m-step">{a}</span>
                <span className="num">{s}</span>
                <div className="bar" role="img" aria-label={`${a}: ${s} out of 100`}><i style={{ width: `${s}%` }} /></div>
                <span className="small muted">{verdict(s)}</span>
              </div>
            );
          })}
        </div>
      </div>

      {others.length > 0 && (
        <div className="panel">
          <span className="label">The other areas</span>
          <ScoreBars rows={others.map((a) => ({ key: a, label: a, value: scan.areas[a], note: verdict(scan.areas[a]) }))} />
        </div>
      )}

      <div className="stack">
        <h2>This month's 3 fixes</h2>
        {scan.fixes?.length ? scan.fixes.map((f, i) => {
          const w = whereTo(me, f.feature);
          return (
            <div key={i} className="panel">
              <div className="m-fix">
                <span className="n">{i + 1}</span>
                <div className="stack" style={{ gap: 8 }}>
                  <span className="label">{f.area} · you said “{WORD[f.rating]?.toLowerCase()}”</span>
                  <p>{f.fix}</p>
                  {w.locked ? <Locked feature={f.feature} what={`${w.label} helps with this`} />
                    : w.path && <div className="row"><Link className="btn sm" to={w.path}>Open {w.label}</Link></div>}
                </div>
              </div>
            </div>
          );
        }) : <div className="panel flat empty"><p className="muted">No big gaps — you answered 4 or 5 on every line. Take the scan again next month to keep it that way.</p></div>}
      </div>
    </>
  );
}

export default function Gaps() {
  const [d, reload, err] = useLoad("/gaps");
  const [result, setResult] = useState(null);
  const [taking, setTaking] = useState(false);
  const latest = result || d?.latest;
  const done = async (r) => {
    setResult(r);
    setTaking(false);
    await reload();
    window.scrollTo({ top: 0, behavior: "smooth" });
  };
  return (
    <Sheet kicker="Structure · Gaps scan" id="MB-07" title={<>Find the <em>silent</em> killers.</>}
      sub="12 honest answers, about 2 minutes. You get a score for 8 areas of your business and this month's 3 fixes.">
      {!d ? (err ? <LoadError err={err} /> : <Loading />)
        : taking || !latest ? <Scan questions={d.questions} initial={latest?.answers} onDone={done} onCancel={latest ? () => setTaking(false) : null} />
        : <Result scan={latest} history={d.history} onRetake={() => { setTaking(true); window.scrollTo({ top: 0 }); }} />}
    </Sheet>
  );
}
