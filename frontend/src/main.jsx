import React from "react";
import { createRoot } from "react-dom/client";
import { BrowserRouter } from "react-router-dom";
import "@fontsource-variable/anek-latin/standard.css";
import "@fontsource/inter/400.css";
import "@fontsource/inter/500.css";
import "@fontsource/space-grotesk/300.css";
import "@fontsource/space-grotesk/400.css";
import "@fontsource/space-grotesk/500.css";
import "./styles.css";
import "./v2.css";
import "./app-theme.css";
import App from "./App.jsx";

document.body.classList.add("app");  // the workspace look (app-theme.css), on every screen
createRoot(document.getElementById("root")).render(<BrowserRouter><App /></BrowserRouter>);
