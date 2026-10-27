import React from "react";
import { Link } from "react-router-dom";
import { api } from "../api.js";
import { Busy, useHub } from "../ui.jsx";

/* What happened to an inquiry's draft quotation (auto_quote.status_view on the server): drafting, ready with its number, or
   no draft and exactly why — with Make quotation where the reason can be fixed by hand (1 AI token). */

const COLOR = { ready: "var(--aqua)", drafting: "var(--ivory-70)", none: "var(--amber)", failed: "var(--amber)" };

export default function QuoteStatus({ leadId, q, onMade, canMake = true, link = true }) {
  const { toast } = useHub();
  if (!q) return null;
  return (
    <div className="row" style={{ gap: 10, alignItems: "center", flexWrap: "wrap" }}>
      <span className="small" style={{ color: COLOR[q.state] || "inherit" }}>{q.text}</span>
      {q.quote_number && link && <Link className="btn sm ghost" to="/quotes">Open {q.quote_number}</Link>}
      {canMake && q.can_make && (
        <Busy className="btn sm ghost ai" run={async () => {
          const r = await api(`/quotes/from-inquiry/${leadId}`, { method: "POST" });
          toast(r.quote_number ? `Draft quotation ${r.quote_number} is ready` : "Done");
          if (onMade) await onMade(r);
        }}>Make quotation</Busy>
      )}
    </div>
  );
}
