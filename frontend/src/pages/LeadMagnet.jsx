import React, { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { ago, api, upload, waLink } from "../api.js";
import { Busy, Copy, Field, FormError, Sheet, Switch, useHub, useLoad } from "../ui.jsx";
import { Loading, Pill } from "../v2.jsx";
import "./program.css";
import FullPreview from "../components/FullPreview.jsx";
import Section from "../components/Section.jsx";
import "./website21.css";

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
      <div className="row between"><span className="label">Your own downloads</span><span className="small muted">{d.items.length} of {d.max}</span></div>
      <p className="small muted">Have a brochure, price list or checklist as a PDF? Upload it. It gets its own page on your website: visitors leave their name and number (email optional), then download it — and each one lands in your leads.</p>
      {!d.site_live && <p className="small muted">Your website has to be live for the pages to open. <Link to="/website">Go to my website</Link></p>}
      {d.items.length > 0 && <div className="lm-list">{d.items.map((m) => <UploadItem key={m.id} m={m} onChange={reload} />)}</div>}
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
      <span className="small muted">Uses 1 AI run. Written from your Business Brain — you can change every word after.</span>
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

export default function LeadMagnet() {
  const { toast, refresh } = useHub();
  const [loaded] = useLoad("/lead-magnet");
  const [data, setData] = useState(null);
  const [pubErr, setPubErr] = useState("");
  const [publishing, setPublishing] = useState(false);
  useEffect(() => { if (loaded) setData(loaded); }, [loaded]);

  const head = { kicker: "Scale · Lead magnet", id: "PR-01", pillar: "Scale" };
  const sub = "A free guide on your website. Visitors leave their name and number to read it, and each one lands in your leads.";
  if (!data) return <Sheet {...head} title={<>A free guide that brings <em>inquiries</em>.</>} sub={sub}><Loading /></Sheet>;

  const g = data.guide;
  if (!g) {
    return (
      <Sheet {...head} title={<>A free guide that brings <em>inquiries</em>.</>} sub={sub}>
        <WriteForm first onDone={setData} />
        <Section title="Or upload your own PDF" note="A brochure, price list or checklist you already have">
          <Uploads />
        </Section>
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
    <Sheet {...head} sub={live ? "Share the link. Everyone who leaves their number to read it lands in your leads." : "Read it full screen, change any words, then publish it on your website."}
      title={!live ? <>Check your guide, then <em>publish</em> it.</> : data.stats.leads ? <>Your guide is bringing in <em>leads</em>.</> : <>Your guide is live. Now <em>share</em> it.</>}>
      <div className={`panel ws-top ${live ? "active" : ""}`}>
        <div className="row between">
          <b className="lm-title">{content.title}</b>
          {live ? <Pill kind="on">Live{g.published_at ? ` · ${ago(g.published_at)}` : ""}</Pill> : <Pill>Not published</Pill>}
        </div>
        <div className="row">
          {data.preview_url && <FullPreview src={data.preview_url} title={`${content.title} · preview`} className={live ? "btn" : "btn primary"} />}
          {live && data.url && <>
            <Copy text={data.url} label="Copy link" />
            <a className="btn sm" href={waLink(shareText)} target="_blank" rel="noopener">Share on WhatsApp</a>
          </>}
        </div>
        {live && data.url && <a className="mono ws-url" href={data.url} target="_blank" rel="noopener">{data.url.replace(/^https?:\/\//, "")}</a>}
        <Switch checked={live} disabled={publishing || (!live && !data.site_live)} label={live ? "Published on my website" : "Publish on my website"} onChange={togglePublish} />
        <FormError msg={pubErr} />
        {!data.site_live && !live && (
          <p className="small muted">The guide lives on your website, so your website has to be live first. <Link to="/website">Go to my website</Link></p>
        )}
        {live && <p className="small muted">{data.stats.views ? `${data.stats.views} opened it · ${data.stats.leads} left their number (${rate}%)` : "Put the link in your WhatsApp Status, groups and Google profile. Opens and leads are counted here."}</p>}
      </div>

      <div className="stack ws-edit">
        <Section title="Edit the words" note={`${(content.sections || []).length} sections · ${content.promise || content.subtitle || ""}`}>
          <GuideEditor content={content} onSaved={setData} />
        </Section>
        <Section title="Write a new guide with AI" note="Replaces this one · 1 AI run">
          <WriteForm onDone={setData} />
        </Section>
        <Section title="Your own downloads" note="Upload a PDF you already have">
          <Uploads />
        </Section>
      </div>
    </Sheet>
  );
}
