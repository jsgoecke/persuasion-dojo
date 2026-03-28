import React from "react";
import ReactDOM from "react-dom/client";
import { initSentry } from "./sentry";
import App from "./App";

// Initialise Sentry before the React tree mounts so uncaught render errors
// and promise rejections are captured from the very first frame.
initSentry();

ReactDOM.createRoot(document.getElementById("root") as HTMLElement).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>,
);
