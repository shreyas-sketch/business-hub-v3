import React from "react";

/** One folding part of a long editor: a title, a one-line summary of what's in it, and the fields inside (styles: website21.css). */
export default function Section({ title, note, open, children }) {
  return (
    <details className="ws-sec" open={open}>
      <summary><span className="ws-sec-t">{title}</span>{note && <span className="ws-sec-n">{note}</span>}</summary>
      <div className="ws-sec-body stack">{children}</div>
    </details>
  );
}
