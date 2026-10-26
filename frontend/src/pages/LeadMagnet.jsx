import React, { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { ago, api, upload, waLink } from "../api.js";
import { Busy, Copy, Field, FormError, Sheet, Stat, Switch, useHub, useLoad } from "../ui.jsx";
import { Loading, Pill } from "../v2.jsx";
import { CompetitorNote, FourQuestions, answersOf } from "../components/SharedInputs.jsx";
import "./program.css";
import "./website21.css";
import "./lead23.css";

const mb = (bytes) => `${(bytes / 1024 / 1024).toFixed(bytes < 1024 * 1024 ? 2 : 1)} MB`;

/** One uploaded download: its link, numbers, the published switch, words and delete. */
function UploadItem({ m, onChange }) {
  const { toast } = useHub();
  const [edit, setEdit] = useState(false);
  const [title, setTitle] = useState(m.title);
  const [desc, setDesc] = useState(m.description || "");
  const [err, setErr] = useState("");
  const patch = async (body, msg) => { await api(`/lead-magnets/${m.id}`, { method: "PATCH", body }); toast(msg); onChange(); };
  return (
    <div className="panel flat lm-item">
      <img src={m.cover_url} alt="" loading="lazy" />
      <div className="lm-body">
        <div className="row between"><b>{m.title}</b>{m.published ? <Pill kind="on">On your website</Pill> : <Pill>Hidden</Pill>}</div>
        {m.description && <span className="small muted">{m.description}</span>}
        <div className="lm-meta small muted">
          <span>{m.leads} form {m.leads === 1 ? "fill" : "fills"} (left their number)</span>
          <span>{m.downloads} {m.downloads === 1 ? "download" : "downloads"} of the PDF</span>
          <span>{mb(m.size || 0)} PDF</span>
          <span>Added {ago(m.created_at)}</span>
        </div>
        {m.url && m.published && (
          <>
            <a className="pg-link" href={m.url} target="_blank" rel="noopener">{m.url}</a>
            <div className="pg-actions">
              <Copy text={m.url} label="Copy link" />
              <a className="btn sm" href={waLink(`Here's a free download for you: ${m.title}. Get it here: ${m.url}`)} target="_blank" rel="noopener">Share on WhatsApp</a>
              <a className="btn sm ghost" href={m.file_url} target="_blank" rel="noopener">See the PDF</a>
            </div>
          </>
        )}
        {edit ? (
          <div className="lm-form">
            <Field label="Title"><input className="input" maxLength={120} value={title} onChange={(e) => setTitle(e.target.value)} /></Field>
            <Field label="One line about it"><input className="input" maxLength={500} value={desc} onChange={(e) => setDesc(e.target.value)} /></Field>
            <FormError msg={err} />
            <div className="row">
              <Busy className="btn sm primary" disabled={title.trim().length < 3} onError={setErr}
                run={async () => { await patch({ title: title.trim(), description: desc.trim() }, "Saved"); setEdit(false); }}>Save</Busy>
              <button className="btn sm ghost" onClick={() => { setEdit(false); setTitle(m.title); setDesc(m.description || ""); }}>Cancel</button>
            </div>
          </div>
        ) : (
          <div className="row">
            <Switch checked={m.published} label="On my website" onChange={(on) => patch({ published: on }, on ? "It's on your website" : "Hidden from your website").catch((e) => toast(e.message, "err"))} />
            <button className="btn sm ghost" onClick={() => setEdit(true)}>Edit words</button>
            <Busy className="btn sm ghost" run={async () => {
              if (!window.confirm(`Delete “${m.title}”? Its link stops working.`)) return;
              await api(`/lead-magnets/${m.id}`, { method: "DELETE" }); toast("Deleted"); onChange();
            }}>Delete</Busy>
          </div>
        )}
      </div>
    </div>
  );
}

/** Upload your own PDF (up to 5): it gets its own page with a form, and the download unlocks only after the form. */
function Uploads() {
  const { toast } = useHub();
  const [d, reload] = useLoad("/lead-magnets");
  const [file, setFile] = useState(null);
  const [cover, setCover] = useState(null);
  const [title, setTitle] = useState("");
  const [desc, setDesc] = useState("");
  const [err, setErr] = useState("");
  const [key, setKey] = useState(0);   // resets the file inputs after an upload
  if (!d) return <div className="panel"><Loading /></div>;
  const full = d.items.length >= d.max;
  return (
    <div className="panel">
      <div className="row between"><span className="label">Or upload your own PDF</span><span className="small muted">{d.items.length} of {d.max} downloads</span></div>
      <p className="small muted">Have a brochure, price list or checklist as a PDF? Upload it. It gets its own page on your website: visitors leave their name and number (email optional), then download it — and each one lands in your leads.</p>
      {!d.site_live && <p className="small muted">Your website has to be live for the pages to open. <Link to="/website">Go to my website</Link></p>}
      {d.items.filter((m) => m.source !== "ai").length > 0 && <div className="lm-list">{d.items.filter((m) => m.source !== "ai").map((m) => <UploadItem key={m.id} m={m} onChange={reload} />)}</div>}
      {full ? <span className="hint">That's {d.max}. Delete one to add another.</span> : (
        <div className="lm-form" key={key}>
          <Field label="Title" hint="What people get, in a few words."><input className="input" maxLength={120} value={title} onChange={(e) => setTitle(e.target.value)} placeholder="e.g. Kitchen planning checklist" /></Field>
          <Field label="One line about it (optional)"><input className="input" maxLength={500} value={desc} onChange={(e) => setDesc(e.target.value)} /></Field>
          <Field label={`The PDF (up to ${d.max_mb} MB)`}><input className="lm-file" type="file" accept="application/pdf,.pdf" onChange={(e) => setFile(e.target.files?.[0] || null)} /></Field>
          <Field label="A cover picture (optional)" hint="PNG or JPG. Leave it empty and we draw one in your brand colours.">
            <input className="lm-file" type="file" accept="image/png,image/jpeg" onChange={(e) => setCover(e.target.files?.[0] || null)} />
          </Field>
          <FormError msg={err} />
          <div className="row">
            <Busy className="btn primary sm" disabled={!file || title.trim().length < 3} onError={setErr} run={async () => {
              const form = new FormData();
              form.append("file", file);
              form.append("title", title.trim());
              form.append("description", desc.trim());
              if (cover) form.append("cover", cover);
              await upload("/lead-magnets", form);
              toast("Uploaded — it's on your website");
              setFile(null); setCover(null); setTitle(""); setDesc(""); setKey((k) => k + 1);
              reload();
            }}>Upload</Busy>
          </div>
        </div>
      )}
    </div>
  );
}

