import React, { useEffect, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { ago, api, tokenSpan, waLink } from "../api.js";
import { Busy, Copy, Field, FormError, LoadError, Sheet, useHub, useLoad } from "../ui.jsx";
import { Empty, Loading, Pill } from "../v2.jsx";
import "./expenses.css";
import "./data.css";

/* v2.3 Data engine — Key-person knowledge: what one key employee knows, written down as an AI agent skill, a role card and
   SOPs, so the business doesn't depend on one person. Owner only. */

const first = (n) => (n || "").split(" ")[0] || "them";
const STATUS = {
  new: ["Not started", ""], owner_questions: ["Your answers needed", "warn"], owner_answered: ["Questionnaire next", "warn"],
  sent: ["Waiting for answers", ""], answered: ["Answers in", "on"], skill: ["Skill ready", "on"],
};
const SECTIONS = ["Daily and weekly work", "Step-by-step processes", "Decisions", "Customers, suppliers and contacts", "Tools and systems",
  "Problems and fixes", "Only they know"];

function AddPerson({ onAdded, onCancel }) {
  const [f, setF] = useState({ name: "", role: "", department: "" });
  const [err, setErr] = useState("");
  return (
    <div className="panel active">
      <span className="label">Which employee does your business depend on most?</span>
      <div className="grid2">
        <Field label="Their name"><input className="input" maxLength={60} value={f.name} onChange={(e) => setF({ ...f, name: e.target.value })} /></Field>
        <Field label="Their role" hint="For example: Accounts executive, Dispatch supervisor"><input className="input" maxLength={60} value={f.role} onChange={(e) => setF({ ...f, role: e.target.value })} /></Field>
      </div>
      <Field label="Department (optional)"><input className="input" maxLength={60} value={f.department} onChange={(e) => setF({ ...f, department: e.target.value })} /></Field>
      <FormError msg={err} />
      <div className="row">
        <Busy className="btn primary" disabled={f.name.trim().length < 2 || f.role.trim().length < 2} onError={(e) => setErr(e.message)}
          run={async () => onAdded(await api("/key-person", { method: "POST", body: f }))}>Add</Busy>
        {onCancel && <button className="btn ghost" onClick={onCancel}>Cancel</button>}
      </div>
    </div>
  );
}

function Step({ n, title, off, children }) {
  return (
    <section className={`panel kp-step ${off ? "off" : ""}`}>
      <span className="row" style={{ gap: 0 }}><span className="num">{n}</span><b>{title}</b></span>
      {children}
    </section>
  );
}

function OwnerStep({ p, set }) {
  const [answers, setAnswers] = useState(() => p.owner_answers.map((x) => x.a));
  useEffect(() => setAnswers(p.owner_answers.map((x) => x.a)), [p.owner_answers]);
  const [err, setErr] = useState("");
  const name = first(p.name);
  if (!p.owner_questions.length) {
    return (
      <Step n={1} title={`Tell us about ${name}`}>
        <p className="small">The hub writes up to 5 questions about {name}'s work for you. Your answers shape the questions {name} gets next.</p>
        <FormError msg={err} />
        <Busy className="btn primary ai" onError={(e) => setErr(e.message)} run={async () => set(await api(`/key-person/${p.id}/owner-questions`, { method: "POST" }))}>
          Write my questions · {p.costs.owner_questions} token</Busy>
      </Step>
    );
  }
  return (
    <Step n={1} title={`Tell us about ${name}`}>
      {p.owner_questions.map((q, i) => (
        <div key={q} className="kp-q">
          <span className="small"><b>{q}</b></span>
          <textarea className="textarea" maxLength={1500} value={answers[i] || ""} onChange={(e) => setAnswers(answers.map((a, j) => (j === i ? e.target.value : a)))} />
        </div>
      ))}
      <FormError msg={err} />
      <div className="row">
        <Busy className="btn" onError={(e) => setErr(e.message)} run={async () => set(await api(`/key-person/${p.id}/owner-answers`, { method: "PUT", body: { answers } }))}>Save my answers</Busy>
        <span className="small muted">Never write passwords or personal details — only how the work is done.</span>
      </div>
    </Step>
  );
}

function Questions({ p, set }) {
  const [edit, setEdit] = useState(null);
  const [err, setErr] = useState("");
  if (!edit) {
    let sec = null;
    return (
      <details className="ex-more">
        <summary>See the {p.questions.length} questions{p.submitted_at ? "" : " (you can change them)"}</summary>
        <ol className="stack" style={{ paddingLeft: 18 }}>
          {p.questions.map((q) => {
            const head = q.section !== sec ? (sec = q.section) : null;
            return <li key={q.key}>{head && <div className="kp-sec">{head}</div>}<span className="small">{q.q}</span>{q.hint && <div className="small muted">{q.hint}</div>}</li>;
          })}
        </ol>
        {!p.submitted_at && <button className="btn sm" onClick={() => setEdit(p.questions.map((q) => ({ ...q })))}>Change the questions</button>}
      </details>
    );
  }
  return (
    <div className="stack">
      {edit.map((q, i) => (
        <div key={i} className="kp-q">
          <div className="row between">
            <select className="select" style={{ maxWidth: 280 }} value={q.section} aria-label="Section" onChange={(e) => setEdit(edit.map((x, j) => (j === i ? { ...x, section: e.target.value } : x)))}>
              {SECTIONS.map((s) => <option key={s}>{s}</option>)}
            </select>
            <button className="btn sm ghost" onClick={() => setEdit(edit.filter((_, j) => j !== i))}>Remove</button>
          </div>
          <textarea className="textarea" maxLength={400} value={q.q} onChange={(e) => setEdit(edit.map((x, j) => (j === i ? { ...x, q: e.target.value } : x)))} />
        </div>
      ))}
      <FormError msg={err} />
      <div className="row">
        <button className="btn sm" disabled={edit.length >= 25} onClick={() => setEdit([...edit, { section: SECTIONS[0], q: "", hint: "" }])}>Add a question</button>
        <Busy className="btn primary" onError={(e) => setErr(e.message)}
          run={async () => { set(await api(`/key-person/${p.id}/questionnaire`, { method: "PUT", body: { questions: edit.filter((q) => q.q.trim()) } })); setEdit(null); }}>Save the questions</Busy>
        <button className="btn ghost" onClick={() => setEdit(null)}>Cancel</button>
      </div>
    </div>
  );
}

function QuestionnaireStep({ p, set }) {
  const [err, setErr] = useState("");
  const name = first(p.name);
  const ready = p.owner_answers.some((x) => x.a);
  const msg = `Hi ${name}, we're writing down how the work is done so the team and our AI assistant can help you. Please answer these questions on your phone, in your own words: ${p.link}`;
  return (
    <Step n={2} title={`${name}'s questionnaire`} off={!ready}>
      {!p.questions.length ? (
        <>
          <p className="small">12 to 20 questions shaped from your answers: {name}'s daily work, the steps, the decisions, who they deal with and what goes wrong. Never passwords or anything personal.</p>
          <FormError msg={err} />
          <Busy className="btn primary ai" disabled={!ready} onError={(e) => setErr(e.message)} run={async () => set(await api(`/key-person/${p.id}/questionnaire`, { method: "POST" }))}>
            Write {name}'s questionnaire · {p.costs.questionnaire} token</Busy>
          {!ready && <span className="small muted">Answer the questions in step 1 first.</span>}
        </>
      ) : (
        <>
          <p className="small">Send {name} this link. It opens on their phone — no login — and their answers save as they type. It works until {new Date(p.link_expires_at).toLocaleDateString("en-IN", { day: "numeric", month: "short" })}.</p>
          {p.link_expired ? <p className="small"><Pill kind="warn">Expired</Pill> Make a new link to send again.</p> : (
            <div className="kp-link">
              <input className="input mono" readOnly value={p.link} aria-label="Link for the questionnaire" onFocus={(e) => e.target.select()} />
              <Copy text={p.link} />
              <a className="btn sm" href={waLink(msg)} target="_blank" rel="noreferrer">Send on WhatsApp</a>
            </div>
          )}
          <div className="row">
            <a className="btn sm ghost" href={`/api/key-person/${p.id}/questions.pdf`}>Download the questions (PDF)</a>
            <Busy className="btn sm ghost" run={async () => set(await api(`/key-person/${p.id}/link`, { method: "POST" }))}>New link</Busy>
            <span className="small muted">{p.submitted_at ? `Answered ${ago(p.submitted_at)}` : p.opened_at ? `Opened ${ago(p.opened_at)}` : "Not opened yet"}</span>
          </div>
          <Questions p={p} set={set} />
        </>
      )}
    </Step>
  );
}

function AnswersStep({ p, set }) {
  const name = first(p.name);
  const [typing, setTyping] = useState(false);
  const [vals, setVals] = useState(() => Object.fromEntries(p.answers.map((a) => [a.key, a.a])));
  const [err, setErr] = useState("");
  useEffect(() => setVals(Object.fromEntries(p.answers.map((a) => [a.key, a.a]))), [p.answers]);
  if (!p.questions.length) return <Step n={3} title="The answers" off><p className="small muted">The answers come in here.</p></Step>;
  if (p.submitted_at) {
    return (
      <Step n={3} title="The answers">
        <p className="small">{p.submitted_by === "owner" ? "You typed the answers" : `${name} sent the answers`} {ago(p.submitted_at)} · {p.answered} of {p.questions.length} answered.</p>
        <details className="ex-more"><summary>Read the answers</summary>
          <div className="stack">{p.answers.filter((a) => a.a).map((a) => <div key={a.key} className="kp-q"><b className="small">{a.q}</b><span className="small" style={{ whiteSpace: "pre-wrap" }}>{a.a}</span></div>)}</div>
        </details>
        <Busy className="btn sm ghost" run={async () => set(await api(`/key-person/${p.id}/reopen`, { method: "POST" }))}>Let {name} change them</Busy>
      </Step>
    );
  }
  const save = async (submit) => set(await api(`/key-person/${p.id}/answers`, { method: "PUT", body: { answers: vals, submit } }));
  return (
    <Step n={3} title="The answers">
      <p className="small">Waiting for {name}{p.answered ? ` — ${p.answered} answered so far` : ""}. Or type the answers yourself, if you're sitting with {name}.</p>
      {!typing ? <button className="btn sm" onClick={() => setTyping(true)}>Type the answers here</button> : (
        <div className="stack">
          {p.questions.map((q) => (
            <Field key={q.key} label={q.q} hint={q.hint}>
              <textarea className="textarea" maxLength={3000} value={vals[q.key] || ""} onChange={(e) => setVals({ ...vals, [q.key]: e.target.value })} />
            </Field>
          ))}
          <FormError msg={err} />
          <div className="row">
            <Busy className="btn" onError={(e) => setErr(e.message)} run={() => save(false)}>Save</Busy>
            <Busy className="btn primary" onError={(e) => setErr(e.message)} run={() => save(true)}>These are the answers</Busy>
          </div>
        </div>
      )}
    </Step>
  );
}

function SkillStep({ p, set }) {
  const [err, setErr] = useState("");
  const sk = p.skill;
  const ready = !!p.submitted_at;
  return (
    <Step n={4} title="The agent skill" off={!ready}>
      {!sk ? (
        <>
          <p className="small">From the answers, the hub writes an AI agent skill file, a role card for the agent, and an SOP for each job {first(p.name)} described — into your SOP library.</p>
          <FormError msg={err} />
          <Busy className="btn primary ai" disabled={!ready} onError={(e) => setErr(e.message)} run={async () => set(await api(`/key-person/${p.id}/skill`, { method: "POST" }))}>
            Build the agent skill · {p.costs.skill} token</Busy>
        </>
      ) : (
        <>
          <div className="kp-card">
            <div className="band"><span className="small">AI agent role card</span><b>{sk.card.agent_name || `${p.role} Agent`}</b><span className="small">Role: {p.role}</span></div>
            <div className="body">
              <span className="small">{sk.card.does || sk.description}</span>
              {sk.card.takes_over.length > 0 && <div><span className="label">Can take over</span><ul>{sk.card.takes_over.map((x) => <li key={x} className="small">{x}</li>)}</ul></div>}
              {sk.card.hands_back.length > 0 && <div><span className="label">Hands back to you</span><ul>{sk.card.hands_back.map((x) => <li key={x} className="small">{x}</li>)}</ul></div>}
            </div>
          </div>
          <div className="row">
            <a className="btn" href={`/api/key-person/${p.id}/skill.md`}>Download the skill file (.md)</a>
            <a className="btn ghost" href={`/api/key-person/${p.id}/card.png`} target="_blank" rel="noreferrer">Role card as an image</a>
          </div>
          <details className="ex-more"><summary>Read the skill file</summary><pre className="kp-md">{sk.md}</pre></details>
          {p.sops.length > 0 && (
            <div className="stack" style={{ gap: 6 }}>
              <span className="label">In your SOP library</span>
              {p.sops.map((s) => <Link key={s.id} to="/sops" className="small">{s.title} →</Link>)}
            </div>
          )}
          {p.can_add_to_agent ? (
            <Busy className="btn ai" run={async () => set(await api(`/key-person/${p.id}/add-to-agent`, { method: "POST" }))}>{p.in_brain ? "Update it in your AI staff" : "Add to your AI staff"}</Busy>
          ) : <p className="small muted">Plans with AI staff can add this skill to an agent. For now, keep the file — it's the agent's instructions, ready when you are.</p>}
          <FormError msg={err} />
          <Busy className="btn sm ghost ai" onError={(e) => setErr(e.message)} run={async () => set(await api(`/key-person/${p.id}/skill`, { method: "POST" }))}>Build it again · {p.costs.skill} token</Busy>
        </>
      )}
    </Step>
  );
}

function Person({ id, onBack, onGone }) {
  const { me, refresh } = useHub();
  const [p, setP] = useState(null);
  const [err, setErr] = useState("");
  useEffect(() => { api(`/key-person/${id}`).then(setP).catch((e) => setErr(e.message)); }, [id]);
  const set = (x) => { setP(x); refresh(); };
  if (!p) return err ? <FormError msg={err} /> : <Loading />;
  return (
    <div className="stack">
      <div className="row between">
        <button className="btn sm ghost" onClick={onBack}>← Everyone</button>
        <span className="small muted">Each AI step uses 1 token · {p.tokens_left} left {tokenSpan(me)}</span>
      </div>
      <div>
        <h2 style={{ margin: 0 }}>{p.name}</h2>
        <span className="small muted">{p.role}{p.department ? ` · ${p.department}` : ""}</span>
      </div>
      <OwnerStep p={p} set={set} />
      <QuestionnaireStep p={p} set={set} />
      <AnswersStep p={p} set={set} />
      <SkillStep p={p} set={set} />
      <div className="row">
        <Busy className="btn sm ghost" run={async () => {
          if (!window.confirm(`Remove ${p.name}? The SOPs already in your SOP library stay.`)) return;
          await api(`/key-person/${p.id}`, { method: "DELETE" });
          onGone();
        }}>Remove {first(p.name)}</Busy>
      </div>
    </div>
  );
}

export default function KeyPerson() {
  const [params, setParams] = useSearchParams();
  const open = params.get("id");
  const [list, reload, err] = useLoad("/key-person");
  const [adding, setAdding] = useState(false);
  const go = (id) => setParams(id ? { id } : {});
  const head = { kicker: "Data · Key-person knowledge", id: "DA-01", pillar: "Data" };
  const title = <>Keep what your key people <em>know</em>.</>;
  const sub = "Pick the employee the business depends on most. Their know-how becomes an AI agent skill, a role card and SOPs — so work doesn't stop when they're away.";
  if (open) return <Sheet {...head} title={title} sub={sub}><Person id={open} onBack={() => { go(null); reload(); }} onGone={() => { go(null); reload(); }} /></Sheet>;
  return (
    <Sheet {...head} title={title} sub={sub}>
      {err && <LoadError err={err} />}
      {!list ? <Loading /> : (
        <>
          {(adding || !list.people.length) ? <AddPerson onAdded={(p) => { setAdding(false); go(p.id); }} onCancel={list.people.length ? () => setAdding(false) : null} /> : (
            <button className="btn primary" onClick={() => setAdding(true)} disabled={list.people.length >= list.max}>Add a person</button>
          )}
          {list.people.length > 0 ? (
            <div className="kp-people">
              {list.people.map((x) => (
                <button key={x.id} className="kp-person" onClick={() => go(x.id)}>
                  <span><b>{x.name}</b><span className="small muted"> · {x.role}</span>
                    {x.questions ? <div className="small muted">{x.answered} of {x.questions} answered</div> : null}</span>
                  <Pill kind={STATUS[x.status]?.[1]}>{STATUS[x.status]?.[0] || x.status}</Pill>
                </button>
              ))}
            </div>
          ) : <Empty>How it works: you answer up to 5 questions about the person, they answer a questionnaire on their phone, and the hub writes the skill. 3 AI tokens in all.</Empty>}
          <p className="small muted">Only work knowledge is collected — never passwords, bank details or anything personal. If someone types a password anyway, the hub removes it.</p>
        </>
      )}
    </Sheet>
  );
}
