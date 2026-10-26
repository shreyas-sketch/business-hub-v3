import React, { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api, day, waLink } from "../api.js";
import { Busy, Copy, FormError, Locked, Sheet, useHub, useLoad } from "../ui.jsx";
import { Loading, Pill } from "../v2.jsx";
import "./free.css";

const ANSWERS = [["yes", "Yes"], ["partly", "Partly"], ["no", "No"]];
const ALIAS = { follow_up_agent: "automations" };  // features without a page of their own live on another page

/** The hub page that helps with a fix: from the owner's own menu, so names, order and locks match the sidebar. */
function FixLink({ feature }) {
  const { me } = useHub();
  const items = (me.menu || []).flatMap((g) => g.items);
  const item = items.find((i) => i.key === feature) || items.find((i) => i.key === ALIAS[feature]);
  if (!item) return null;
  return (
    <span className="row" style={{ gap: 8 }}>
      <Link className="btn sm" to={item.path}>Open {item.label}</Link>
      {item.locked && <Pill>Unlocks with {item.tier_name}</Pill>}
    </span>
  );
}

/** The six "your numbers" answers: asked with the questions but not scored, and optional here (the workshop flow stays
    as quick as before). When the three that matter are filled, the score shows what the owner is missing in rupees. */
function Numbers({ fields, numbers, setNumbers }) {
  const set = (k, v) => setNumbers({ ...numbers, [k]: v });
  return (
    <div className="grid2">
      {fields.map((n) => (
        <label className="field" key={n.key}><span className="label">{n.label}</span>
          {n.kind === "choice" ? (
            <select className="select" value={numbers[n.key] ?? ""} onChange={(e) => set(n.key, e.target.value)}>
              <option value="">Choose…</option>
              {n.choices.map((c) => <option key={c.value} value={c.value}>{c.label}</option>)}
            </select>
          ) : (
            <input className="input" type="number" inputMode="numeric" min={n.min} max={n.max} value={numbers[n.key] ?? ""}
              placeholder={n.key === "aov" ? "₹" : ""} onChange={(e) => set(n.key, e.target.value === "" ? "" : Number(e.target.value))} />
          )}
          {n.hint && <span className="hint">{n.hint}</span>}
        </label>
      ))}
    </div>
  );
}

/** The ₹99 webinar's report, once saved with the WhatsApp code. */
function WebinarReport() {
  const [mine] = useLoad("/day1/mine");
  const r = mine?.find((x) => x.status === "done");
  if (!r) return null;
  return (
    <div className="panel flat">
      <div className="row between"><span className="label">Your webinar report</span><span className="small muted">{day(r.created_at)}</span></div>
      <div className="row">
        <a className="btn sm" href={r.png_url} target="_blank" rel="noopener">Open the image</a>
        <a className="btn sm ghost" href={r.pdf_url} target="_blank" rel="noopener">Open the PDF</a>
      </div>
    </div>
  );
}

function Questions({ questions, answers, setAnswers }) {
  return (
    <div>
      {questions.map((q) => (
        <div className="fr-q" key={q.key}>
          <div className="stack" style={{ gap: 4 }}><span className="label">{q.area}</span><p>{q.q}</p></div>
          <div className="fr-seg" role="group" aria-label={q.area}>
            {ANSWERS.map(([v, l]) => (
              <button key={v} type="button" aria-pressed={answers[q.key] === v} onClick={() => setAnswers({ ...answers, [q.key]: v })}>{l}</button>
            ))}
          </div>
        </div>
      ))}
    </div>
  );
}