const FORMATS = [["checklist", "Checklist"], ["guide", "Step-by-step guide"], ["price guide", "Price guide"], ["mistakes list", "Mistakes to avoid"]];
const MAX_SECTIONS = 10;

/** The two questions the AI needs before it writes the guide. */
function WriteForm({ first, onDone, onCancel }) {
  const { refresh } = useHub();
  const [problem, setProblem] = useState("");
  const [format, setFormat] = useState("checklist");
  const [err, setErr] = useState("");
  return (
    <div className="panel active">
      <span className="label">{first ? "Write your guide" : "Write a new guide"}</span>
      <Field label="What problem does it solve for your customer?" hint="One line, in your customer's words. For example: choosing a modular kitchen without overpaying.">
        <textarea className="textarea" style={{ minHeight: 72 }} maxLength={300} value={problem} onChange={(e) => setProblem(e.target.value)} />
      </Field>
      <div className="stack" style={{ gap: 8 }}>
        <span className="label">Format</span>
        <div className="row" role="radiogroup" aria-label="Format">
          {FORMATS.map(([k, l]) => (
            <button key={k} type="button" role="radio" aria-checked={format === k} className={`btn sm ${format === k ? "primary" : "ghost"}`} onClick={() => setFormat(k)}>{l}</button>
          ))}
        </div>
      </div>
      <FormError msg={err} />
      <div className="row">
        <Busy className="btn primary ai" onError={setErr} disabled={problem.trim().length < 5} run={async () => {
          if (!first && !window.confirm("Write a new guide? It replaces the one you have now.")) return;
          const d = await api("/lead-magnet/generate", { method: "POST", body: { problem: problem.trim(), format } });
          refresh();
          onDone(d);
        }}>Write my guide</Busy>
        {onCancel && <button className="btn ghost" onClick={onCancel}>Cancel</button>}
      </div>
      <span className="small muted">Uses 1 AI token. Written from your Business Brain — you can change every word after.</span>
    </div>
  );
}

