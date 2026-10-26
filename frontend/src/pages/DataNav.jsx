import React from "react";
import { NavLink, useLocation } from "react-router-dom";
import { canAct, useHub } from "../ui.jsx";
import "./data.css";

/* v2.3 Data engine: Expenses, the bank statement and the analysis read as one feature — "Expenses & bank statement". */
export default function DataNav() {
  const { me } = useHub();
  const loc = useLocation();
  const analysis = loc.pathname === "/expenses" && new URLSearchParams(loc.search).get("tab") === "analysis";
  const links = [["/expenses", "Expenses", loc.pathname === "/expenses" && !analysis]];
  if (canAct(me, "owner") && me.features?.bank_reader) links.push(["/bank-statement", "Bank statement", loc.pathname === "/bank-statement"]);
  if (canAct(me, "manager")) links.push(["/expenses?tab=analysis", "Analysis & AI advice", analysis]);
  if (links.length < 2) return null;
  return (
    <nav className="dn" aria-label="Expenses and bank statement">
      {links.map(([to, label, on]) => <NavLink key={to} to={to} className={on ? "on" : ""} end>{label}</NavLink>)}
    </nav>
  );
}
