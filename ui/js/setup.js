// First-run setup on the Pi's own screen: region, network, remote access, servers, people, preferences.

import { $, api, bind, html, icon, mount, refresh, store, watch } from "./lib.js";
import { pinPanel, preferencesPanel, profileForm, profileList, regionPanel, serverForm, serverList, tailscalePanel, wifiPanel } from "./parts.js";

const STEP_KEY = "dot.setupStep";
const STEPS = ["welcome", "region", "network", "remote", "servers", "people", "prefs", "done"];

export function setup(root, { go }) {
  let step = Math.min(Number(safeGet(STEP_KEY)) || 0, STEPS.length - 1);
  let cleanup = null;
  let mode = "list"; // servers / people: "list" | "form"
  let editing = null;
  const startKeyboard = store.state.device.keyboard;

  function goto(n) {
    step = Math.max(0, Math.min(STEPS.length - 1, n));
    mode = "list"; editing = null;
    safeSet(STEP_KEY, step);
    render();
  }

  function render() {
    cleanup?.(); cleanup = null;
    const name = STEPS[step];
    if (name === "welcome") return renderWelcome();
    mount(root, html`<div class="screen wizard">
      <div class="steps" aria-label="Step ${step} of ${STEPS.length - 1}">${STEPS.slice(1).map((_, i) => html`<i class="${i + 1 < step ? "done" : i + 1 === step ? "now" : ""}"></i>`)}</div>
      <div class="wiz-body"><div class="head" id="head"></div><div id="content"></div></div>
      <div class="wiz-foot"><button class="btn btn-ghost" data-on-click="back">${icon("back", 16)} Back</button><div class="row" id="actions"></div></div>
    </div>`);
    bind(root, { back: () => (mode === "form" && hasItems(name) ? (mode = "list", render()) : goto(step - 1)) });
    const head = (title, lead) => mount($("#head", root), html`<h1 class="title">${title}</h1><p class="lead">${lead}</p>`);
    const content = $("#content", root);
    const actions = (markup, handlers) => { mount($("#actions", root), markup); bind($("#actions", root), handlers); };
    const next = (label = "Continue", enabled = true) => actions(html`<button class="btn btn-primary" data-on-click="next" ${enabled ? "" : "disabled"}>${label}</button>`, { next: () => goto(step + 1) });

    if (name === "region") {
      head("Keyboard and time", "So your password types the way you expect, and the clock is right.");
      regionPanel(content);
      actions(html`<button class="btn btn-primary" data-on-click="next">Continue</button>`, {
        next: async () => {
          safeSet(STEP_KEY, step + 1);
          if (store.state.device.keyboard !== startKeyboard && !store.state.dev) {
            mount(content, html`<div class="row small"><span class="spinner"></span> Applying the keyboard layout…</div>`);
            await api("/api/kiosk/restart", {}); // the page reloads into the next step
          } else goto(step + 1);
        },
      });
    }

    if (name === "network") {
      head("Get online", "Dot needs a network to reach your server. A cable is fastest.");
      const stopWifi = wifiPanel(content);
      const update = () => next(store.state.network.connected ? "Continue" : "Skip for now");
      update();
      const stopWatch = watch((st) => st.network.connected, update);
      cleanup = () => { stopWifi(); stopWatch(); };
    }

    if (name === "remote") {
      head("Away from home?", "If your server is on another network, Tailscale connects them securely without opening ports. Skip this if the server is on the same network as this Dot.");
      const stopTs = tailscalePanel(content);
      const update = () => next(store.state.tailscale.running ? "Continue" : "Skip");
      update();
      const stopWatch = watch((st) => st.tailscale.running, update);
      cleanup = () => { stopTs(); stopWatch(); };
    }

    if (name === "servers") {
      const servers = store.state.servers;
      if (mode === "form" || !servers.length) {
        head(editing ? `Edit ${editing.name}` : servers.length ? "Add another server" : "Add your Windows server",
          "The computer or Windows Server you sign in to. Remote Desktop must be turned on there.");
        serverForm(content, editing, {
          submitLabel: editing ? "Save" : "Add server",
          onDone: () => { mode = "list"; editing = null; render(); },
          onCancel: servers.length ? () => { mode = "list"; editing = null; render(); } : null,
        });
        actions("", {});
      } else {
        head("Servers", "People pick one of these when they sign in. You can add more later in Settings.");
        mount(content, html`<div id="list"></div><button class="more" style="margin-top:12px" data-on-click="add">${icon("plus", 14)} Add another server</button>`);
        cleanup = serverList($("#list", content), { onEdit: (s) => { editing = s; mode = "form"; render(); } });
        bind(content, { add: () => { mode = "form"; render(); } });
        next();
      }
    }

    if (name === "people") {
      const profiles = store.state.profiles;
      if (mode === "form" || !profiles.length) {
        head(editing ? `Edit ${editing.name}` : profiles.length ? "Add someone else" : "Who's using Dot?",
          "Each person signs in with their own Windows account. Dot never saves the password.");
        profileForm(content, editing, {
          submitLabel: editing ? "Save" : "Add person",
          onDone: () => { mode = "list"; editing = null; render(); },
          onCancel: profiles.length ? () => { mode = "list"; editing = null; render(); } : null,
        });
        actions("", {});
      } else {
        head("People", "Everyone who signs in on this Dot.");
        mount(content, html`<div id="list"></div><button class="more" style="margin-top:12px" data-on-click="add">${icon("plus", 14)} Add someone else</button>`);
        cleanup = profileList($("#list", content), { onEdit: (p) => { editing = p; mode = "form"; render(); } });
        bind(content, { add: () => { mode = "form"; render(); } });
        next();
      }
    }

    if (name === "prefs") {
      head("A few preferences", "The defaults suit most setups. Everything here is in Settings later.");
      mount(content, html`<div id="prefs"></div><div class="section-gap"><span class="label">Security</span><div id="pin"></div></div>`);
      preferencesPanel($("#prefs", content));
      pinPanel($("#pin", content));
      next();
    }

    if (name === "done") {
      const { profiles, servers } = store.state;
      head("Dot is ready", "Here's what you set up.");
      mount(content, html`<div class="card-list">
        <div class="card">${icon("users", 20)}<div class="grow"><div class="t">${profiles.map((p) => p.name).join(", ")}</div><div class="s">${profiles.length === 1 ? "1 person" : `${profiles.length} people`}</div></div></div>
        <div class="card">${icon("server", 20)}<div class="grow"><div class="t">${servers.map((s) => s.name).join(", ")}</div><div class="s">${servers.length === 1 ? "1 server" : `${servers.length} servers`}</div></div></div>
        <div class="card">${icon(store.state.network.connected ? "wifi" : "wifiOff", 20)}<div class="grow"><div class="t">${store.state.network.connected ? store.state.network.name : "Not connected"}</div><div class="s">${store.state.tailscale.running ? "Tailscale on" : "Local network"}</div></div></div>
      </div>`);
      actions(html`<button class="btn btn-primary" data-on-click="finish">Start using Dot</button>`, {
        finish: async () => {
          await api("/api/setup/complete", {});
          safeSet(STEP_KEY, 0);
          await refresh();
          go("login");
        },
      });
    }
  }

  function hasItems(name) {
    return name === "servers" ? store.state.servers.length > 0 : name === "people" ? store.state.profiles.length > 0 : false;
  }

  function renderWelcome() {
    const ip = store.state.network.ip;
    mount(root, html`<div class="screen welcome">
      <div class="brand"><div class="mark"></div><div class="word">Dot</div></div>
      <p class="lead">Let's get this Raspberry Pi signing in to your Windows server. It takes about two minutes.</p>
      <button class="btn btn-primary" data-on-click="start">Set up</button>
      <div class="foot">Dot ${store.state.version}${ip ? ` · ${ip}` : ""}</div>
    </div>`);
    bind(root, { start: () => goto(1) });
    $(".btn", root).focus();
  }

  render();
  return () => cleanup?.();
}

function safeGet(k) { try { return localStorage.getItem(k); } catch { return null; } }
function safeSet(k, v) { try { localStorage.setItem(k, String(v)); } catch { /* private mode */ } }