/** Every word of the guide, editable. Points are one per line. */
function GuideEditor({ content, onSaved }) {
  const { toast } = useHub();
  const [c, setC] = useState(content);
  const [err, setErr] = useState("");
  const saved = JSON.stringify(content);
  useEffect(() => setC(content), [saved]); // a fresh copy from the server only when the saved words changed
  const set = (k) => (e) => setC({ ...c, [k]: e.target.value });
  const setSec = (i, patch) => setC({ ...c, sections: c.sections.map((s, j) => (j === i ? { ...s, ...patch } : s)) });
  const clean = () => ({ ...c, sections: c.sections.map((s) => ({ ...s, points: (s.points || []).map((p) => p.trim()).filter(Boolean) })) });
  const dirty = JSON.stringify(clean()) !== saved;
  const ready = c.title.trim().length > 0 && c.sections.filter((s) => s.heading.trim()).length >= 2;
  return (
    <div className="panel">
      <div className="row between"><span className="label">Your guide</span>{dirty && <span className="small muted">Changes not saved yet</span>}</div>
      <Field label="Title"><input className="input" maxLength={140} value={c.title} onChange={set("title")} /></Field>
      <Field label="Line under the title"><input className="input" maxLength={200} value={c.subtitle} onChange={set("subtitle")} /></Field>
      <Field label="The promise" hint="What the reader gets from it, in one line."><input className="input" maxLength={200} value={c.promise} onChange={set("promise")} /></Field>
      <Field label="Opening"><textarea className="textarea" maxLength={800} value={c.intro} onChange={set("intro")} /></Field>
      <div className="stack">
        <span className="label">Sections</span>
        {c.sections.map((s, i) => (
          <div key={i} className="pg-sec">
            <div className="pg-sec-head">
              <span className="small muted">Section {i + 1}</span>
              {c.sections.length > 2 && <button className="btn sm ghost" onClick={() => setC({ ...c, sections: c.sections.filter((_, j) => j !== i) })}>Remove</button>}
            </div>
            <Field label="Heading"><input className="input" maxLength={140} value={s.heading} onChange={(e) => setSec(i, { heading: e.target.value })} /></Field>
            <Field label="Text"><textarea className="textarea" style={{ minHeight: 72 }} maxLength={800} value={s.text} onChange={(e) => setSec(i, { text: e.target.value })} /></Field>
            <Field label="Points" hint="One per line, up to 8.">
              <textarea className="textarea" style={{ minHeight: 0 }} rows={Math.max(3, (s.points || []).length + 1)} value={(s.points || []).join("\n")}
                onChange={(e) => setSec(i, { points: e.target.value.split("\n").slice(0, 8) })} />
            </Field>
          </div>
        ))}
        {c.sections.length < MAX_SECTIONS && (
          <button className="btn sm ghost" style={{ justifySelf: "start" }} onClick={() => setC({ ...c, sections: [...c.sections, { heading: "", text: "", points: [] }] })}>Add a section</button>
        )}
      </div>
      <Field label="Closing call to action" hint="What you want them to do next — message you, book a visit."><textarea className="textarea" style={{ minHeight: 64 }} maxLength={400} value={c.cta} onChange={set("cta")} /></Field>
      <FormError msg={err} />
      <div className="row">
        <Busy className="btn primary" disabled={!ready || !dirty} onError={setErr} run={async () => {
          const d = await api("/lead-magnet", { method: "PUT", body: { content: clean() } });
          toast("Guide saved");
          onSaved(d);
        }}>Save guide</Busy>
        {dirty && <button className="btn ghost" onClick={() => setC(content)}>Undo changes</button>}
      </div>
      {!ready && <span className="hint">The guide needs a title and at least two sections with headings.</span>}
    </div>
  );
}


