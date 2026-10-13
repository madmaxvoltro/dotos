// Settings: people, servers, network, remote access, connection, this Dot, about.

import { $, api, bind, html, icon, mount, refresh, setAdminToken, store, toast } from "./lib.js";
import { bluetoothPanel } from "./bluetooth.js";
import { gamesPanel } from "./games.js";
import { screenPanel } from "./screen.js";
import { confirmModal, modal, pinPanel, preferencesPanel, profileForm, profileList, regionPanel, serverForm, serverList, soundPanel, tailscalePanel, wifiPanel } from "./parts.js";

const SECTIONS = [
  ["people", "People", "users"],
  ["servers", "Servers", "server"],
  ["network", "Network", "wifi"],
  ["bluetooth", "Bluetooth", "bluetooth"],
  ["remote", "Remote access", "globe"],
  ["connection", "Connection", "sliders"],
  ["screen", "Screen", "monitor"],
  ["sound", "Sound", "volume"],
  ["games", "Games", "gamepad"],
  ["device", "This Dot", "gear"],
  ["about", "About", "info"],
];

export function settings(root, { go, token }) {
  setAdminToken(token);
  let section = "people";
  let form = null; // {kind, item}
  let cleanup = null;

  mount(root, html`<div class="screen settings">
    <nav aria-label="Settings">
      <div class="brandline"><div class="mark"></div>Settings</div>
      ${SECTIONS.map(([id, label, ic]) => html`<button data-id="${id}" data-on-click="nav">${icon(ic, 18)}${label}</button>`)}
      <div class="spacer"></div>
      <button data-on-click="done">${icon("back", 18)}Back to sign in</button>
    </nav>
    <main><div class="inner"><div class="head" id="head"></div><div id="body"></div></div></main>
  </div>`);
  bind(root, {
    nav: (e, el) => { section = el.dataset.id; form = null; render(); },
    done: exit,
  });

  function exit() { setAdminToken(""); go("login"); }

  function render() {
    cleanup?.(); cleanup = null;
    root.querySelectorAll("nav button[data-id]").forEach((b) => b.setAttribute("aria-current", b.dataset.id === section ? "page" : "false"));
    const head = $("#head", root);
    const body = $("#body", root);
    const title = (t, sub, action = "") => { mount(head, html`<div><h1 class="title">${t}</h1>${sub ? html`<p class="lead">${sub}</p>` : ""}</div>${action}`); };
    const back = () => { form = null; render(); };

    if (section === "people") {
      if (form) {
        title(form.item ? `Edit ${form.item.name}` : "Add a person", "Each person signs in with their own Windows account.");
        return profileForm(body, form.item, { onDone: back, onCancel: back, submitLabel: form.item ? "Save" : "Add person" });
      }
      title("People", "Everyone who can sign in on this Dot.", html`<button class="btn btn-raised btn-sm" data-on-click="add">${icon("plus", 14)} Add</button>`);
      bind(head, { add: () => { form = { item: null }; render(); } });
      cleanup = profileList(body, { onEdit: (p) => { form = { item: p }; render(); } });
    }

    if (section === "servers") {
      if (form) {
        title(form.item ? `Edit ${form.item.name}` : "Add a server", "Remote Desktop must be turned on on the server.");
        return serverForm(body, form.item, { onDone: back, onCancel: back, submitLabel: form.item ? "Save" : "Add server" });
      }
      title("Servers", "If someone's default server doesn't answer, Dot tries their next one.", html`<button class="btn btn-raised btn-sm" data-on-click="add">${icon("plus", 14)} Add</button>`);
      bind(head, { add: () => { form = { item: null }; render(); } });
      cleanup = serverList(body, { onEdit: (s) => { form = { item: s }; render(); } });
    }

    if (section === "network") {
      title("Network");
      cleanup = wifiPanel(body);
    }

    if (section === "remote") {
      title("Remote access", "Tailscale lets this Dot reach servers on other networks without opening ports.");
      cleanup = tailscalePanel(body);
    }

    if (section === "connection") {
      title("Connection", "Applies the next time someone signs in.");
      preferencesPanel(body, { device: false });
    }

    if (section === "screen") {
      title("Screen", "Your monitors, arranged and controlled over their HDMI cables.");
      cleanup = screenPanel(body);
    }

    if (section === "sound") {
      title("Sound", "One volume for everything: Dot's slider, Windows' slider and the volume keys all move it.");
      cleanup = soundPanel(body);
    }

    if (section === "bluetooth") {
      title("Bluetooth", "Keyboards, mice, headphones and game controllers.");
      cleanup = bluetoothPanel(body);
    }

    if (section === "games") {
      title("Games", "Stream games from a PC with Moonlight, straight to your main monitor.");
      cleanup = gamesPanel(body);
    }

    if (section === "device") {
      title("This Dot");
      mount(body, html`<div id="region"></div><div class="section-gap" id="prefs"></div><div class="section-gap"><span class="label">Security</span><div id="pin"></div></div>
        <div class="section-gap"><div class="setting"><div class="grow"><div class="t">Reload the screen</div><div class="s">Reloads Dot's screen, for example if something looks stuck. Keyboard layouts apply right away.</div></div>
        <button class="btn btn-raised btn-sm" data-on-click="kiosk">Reload</button></div></div>`);
      regionPanel($("#region", body));
      preferencesPanel($("#prefs", body), { connection: false });
      pinPanel($("#pin", body));
      bind(body, { kiosk: () => api("/api/kiosk/restart", {}) });
    }

    if (section === "about") {
      title("About");
      mount(body, html`<div class="small">Loading…</div>`);
      api("/api/info").then((info) => {
        const st = store.state;
        const row = (k, v) => html`<div class="setting"><div class="grow"><div class="t">${k}</div></div><div class="small mono">${v}</div></div>`;
        mount(body, html`<div class="group">
          ${row("Dot", info.version)}
          ${row("Device", info.model)}
          ${row("Name", info.hostname)}
          ${row("IP address", st.network.ip || "Not connected")}
          ${st.tailscale.ip ? row("Tailscale", st.tailscale.ip) : ""}
          ${row("Remote Desktop client", st.client ? `${st.client.name} (FreeRDP ${st.client.major})` : "Missing")}
          ${info.temp != null ? row("Temperature", `${info.temp} °C`) : ""}
          ${row("Up for", formatUptime(info.uptime))}
        </div>
        <div class="row section-gap" style="flex-wrap:wrap">
          <button class="btn btn-raised" data-on-click="update">Update Dot</button>
          <button class="btn btn-raised" data-on-click="reboot">${icon("restart", 16)} Restart</button>
          <button class="btn btn-ghost btn-danger" data-on-click="reset">Run setup again</button>
        </div>`);
        bind(body, {
          update: () => confirmModal("Update Dot?", "Dot downloads the latest version and restarts its screen. Your settings are kept. It takes a minute or two.", "Update", async () => {
            await api("/api/system/update", {});
            updateProgress();
          }),
          reboot: () => confirmModal("Restart this Dot?", "", "Restart", () => { api("/api/power", { action: "reboot" }); go("off", { action: "reboot" }); }),
          reset: () => confirmModal("Run setup again?", "Your servers and people are kept. You'll walk through every step again.", "Run setup", async () => { await api("/api/system/reset", {}); await refresh(); go("setup"); }),
        });
      });
    }
  }

  // leave Settings after 10 idle minutes, which is when the unlock expires anyway
  let idleTimer;
  const poke = () => { clearTimeout(idleTimer); idleTimer = setTimeout(exit, 10 * 60 * 1000); };
  ["keydown", "mousedown", "mousemove"].forEach((e) => window.addEventListener(e, poke));
  poke();

  render();
  return () => {
    cleanup?.();
    clearTimeout(idleTimer);
    ["keydown", "mousedown", "mousemove"].forEach((e) => window.removeEventListener(e, poke));
  };
}

