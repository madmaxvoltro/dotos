// Router + layout. One screen at a time; an RDP session always wins.

import { startPolling, store, subscribe } from "./lib.js";
import { login, off } from "./login.js";
import { boot, game, idle, session } from "./screens.js";
import { settings } from "./settings.js";
import { setup } from "./setup.js";

const app = document.getElementById("app");
const screens = { boot, setup, login, settings, idle, session, off, game };
let current = null;
let cleanup = null;
let booted = false;

function go(name, opts = {}) {
  cleanup?.();
  cleanup = null;
  app.replaceChildren();
  const root = document.createElement("div");
  root.style.cssText = "position:absolute;inset:0";
  app.appendChild(root);
  current = name;
  cleanup = screens[name](root, { go, ...opts, done: (next) => { booted = true; go(next); }, wake: () => go("login") }) || null;
  resetIdle();
}

// the design is laid out for 1280×720 and up: shrink on smaller screens, never blow it up
// (a 1080p screen just has more room; 4K is handled by the monitor's scale). Settings > This Dot > Interface size.
function layout() {
  const user = Number(store.state?.display?.scale) || 1;
  const fit = Math.min(1, innerWidth / 1280, innerHeight / 720);
  const s = fit * user;
  app.style.width = `${innerWidth / s}px`;
  app.style.height = `${innerHeight / s}px`;
  app.style.transform = `scale(${s})`;
}
addEventListener("resize", layout);

// idle clock on the login screen
let idleTimer;
function resetIdle() {
  clearTimeout(idleTimer);
  const minutes = store.state?.device?.idleMinutes;
  if (current !== "login" || !minutes) return;
  idleTimer = setTimeout(() => {
    if (current === "login" && !document.querySelector(".scrim") && document.activeElement?.value === "") go("idle");
    else resetIdle();
  }, minutes * 60 * 1000);
}
["keydown", "mousedown", "mousemove", "touchstart"].forEach((e) => addEventListener(e, resetIdle, { passive: true }));

let lastScale;
subscribe((st) => {
  if (!st) return;
  document.documentElement.style.setProperty("--accent", st.device.accent || "#e8305e");
  if (st.display.scale !== lastScale) { lastScale = st.display.scale; layout(); }
  if (!booted) return;
  // game mode owns the main monitor while it runs (RDP may still be on the second one)
  const gaming = st.games && st.games.state !== "idle";
  if (gaming) { if (current !== "game" && current !== "setup") go("game"); return; }
  if (current === "game") { go(st.session ? "session" : "login"); return; }
  const inSession = Boolean(st.session);
  if (inSession && current !== "session" && current !== "setup") go("session");
  else if (!inSession && current === "session") go("login");
  else if (!st.setupComplete && current !== "setup" && current !== "off") go("setup");
});

layout();
startPolling();
go("boot");

// handy when developing: ?screen=idle etc.
const forced = new URLSearchParams(location.search).get("screen");
if (forced && screens[forced]) setTimeout(() => { booted = true; go(forced); }, 400);
