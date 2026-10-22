import React, { useEffect, useRef, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { api } from "../api.js";
import { Busy, Field, InviteLine, Sheet, useHub, useLoad } from "../ui.jsx";

export default function Brand() {
  const { refresh, toast, nav } = useHub();
  const [params] = useSearchParams();
  const [biz, reload] = useLoad("/business");
  const [pick, setPick] = useState(null);
  const [draft, setDraft] = useState({ message: "", pitch: "" });
  const [generating, setGenerating] = useState(false);
  const auto = useRef(false);

  const generate = async () => {
    setGenerating(true);
    try { await api("/brand/generate", { method: "POST" }); await reload(); await refresh(); setPick(null); }
    catch (e) { if (!e.handled) toast(e.message, "err"); } finally { setGenerating(false); }
  };
  useEffect(() => {
    if (!biz) return;
    if (biz.brand?.message && pick === null) setDraft({ message: biz.brand.message, pitch: biz.brand.pitch || "" });
    if (params.get("auto") && !auto.current && !biz.brand_options?.length) { auto.current = true; generate(); }
  }, [biz]);

  const options = biz?.brand_options || [];
  return (
    <Sheet kicker="Structure · Brand message" id="S-01" pillar="Structure" step={1}
      title={<>One line that says what you <em>do</em>.</>}
      sub="Your brand message becomes your website headline and the first line of your posts. Pick the option that sounds most like you, then make it yours."
      actions={options.length > 0 && <button className="btn ghost sm ai" disabled={generating} onClick={generate}>{generating ? "Writing…" : "Write 3 new options"}</button>}>
      {generating && !options.length && <div className="panel"><span className="label">Writing</span><p>Reading your Business Brain and drafting three options…</p></div>}
      {!generating && !options.length && !biz?.brand && (
        <div className="empty"><h2>Let the AI draft it.</h2><p className="muted">Uses 1 AI run.</p><button className="btn primary ai" onClick={generate}>Write my brand message</button></div>
      )}
      {options.length > 0 && (
        <div className="grid3">
          {options.map((o, i) => (
            <button key={i} className={`panel ${pick === i ? "active" : ""}`} style={{ textAlign: "left", cursor: "pointer", color: "inherit" }}
              onClick={() => { setPick(i); setDraft(o); }}>
              <span className="label">Option {i + 1}{pick === i ? " · selected" : ""}</span>
              <h3>{o.message}</h3>
              <p className="small muted">{o.pitch}</p>
            </button>
          ))}
        </div>
      )}
      {(pick !== null || biz?.brand) && (
        <div className="panel">
          <Field label="Brand message" hint="Up to about 14 words."><input className="input" value={draft.message} maxLength={160} onChange={(e) => setDraft({ ...draft, message: e.target.value })} /></Field>
          <Field label="30-second pitch"><textarea className="textarea" value={draft.pitch} maxLength={700} onChange={(e) => setDraft({ ...draft, pitch: e.target.value })} /></Field>
          <div className="row">
            <Busy className="btn primary" disabled={draft.message.trim().length < 5}
              run={async () => { await api("/brand", { method: "PUT", body: draft }); await refresh(); toast("Brand message saved"); nav("/website"); }}>
              Use this and build my website</Busy>
            <Busy className="btn ghost" run={async () => { await api("/brand", { method: "PUT", body: draft }); await refresh(); toast("Saved"); }}>Save only</Busy>
          </div>
          <InviteLine />
        </div>
      )}
    </Sheet>
  );
}
