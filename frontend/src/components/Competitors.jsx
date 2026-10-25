import React from "react";
import "./competitors.css";

/** Up to 3 competitor websites. One box to start; "Add another" for the 2nd and 3rd. Empty boxes are ignored. */
export const MAX_COMPETITORS = 3;

export default function CompetitorInputs({ value, onChange, read = [] }) {
  const list = value.length ? value : [""];
  const set = (i, x) => onChange(list.map((v, j) => (j === i ? x : v)));
  const status = (url) => read.find((r) => r.url && url && r.host && url.toLowerCase().includes(r.host));
  return (
    <div className="stack comp-inputs" style={{ gap: 8 }}>
      {list.map((v, i) => {
        const st = status(v.trim());
        return (
          <div key={i} className="comp-row">
            <input className="input" inputMode="url" autoComplete="off" maxLength={300} placeholder={i ? "www.another-competitor.in" : "www.competitor.in"}
              aria-label={`Competitor website ${i + 1}`} value={v} onChange={(e) => set(i, e.target.value)} />
            {list.length > 1 && <button type="button" className="btn sm ghost" aria-label={`Remove competitor website ${i + 1}`} onClick={() => onChange(list.filter((_, j) => j !== i))}>Remove</button>}
            {st && <span className={`small ${st.read ? "ok-text" : "muted"}`}>{st.read ? "Read ✓" : `Couldn't read it: ${st.error || "no answer"}`}</span>}
          </div>
        );
      })}
      {list.length < MAX_COMPETITORS && list[list.length - 1].trim() && (
        <div><button type="button" className="btn sm ghost" onClick={() => onChange([...list, ""])}>+ Add another</button></div>
      )}
    </div>
  );
}

export const cleanCompetitors = (list) => list.map((x) => x.trim()).filter(Boolean).slice(0, MAX_COMPETITORS);