/* ───────────── v2.3: answers → 8 ideas → build → on your website ───────────── */
const STEPS = ["Your answers", "Pick an idea", "Built for you", "On your website"];
const FORMAT_LABEL = { checklist: "Checklist", guide: "Step-by-step guide", calculator: "Calculator sheet", price_guide: "Price guide",
  mistakes: "Mistakes to avoid", questions: "Questions to ask" };

/** Every word of a lead magnet the hub built, editable; saving draws the PDF again (no AI token). */
function BuiltEditor({ m, onSaved, onCancel }) {
  const { toast } = useHub();
  const [c, setC] = useState(m.content);
  const [l, setL] = useState(m.landing || {});
  const [err, setErr] = useState("");
  const set = (k) => (e) => setC({ ...c, [k]: e.target.value });
  const setSec = (i, patch) => setC({ ...c, sections: c.sections.map((x, j) => (j === i ? { ...x, ...patch } : x)) });
  const ready = (c.title || "").trim() && c.sections.filter((x) => (x.heading || "").trim()).length >= 2;
  return (
    <div className="stack">
      <Field label="Title"><input className="input" maxLength={140} value={c.title} onChange={set("title")} /></Field>
      <Field label="Line under the title"><input className="input" maxLength={200} value={c.subtitle || ""} onChange={set("subtitle")} /></Field>
      <Field label="Opening"><textarea className="textarea" maxLength={1200} value={c.intro || ""} onChange={set("intro")} /></Field>
      {c.sections.map((x, i) => (
        <div key={i} className="pg-sec">
          <span className="small muted">Section {i + 1}</span>
          <Field label="Heading"><input className="input" maxLength={140} value={x.heading} onChange={(e) => setSec(i, { heading: e.target.value })} /></Field>
          <Field label="Text"><textarea className="textarea" maxLength={2400} value={x.text || ""} onChange={(e) => setSec(i, { text: e.target.value })} /></Field>
          <Field label="Points" hint="One per line.">
            <textarea className="textarea" rows={Math.max(3, (x.points || []).length + 1)} value={(x.points || []).join("\n")} onChange={(e) => setSec(i, { points: e.target.value.split("\n").slice(0, 10) })} />
          </Field>
        </div>
      ))}
      <Field label="Closing call to action"><textarea className="textarea" style={{ minHeight: 64 }} maxLength={500} value={c.cta || ""} onChange={set("cta")} /></Field>
      <span className="label">Its download page</span>
      <Field label="Headline"><input className="input" maxLength={140} value={l.headline || ""} onChange={(e) => setL({ ...l, headline: e.target.value })} /></Field>
      <Field label="Who it's for"><input className="input" maxLength={200} value={l.who_for || ""} onChange={(e) => setL({ ...l, who_for: e.target.value })} /></Field>
      <Field label="3 benefits" hint="One per line.">
        <textarea className="textarea" rows={3} value={(l.benefits || []).join("\n")} onChange={(e) => setL({ ...l, benefits: e.target.value.split("\n").slice(0, 3) })} />
      </Field>
      <FormError msg={err} />
      <div className="row">
        <Busy className="btn primary sm" disabled={!ready} onError={setErr} run={async () => {
          const clean = { ...c, sections: c.sections.map((x) => ({ ...x, points: (x.points || []).map((p) => p.trim()).filter(Boolean) })) };
          const d = await api(`/lead-magnet/built/${m.id}`, { method: "PUT", body: { content: clean, landing: { ...l, benefits: (l.benefits || []).map((b) => b.trim()).filter(Boolean) } } });
          toast("Saved — the PDF is drawn again"); onSaved(d);
        }}>Save and redraw the PDF</Busy>
        <button className="btn sm ghost" onClick={onCancel}>Cancel</button>
      </div>
      <span className="small muted">Saving uses no AI tokens.</span>
    </div>
  );
}

