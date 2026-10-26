import React, { useState } from "react";
import { api, day, upload } from "../api.js";
import { Busy, Field, FormError, Sheet, canAct, useHub, useLoad } from "../ui.jsx";
import { Empty, Loading, Tabs } from "../v2.jsx";
import "./growth.css";

const ACCEPT = ".pdf,.docx,.xlsx,.xlsm,.csv,.txt,.md";
const MAX_MB = 8;

/** About how many words a document holds (the server keeps its length in characters). */
const words = (chars) => {
  const w = Math.round((chars || 0) / 6);
  return w < 100 ? "under 100 words" : `about ${new Intl.NumberFormat("en-IN").format(Math.round(w / 10) * 10)} words`;
};

function KindSelect({ kinds, value, onChange }) {
  return (
    <select className="select" value={value} onChange={(e) => onChange(e.target.value)}>
      {Object.entries(kinds).map(([k, label]) => <option key={k} value={k}>{label}</option>)}
    </select>
  );
}

function AddDoc({ kinds, full, onAdded }) {
  const { toast } = useHub();
  const [mode, setMode] = useState("file");
  const [title, setTitle] = useState("");
  const [kind, setKind] = useState(Object.keys(kinds)[0] || "other");
  const [file, setFile] = useState(null);
  const [text, setText] = useState("");
  const [err, setErr] = useState("");
  const [picker, setPicker] = useState(0); // a new key clears the file input

  const ready = mode === "file" ? !!file : title.trim().length >= 2 && text.trim().length >= 20;
  const add = async () => {
    if (mode === "file") {
      if (file.size > MAX_MB * 1024 * 1024) throw new Error(`Keep each file under ${MAX_MB} MB.`);
      const form = new FormData();
      form.append("file", file);
      form.append("title", title.trim());
      form.append("kind", kind);
      await upload("/brain-docs", form);
    } else {
      await api("/brain-docs", { method: "POST", body: { title: title.trim(), kind, text: text.trim() } });
    }
    setTitle(""); setFile(null); setText(""); setPicker((k) => k + 1);
    toast("Added. Your AI staff read it from now on.");
    onAdded();
  };

  if (full) return <p className="small muted">Your Company Brain is full. Delete an old document to add a new one.</p>;
  return (
    <div className="panel active">
      <span className="label">Add a document</span>
      <Tabs tabs={[["file", "Upload a file"], ["text", "Paste text"]]} value={mode} onChange={(m) => { setMode(m); setErr(""); }} />
      <div className="grid2">
        <Field label={mode === "file" ? "Name (optional)" : "Name"} hint={mode === "file" ? "Leave empty to use the file name." : undefined}>
          <input className="input" maxLength={120} value={title} onChange={(e) => setTitle(e.target.value)} placeholder="e.g. Price list 2026" />
        </Field>
        <Field label="What it is"><KindSelect kinds={kinds} value={kind} onChange={setKind} /></Field>
      </div>
      {mode === "file" ? (
        <Field label="File" hint={`PDF, Word (.docx), Excel (.xlsx), CSV or text — up to ${MAX_MB} MB. Scanned pictures have no text to read; paste the key facts instead.`}>
          <input key={picker} className="input" type="file" accept={ACCEPT} onChange={(e) => setFile(e.target.files?.[0] || null)} />
        </Field>
      ) : (
        <Field label="Text" hint="Prices, terms, answers to common questions — anything a customer might ask.">
          <textarea className="textarea" rows={8} maxLength={200000} value={text} onChange={(e) => setText(e.target.value)}
            placeholder={"Modular kitchen, L-shape, 10 ft: ₹1,85,000\nWardrobe, 7 ft sliding: ₹62,000\nWarranty: 10 years on hardware"} />
        </Field>
      )}
      <FormError msg={err} />
      <div><Busy className="btn primary" disabled={!ready} onError={setErr} run={add}>Add to the Company Brain</Busy></div>
    </div>
  );
}

function Ask({ empty }) {
  const { refresh } = useHub();
  const [q, setQ] = useState("");
  const [a, setA] = useState(null);
  const [err, setErr] = useState("");
  return (
    <div className="panel">
      <span className="label">Ask your Company Brain</span>
      <p className="small muted">Check what your AI staff will say. Ask the way a customer would.</p>
      <div className="inline-add">
        <input className="input" maxLength={1000} value={q} onChange={(e) => setQ(e.target.value)} aria-label="Your question"
          placeholder="e.g. What is the price of a 7 ft wardrobe?" disabled={empty} />
        <Busy className="btn primary ai" disabled={empty || q.trim().length < 3} onError={setErr} run={async () => {
          setA(null);
          const r = await api("/brain-docs/ask", { method: "POST", body: { question: q.trim() } });
          setA({ ...r, q: q.trim() });
          refresh();
        }}>Ask</Busy>
      </div>
      <FormError msg={err} />
      {a && (
        <div className="stack" style={{ gap: 8 }}>
          <span className="small muted">“{a.q}”</span>
          <div className="prompt">{a.answer}</div>
          {a.found === false && <p className="small muted">This isn't in your documents yet. Add it above, and your AI staff can answer it too.</p>}
        </div>
      )}
      <span className="small muted">{empty ? "Add a document first." : "Uses one AI token."}</span>
    </div>
  );
}

export default function CompanyBrain() {
  const { me, toast } = useHub();
  const [data, reload] = useLoad("/brain-docs");
  const editor = canAct(me, "manager");
  const docs = data?.docs || [];
  const title = docs.length ? <>Your AI staff answer from <em>your</em> documents.</> : <>Teach your AI staff your <em>business</em>.</>;
  return (
    <Sheet kicker="Structure · Company Brain" id="GR-02" pillar="Structure" title={title}
      sub="The documents your AI staff read before they answer: price lists, FAQs, past quotations, policies, catalogues. Nothing here is shared with anyone else.">
      {!data ? <Loading /> : (
        <>
          {editor && <AddDoc kinds={data.kinds} full={docs.length >= data.max} onAdded={reload} />}
          <Ask empty={!docs.length} />
          <div className="row between">
            <span className="label">Your documents</span>
            <span className="small muted">{docs.length} of {data.max}</span>
          </div>
          {!docs.length ? (
            <Empty>Nothing here yet. Start with your price list — it's what customers ask about most.</Empty>
          ) : (
            <table className="t cards">
              <thead><tr><th>Document</th><th>What it is</th><th>Size</th><th>Added</th>{editor && <th />}</tr></thead>
              <tbody>{docs.map((d) => (
                <tr key={d.id}>
                  <td><b style={{ overflowWrap: "anywhere" }}>{d.title}</b>{d.filename && d.filename !== d.title && <div className="small muted" style={{ overflowWrap: "anywhere" }}>{d.filename}</div>}</td>
                  <td data-l="What it is" className="small">{data.kinds[d.kind] || "Other"}</td>
                  <td data-l="Size" className="small muted">{words(d.chars)}{d.passages ? ` · ${d.passages} ${d.passages === 1 ? "passage" : "passages"}` : ""}</td>
                  <td data-l="Added" className="small muted">{day(d.created_at)}</td>
                  {editor && (
                    <td><Busy className="btn sm ghost" run={async () => {
                      if (!window.confirm(`Delete “${d.title}”? Your AI staff stop using it straight away.`)) return;
                      await api(`/brain-docs/${d.id}`, { method: "DELETE" });
                      toast("Deleted");
                      reload();
                    }}>Delete</Busy></td>
                  )}
                </tr>
              ))}</tbody>
            </table>
          )}
        </>
      )}
    </Sheet>
  );
}