/** The same progress as the on-screen installer: bar, percentage, step and the log. */
function updateProgress() {
  const m = modal(html`<h2 class="title" style="font-size:22px">Updating Dot</h2>
    <div class="boot" style="position:static;margin-top:20px;align-items:stretch">
      <div class="bar" style="width:100%;margin:0"><div id="ufill"></div></div></div>
    <div class="row small" style="justify-content:space-between;margin-top:10px"><span id="ustep">Starting</span><span class="mono" id="upct">0%</span></div>
    <div class="mono" id="ulog" style="margin-top:16px;height:180px;overflow:hidden;font-size:11.5px;line-height:1.55;color:rgba(255,255,255,.45);white-space:pre"></div>`);
  const t = setInterval(async () => {
    if (!document.body.contains(m.el)) return clearInterval(t);
    let u;
    try { u = await api("/api/update"); } catch { return; } // dotd restarting: the page reloads shortly
    const pct = Math.round(Number(u.pct) || 0);
    $("#ufill", m.el).style.width = `${pct}%`;
    $("#upct", m.el).textContent = `${pct}%`;
    $("#ustep", m.el).textContent = u.error ? `Stopped while: ${u.step}` : u.step || "Starting";
    $("#ulog", m.el).textContent = (u.log || []).join("\n");
    if (u.done) { $("#ustep", m.el).textContent = "Done. The screen restarts now."; clearInterval(t); }
  }, 800);
}

function formatUptime(s) {
  const d = Math.floor(s / 86400), h = Math.floor((s % 86400) / 3600), m = Math.floor((s % 3600) / 60);
  return d ? `${d} d ${h} h` : h ? `${h} h ${m} min` : `${m} min`;
}