function BuiltItem({ m, siteLive, onChange }) {
  const { toast } = useHub();
  const [edit, setEdit] = useState(false);
  const [err, setErr] = useState("");
  const live = !!m.published;
  return (
    <div className={`panel ${live ? "active" : ""}`}>
      <div className="ms-built">
        <img src={m.cover_url} alt="" loading="lazy" />
        <div className="stack" style={{ gap: 8, minWidth: 0 }}>
          <div className="row between"><b style={{ overflowWrap: "anywhere" }}>{m.title}</b>{live ? <Pill kind="on">On your website</Pill> : <Pill>Not on your website yet</Pill>}</div>
          <span className="small muted">{FORMAT_LABEL[m.format] || "Guide"} · {m.pages} pages PDF · {mb(m.size || 0)} · in your brand colours</span>
          {m.landing?.who_for && <span className="small">{m.landing.who_for}</span>}
          <div className="lm-meta small muted"><span>{m.leads} {m.leads === 1 ? "person" : "people"} left their number</span><span>{m.downloads} PDF {m.downloads === 1 ? "download" : "downloads"}</span></div>
          <div className="row">
            <a className="btn sm ghost" href={m.file_url} target="_blank" rel="noopener">See the PDF</a>
            <Switch checked={live} label="On my website" disabled={!siteLive && !live} onChange={async (on) => {
              setErr("");
              try { onChange(await api(`/lead-magnet/built/${m.id}/live`, { method: "POST", body: { published: on } })); toast(on ? "It's on your website" : "Taken off your website"); }
              catch (e) { if (!e.handled) setErr(e.message); }
            }} />
            <button className="btn sm ghost" onClick={() => setEdit(!edit)}>{edit ? "Close" : "Edit words"}</button>
            <Busy className="btn sm ghost" run={async () => {
              if (!window.confirm(`Delete “${m.title}”? Its page stops working.`)) return;
              await api(`/lead-magnets/${m.id}`, { method: "DELETE" }); toast("Deleted"); onChange(await api("/lead-magnet/studio"));
            }}>Delete</Busy>
          </div>
          {!siteLive && !live && <span className="small muted">Your website has to be live first. <Link to="/website">Go to my website</Link></span>}
          <FormError msg={err} />
          {live && m.url && (
            <>
              <a className="pg-link" href={m.url} target="_blank" rel="noopener">{m.url}</a>
              <div className="pg-actions">
                <Copy text={m.url} label="Copy link" />
                <a className="btn sm" href={waLink(`Here's a free download for you: ${m.title}. Get it here: ${m.url}`)} target="_blank" rel="noopener">Share on WhatsApp</a>
              </div>
              <span className="small muted">Its button shows on your website. Every download lands in your leads.</span>
            </>
          )}
        </div>
      </div>
      {edit && <BuiltEditor m={m} onCancel={() => setEdit(false)} onSaved={(d) => { setEdit(false); onChange(d); }} />}
    </div>
  );
}