export default function Score() {
  const { me, toast } = useHub();
  const [data, reload] = useLoad("/score");
  const [answers, setAnswers] = useState({});
  const [numbers, setNumbers] = useState({});
  const [taking, setTaking] = useState(false);
  const [err, setErr] = useState("");
  const latest = data?.latest;
  useEffect(() => { if (latest?.answers) setAnswers(latest.answers); if (latest?.numbers) setNumbers(latest.numbers); }, [latest?.id]);  // eslint-disable-line react-hooks/exhaustive-deps

  if (!data) return <Sheet kicker="Structure · Business score" id="ST-01" pillar="Structure" step={1} title={<>Your Business AI <em>Score</em>.</>}><Loading /></Sheet>;

  const fields = data.numbers || [];
  const answered = data.questions.filter((q) => answers[q.key]).length;
  const total = data.questions.length;
  const showForm = !latest || taking;
  const submit = async () => {
    const filled = Object.fromEntries(Object.entries(numbers).filter(([, v]) => v !== "" && v != null));
    const r = await api("/score", { method: "POST", body: { answers, numbers: filled } });
    setTaking(false);
    await reload();
    toast(`Your score: ${r.score} out of 100`);
    window.scrollTo({ top: 0, behavior: "smooth" });
  };
  const p = latest?.parts;
  const passed = data.scan ? data.website_checks.filter((c) => data.scan.checks?.[c.key]).length : 0;
  const shareText = latest && `My business scored ${latest.score}/100 on the Business AI Score. See my card and get your own free score: ${latest.share_url}`;

  return (
    <Sheet kicker="Structure · Business score" id="ST-01" pillar="Structure" step={1}
      title={latest && !taking ? <>Your business scores <em>{latest.score}</em>.</> : <>Get your Business AI <em>Score</em>.</>}
      sub="10 quick questions about how your business runs. You get a score out of 100 and the 3 fixes that matter most. Worked out by the hub, so no AI token is used.">

      {latest && !taking && (
        <>
          <div className="grid2" style={{ alignItems: "start" }}>
            <div className="panel active">
              <div className="row between">
                <div className="fr-score"><span className="bigscore">{latest.score}</span><span className="of">/100</span></div>
                <Pill kind={latest.band === "Strong" ? "on" : ""}>{latest.band}</Pill>
              </div>
              <div className="bar" aria-hidden="true"><i style={{ width: `${latest.score}%` }} /></div>
              <span className="small muted">
                Your answers {p.questions} of {p.questions_out_of}{p.website != null ? ` · Website ${p.website} of ${p.website_out_of}` : ""} · {day(latest.at)}
              </span>
              {latest.change != null && (
                <p>{latest.change > 0 ? <>Up <b>{latest.change}</b> since last time.</> : latest.change < 0 ? <>Down <b>{-latest.change}</b> since last time.</> : "Same as last time."}</p>
              )}
              <span className="small muted">75 and above is Strong · 50 to 74 is Getting there · below 50 needs attention.</span>
              {latest.gap && (
                <div className="stack" style={{ gap: 4 }}>
                  <span className="label">What you're missing · Estimate</span>
                  <p style={{ margin: 0 }}>About <span className="money">₹{new Intl.NumberFormat("en-IN").format(latest.gap.year)}</span> a year in enquiries that go cold, at your numbers.</p>
                  <span className="small muted">Your enquiries a month × {latest.gap.cold_rate_pct}% that go cold × your close rate × your average order × 12.</span>
                </div>
              )}
            </div>
            <div className="panel">
              <span className="label">Your top 3 fixes</span>
              {latest.fixes.length ? (
                <div>{latest.fixes.map((f, i) => (
                  <div key={i} className="fr-fix">
                    <span className="n">{i + 1}</span>
                    <div className="stack" style={{ gap: 4 }}><b>{f.about}</b><span className="small">{f.fix}</span></div>
                    <FixLink feature={f.feature} />
                  </div>
                ))}</div>
              ) : <p className="muted">Nothing to fix right now. Keep it up.</p>}
            </div>
          </div>

          <div className="panel">
            <span className="label">Share your score</span>
            <p className="small muted">A card with your score and your 3 fixes. Owners who open it can take their own free score.</p>
            <div className="inline-add"><input className="input mono" readOnly value={latest.share_url} aria-label="Your score card link" /><Copy text={latest.share_url} /></div>
            <div className="row">
              <a className="btn sm" href={waLink(shareText)} target="_blank" rel="noreferrer">Share on WhatsApp</a>
              <a className="btn sm ghost" href={latest.share_url} target="_blank" rel="noopener">Open my card</a>
            </div>
          </div>
        </>
      )}

      <WebinarReport />

      {data.scan ? (
        <div className="panel flat">
          <div className="row between">
            <span className="label">Website · {data.scan.url.replace(/^https?:\/\//, "")}</span>
            <span className="small muted">{passed} of {data.website_checks.length} checks · counts for 40 of the 100</span>
          </div>
          <ul className="fr-checks">
            {data.website_checks.map((c) => (
              <li key={c.key}>
                <span className={data.scan.checks?.[c.key] ? "ok" : "no"} aria-hidden="true">{data.scan.checks?.[c.key] ? "✓" : "✕"}</span>
                <span>{c.label}<span className="fr-sr">{data.scan.checks?.[c.key] ? ": yes" : ": not yet"}</span></span>
              </li>
            ))}
          </ul>
          {latest && !latest.website && <span className="small muted">Read on {day(data.scan.at)} — your next score will include it.</span>}
        </div>
      ) : (
        <p className="small muted">Have a website of your own? <Link to="/start?way=website">Let the hub read it</Link> — 8 quick checks on it then count for 40 of your 100.</p>
      )}

      {showForm && (
        <div className="panel">
          <div className="row between">
            <span className="label">{latest ? "Re-check your score" : "Answer honestly — yes, partly or no"}</span>
            <span className="small muted">{answered} of {total} answered</span>
          </div>
          <Questions questions={data.questions} answers={answers} setAnswers={setAnswers} />
          {fields.length > 0 && <>
            <span className="label" style={{ marginTop: 8 }}>Your numbers (optional) — not scored; they show what you're missing in rupees</span>
            <Numbers fields={fields} numbers={numbers} setNumbers={setNumbers} />
          </>}
          <FormError msg={err} />
          <div className="row">
            <Busy className="btn primary" disabled={answered < total} onError={setErr} run={submit}>{latest ? "Re-check my score" : "Work out my score"}</Busy>
            {taking && <button className="btn ghost" onClick={() => { setTaking(false); setErr(""); }}>Cancel</button>}
          </div>
          {!latest && !me.features.score_recheck && <span className="small muted">Your first score is free. Re-checking it every month is part of {data.recheck_tier}.</span>}
        </div>
      )}

      {latest && !taking && (data.can_retake
        ? <div className="row panel flat" style={{ padding: "12px 16px" }}>
            <span className="small muted" style={{ flex: "1 1 200px" }}>Fixed something? Re-check your score and see what moved.</span>
            <button className="btn sm" onClick={() => { setErr(""); setTaking(true); }}>Re-check my score</button>
          </div>
        : <Locked feature="score_recheck" what="Re-check your score every month" />)}

      {data.history.length > 1 && (
        <div className="stack">
          <span className="label">Your scores</span>
          <table className="t cards">
            <thead><tr><th>Date</th><th>Score</th><th>Band</th><th>Change</th></tr></thead>
            <tbody>{data.history.map((h) => (
              <tr key={h.id}>
                <td data-l="Date">{day(h.at)}</td>
                <td data-l="Score"><span><b>{h.score}</b><span className="muted">/100</span></span></td>
                <td data-l="Band">{h.band}</td>
                <td data-l="Change" className="muted">{h.change == null ? "First score" : h.change > 0 ? `+${h.change}` : h.change < 0 ? h.change : "No change"}</td>
              </tr>
            ))}</tbody>
          </table>
        </div>
      )}
    </Sheet>
  );
}
