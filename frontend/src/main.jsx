import React from "react";
import { createRoot } from "react-dom/client";
import { BrowserRouter } from "react-router-dom";
// OwnerDesk design system: Manrope for everything, JetBrains Mono for prompts and code only.
import "@fontsource/manrope/300.css";
import "@fontsource/manrope/400.css";
import "@fontsource/manrope/500.css";
import "@fontsource/manrope/700.css";
import "@fontsource/manrope/800.css";
import "@fontsource/jetbrains-mono/400.css";
import "./styles.css";
import "./v2.css";
import "./v23.css";
import App from "./App.jsx";

createRoot(document.getElementById("root")).render(<BrowserRouter><App /></BrowserRouter>);