function Studio({ st, setSt }) {
  const { refresh, toast } = useHub();
  const [ans, setAns] = useState(answersOf(st.inputs));
  const [rival, setRival] = useState(st.inputs.competitor_url || "");
  const [err, setErr] = useState("");
  const [building, setBuilding] = useState(null);
  const ideas = st.ideas?.items || [];
  const step = st.built.some((m) => m.published) ? 3 : st.built.length ? 2 : ideas.length ? 1 : 0;
  const full = st.count >= st.max;
  const firstRead = rival.trim() && !(st.competitor && st.competitor.matches && st.competitor.status === "ok");
  const ready = (ans.about || "").trim().length > 2 && ((ans.offers || "").trim() || (ans.audience || "").trim());
  const getIdeas = async () => {
    await api("/inputs", { method: "PUT", body: { competitors: rival.trim() ? [rival.trim()] : [] } });
    setSt(await api("/lead-magnet/ideas", { method: "POST", body: ans }));
    refresh();
    document.getElementById("ms-ideas")?.scrollIntoView({ behavior: "smooth", block: "start" });
  };
  const build = async (i) => {
    setBuilding(i); setErr("");
    try {
      const d = await api("/lead-magnet/build", { method: "POST", body: { index: i } });
      setSt(d); refresh(); toast("Your lead magnet is ready — check the PDF");
      document.getElementById("ms-built")?.scrollIntoView({ behavior: "smooth", block: "start" });
    } catch (e) { if (!e.handled) setErr(e.message); } finally { setBuilding(null); }
  };
  return (
    <>
      <div className="ms-steps" aria-label="Steps">{STEPS.map((x, i) => <span key={x} className={`ms-step ${i === step ? "on" : i < step ? "done" : ""}`}>{i + 1}. {x}</span>)}</div>
      <div className="panel">
        <span className="label">1 · Your answers</span>
        <p className="small muted">We filled in what your Knowledge Hub already says. Check it, add your customers' worries, then get ideas.</p>
        <FourQuestions data={st} value={ans} onChange={setAns} />
        <Field label="A competitor's website (optional)" hint="So your ideas fill the gaps they leave. We never copy their words.">
          <input className="input" inputMode="url" placeholder="www.theirbusiness.in" value={rival} onChange={(e) => setRival(e.target.value)} />
        </Field>
        {st.competitor && st.competitor.matches && <CompetitorNote comp={st.competitor} />}
        <div className="row">
          <Busy className="btn primary ai" disabled={!ready} onError={setErr} run={getIdeas}>{ideas.length ? "Show me 8 new ideas" : "Show me 8 ideas"}</Busy>
          <span className="small muted">1 AI token{firstRead ? " (plus 1 to read your competitor's website the first time)" : ""}.</span>
        </div>
        <FormError msg={!ideas.length ? err : ""} />
      </div>
      {ideas.length > 0 && (
        <div className="panel" id="ms-ideas">
          <span className="label">2 · Pick one idea</span>
          <p className="small muted">We build the one you pick in full: a PDF in your brand colours and its own download page. 1 AI token.</p>
          {full && <span className="hint">You have {st.max} lead magnets. Delete one to build another.</span>}
          <div className="ms-ideas">
            {ideas.map((x, i) => (
              <div key={i} className="ms-idea">
                <Pill>{x.format_label}</Pill>
                <b>{x.title}</b>
                <span className="small">{x.who}</span>
                <span className="small muted">Solves: {x.problem}</span>
                {x.why && <span className="small muted">{x.why}</span>}
                <div className="row">
                  <button className="btn sm primary ai" disabled={full || building !== null} onClick={() => build(i)}>{building === i ? "Building… about a minute" : "Build this one"}</button>
                </div>
              </div>
            ))}
          </div>
          <FormError msg={err} />
        </div>
      )}
      {st.built.length > 0 && (
        <div className="stack" id="ms-built">
          <span className="label">3 · Built for you · 4 · On your website</span>
          {st.built.map((m) => <BuiltItem key={m.id} m={m} siteLive={st.site_live} onChange={setSt} />)}
        </div>
      )}
    </>
  );
}

export default function LeadMagnet() {
  const { toast, refresh } = useHub();
  const [loaded] = useLoad("/lead-magnet");
  const [stLoaded, , stErr] = useLoad("/lead-magnet/studio");
  const [data, setData] = useState(null);
  const [st, setSt] = useState(null);
  const [rewrite, setRewrite] = useState(false);
  const [pubErr, setPubErr] = useState("");
  const [publishing, setPublishing] = useState(false);
  useEffect(() => { if (loaded) setData(loaded); }, [loaded]);
  useEffect(() => { if (stLoaded) setSt(stLoaded); }, [stLoaded]);

  const head = { kicker: "Scale · Lead magnet", id: "PR-01", pillar: "Scale" };
  const sub = "A free download on your website that turns visitors into inquiries. Visitors leave their name and number to get it, and each one lands in your leads.";
  if (!data || !st) return <Sheet {...head} title={<>A free download that brings <em>inquiries</em>.</>} sub={sub}>{stErr ? <FormError msg={stErr.message} /> : <Loading />}</Sheet>;

  const g = data.guide;
  const title = st.built.some((m) => m.published) ? <>Your lead magnet is <em>live</em>.</> : st.built.length ? <>Check it, then put it on your <em>website</em>.</>
    : <>A free download that brings <em>inquiries</em>.</>;
  if (!g) {
    return (
      <Sheet {...head} title={title} sub={sub}>
        <Studio st={st} setSt={setSt} />
        <Uploads />
      </Sheet>
    );
  }

  const live = !!g.published;
  const content = g.content;
  const togglePublish = async (published) => {
    setPublishing(true);
    setPubErr("");
    try {
      const d = await api("/lead-magnet/publish", { method: "POST", body: { published } });
      setData(d);
      refresh();
      toast(published ? "Your guide is live on your website" : "Guide taken off your website");
    } catch (e) { if (!e.handled) setPubErr(e.message); }
    finally { setPublishing(false); }
  };
  const shareText = `Here's a free guide for you: ${content.title}. Read it here: ${data.url}`;
  const rate = data.stats.views ? Math.round((data.stats.leads / data.stats.views) * 1000) / 10 : 0;

  return (
    <Sheet {...head} sub={sub} title={title}>
      <Studio st={st} setSt={setSt} />
      <span className="label">Your earlier guide (a web page)</span>
      <div className={`panel ${live ? "active" : ""}`}>
        <div className="row between">
          <span className="label">On your website</span>
          {live ? <Pill kind="on">Live{g.published_at ? ` · ${ago(g.published_at)}` : ""}</Pill> : <Pill>Not published</Pill>}
        </div>
        <Switch checked={live} disabled={publishing} label={live ? "Published on my website" : "Publish on my website"} onChange={togglePublish} />
        <FormError msg={pubErr} />
        {!data.site_live && !live && (
          <p className="small muted">The guide lives on your website, so the website has to be live first. <Link to="/website">Go to my website</Link></p>
        )}
        {data.url && live && (
          <>
            <a className="pg-link" href={data.url} target="_blank" rel="noopener">{data.url}</a>
            <div className="pg-actions">
              <Copy text={data.url} label="Copy link" />
              <a className="btn sm" href={data.url} target="_blank" rel="noopener">Open</a>
              <a className="btn sm" href={waLink(shareText)} target="_blank" rel="noopener">Share on WhatsApp</a>
            </div>
            <p className="small muted">Put this link in your WhatsApp Status, your groups and your Google profile. A link to it also shows on your website.</p>
          </>
        )}
        {data.url && !live && data.site_live && <span className="small muted">Once published, it will be at <span className="pg-link">{data.url}</span></span>}
      </div>

      <div className="statgrid">
        <Stat label="People who opened it" value={data.stats.views} />
        <Stat label="Left their number" value={data.stats.leads} note={data.stats.views ? `${rate}% of readers` : "Counted from the day you publish"} />
      </div>

      <GuideEditor content={content} onSaved={setData} />

      {rewrite
        ? <WriteForm onDone={(d) => { setData(d); setRewrite(false); }} onCancel={() => setRewrite(false)} />
        : <div><button className="btn ghost sm" onClick={() => setRewrite(true)}>Write a new guide with AI</button></div>}

      <Uploads />
    </Sheet>
  );
}
