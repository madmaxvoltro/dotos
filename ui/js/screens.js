// Boot, idle clock and the in-session backdrop.

import { $, api, bind, html, icon, mount, refresh, store, toast, watch } from "./lib.js";
import { detailsModal, soundPanel } from "./parts.js";

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

/** Boot: real checks behind the status line. Calls done() when it is time to show login or setup. */
export function boot(root, { done }) {
  let alive = true;
  const verbose = () => store.state?.device?.verboseBoot;
  const lines = [];

  if (verbose()) mount(root, html`<div class="screen boot-verbose" id="log"></div>`);
  else mount(root, html`<div class="screen boot">
    <div class="mark"></div>
    <div class="status" id="status" aria-live="polite"></div>
    <div class="bar"><div id="fill"></div></div>
    <div class="sub" id="sub"></div></div>`);

  const show = (status, pct, sub = "", fill = "#fff") => {
    if (!alive) return;
    if (verbose()) return;
    $("#status", root).textContent = status;
    $("#sub", root).textContent = sub;
    const bar = $("#fill", root);
    bar.style.width = `${pct}%`;
    bar.style.background = fill;
  };
  const log = (ok, text) => {
    if (!alive || !verbose()) return;
    lines.push(html`<div><span>${ok ? "[ ok ]" : "[ !! ]"}</span><span>${text}</span></div>`);
    mount($("#log", root), lines);
  };
  const waitFor = async (test, timeout) => {
    const end = Date.now() + timeout;
    while (alive && Date.now() < end) {
      const st = await refresh();
      if (st && test(st)) return true;
      await sleep(400);
    }
    return false;
  };

  (async () => {
    show("Starting system", 8);
    await waitFor((st) => st, 30000);
    const st = store.state;
    log(true, `Dot ${st.version} started on ${st.client ? st.client.name : "no FreeRDP client"}`);
    await sleep(1200);
    if (!st.setupComplete) { if (alive) done("setup"); return; }

    // network, retrying with the countdown from the design board
    show("Connecting to the network", 22);
    let tries = 0;
    while (alive && !(await waitFor((s) => s.network.connected, 8000))) {
      log(false, "No network connection");
      tries += 1;
      for (let s = 10; s > 0 && alive; s--) {
        show("No connection", 60, `Trying again in ${s} s`, "var(--accent)");
        await sleep(1000);
      }
      if (tries >= 2) { if (alive) done("login"); return; }
      show("Testing the connection", 60);
    }
    const net = store.state.network;
    log(true, `Network ${net.kind === "ethernet" ? "wired" : "Wi-Fi"} link up${net.ip ? `, ${net.ip}` : ""}`);

    if (store.state.tailscale.enabled) {
      show("Connecting to Tailscale", 45);
      const ok = await waitFor((s) => s.tailscale.running, 8000);
      log(ok, ok ? `Connected to Tailscale as ${store.state.tailscale.ip}` : "Tailscale not connected yet");
    }

    show("Testing the connection", 62);
    await sleep(700);
    show("Reaching the server", 80);
    const target = () => {
      const s = store.state;
      const p = s.profiles.find((x) => x.id === s.lastProfile) || s.profiles[0];
      return s.servers.find((x) => x.id === p?.defaultServer) || s.servers[0];
    };
    const reached = await waitFor(() => target()?.latency != null, 6000);
    const srv = target();
    log(reached, srv ? (reached ? `Server ${srv.name} reachable, ${srv.latency} ms` : `Server ${srv.name} not answering`) : "No server configured");
    show("Ready", 100);
    log(true, "Login ready");
    await sleep(600);
    if (alive) done("login");
  })();

  return () => { alive = false; };
}

/** Idle: the analogue clock from the board. Any input wakes it. */
export function idle(root, { wake }) {
  const ticks = [];
  for (let i = 0; i < 60; i++) {
    const major = i % 5 === 0;
    const w = major ? 8 : 2;
    ticks.push(html`<div class="arm" style="transform:rotate(${i * 6}deg)"><div style="left:${-w / 2}px;top:-300px;width:${w}px;height:${major ? 34 : 12}px;border-radius:1px;background:${major ? "#fff" : "rgba(255,255,255,.4)"}"></div></div>`);
  }
  mount(root, html`<div class="screen idle" aria-label="Clock">
    <div class="clock">
      ${ticks}
      <div id="date" style="position:absolute;left:-70px;top:104px;width:140px;text-align:center;font-size:26px;font-weight:500;color:rgba(255,255,255,.55)"></div>
      <div class="arm" id="h"><div style="left:-8px;top:-170px;width:16px;height:170px;border-radius:4px;background:#fff"></div></div>
      <div class="arm" id="m"><div style="left:-6px;top:-262px;width:12px;height:262px;border-radius:4px;background:#fff"></div></div>
      <div class="arm" id="s"><div style="left:-2px;top:-282px;width:4px;height:344px;border-radius:2px;background:var(--accent)"></div></div>
      <div style="position:absolute;left:-11px;top:-11px;width:22px;height:22px;border-radius:50%;background:var(--accent)"></div>
      <div style="position:absolute;left:-4px;top:-4px;width:8px;height:8px;border-radius:50%;background:#000"></div>
    </div></div>`);
  let frame;
  const tick = () => {
    const now = new Date();
    const sec = now.getSeconds() + now.getMilliseconds() / 1000;
    const min = now.getMinutes() + sec / 60;
    const hour = (now.getHours() % 12) + min / 60;
    $("#s", root).style.transform = `rotate(${sec * 6}deg)`;
    $("#m", root).style.transform = `rotate(${min * 6}deg)`;
    $("#h", root).style.transform = `rotate(${hour * 30}deg)`;
    $("#date", root).textContent = now.toLocaleDateString("en-GB", { weekday: "short", day: "numeric" });
    frame = requestAnimationFrame(tick);
  };
  tick();
  const wakeUp = () => wake();
  const events = ["keydown", "mousedown", "touchstart", "wheel"];
  // a short grace period so the input that triggered nothing doesn't instantly wake it
  const t = setTimeout(() => {
    events.forEach((e) => window.addEventListener(e, wakeUp, { once: true }));
    window.addEventListener("mousemove", onMove);
  }, 500);
  let moved = 0;
  function onMove(e) { moved += Math.abs(e.movementX) + Math.abs(e.movementY); if (moved > 40) wake(); }
  return () => {
    cancelAnimationFrame(frame);
    clearTimeout(t);
    events.forEach((e) => window.removeEventListener(e, wakeUp));
    window.removeEventListener("mousemove", onMove);
  };
}

/** Under the FreeRDP window: visible only while it opens or closes. */
/** Under the session window. Ctrl+Alt+Shift+D brings it in front as Dot's session menu. */
export function session(root) {
  let stopSound = null;
  const render = (st) => {
    if (!st) return;
    stopSound?.(); stopSound = null;
    const sid = st.session?.serverId || st.connecting?.serverId;
    const name = st.servers.find((s) => s.id === sid)?.name || "the server";
    const menu = st.sessionMenu;
    mount(root, html`<div class="screen session"><div class="mark"></div><div class="msg">${st.session ? `Signed in to ${name}` : `Connecting to ${name}`}</div>
      ${st.session ? html`<p class="small" style="margin:-8px 0 0;max-width:460px;text-align:center;line-height:1.5">${menu
        ? "Disconnecting keeps Windows and your apps running: sign in again to carry on."
        : "Windows should be on screen now. If you can see this instead:"}</p>
      ${menu ? html`<div class="panel" style="width:340px;padding:10px"><div id="sound"></div></div>` : ""}
      <div class="row">
        <button class="btn btn-primary" data-on-click="show">${menu ? "Back to Windows" : "Show Windows"}</button>
        <button class="btn btn-raised btn-danger" data-on-click="disconnect">Disconnect</button>
        <button class="btn btn-ghost" data-on-click="details">Details</button>
      </div>
      <p class="small" style="margin:6px 0 0;text-align:center;line-height:1.6;color:rgba(255,255,255,.4)">Ctrl + Alt + Shift + D opens this menu from Windows.<br>In Windows, Start → your name → Disconnect or Sign out also comes back here.</p>` : ""}</div>`);
    if (menu) {
      stopSound = soundPanel($("#sound", root), { compact: true });
      $('[data-on-click="show"]', root)?.focus();
    }
    bind(root, {
      show: async () => { const r = await api("/api/session/show", {}); refresh(); if (!r.ok) toast("No Remote Desktop window found. Details shows what FreeRDP said."); },
      details: async () => {
        const { lines = [] } = await api("/api/session/log");
        detailsModal("Remote Desktop (FreeRDP)", lines.join("\n") || "FreeRDP hasn't said anything yet.");
      },
      disconnect: () => api("/api/session/disconnect", {}),
    });
  };
  render(store.state);
  // Esc in the menu: straight back to Windows
  const onKey = (e) => {
    if (e.key === "Escape" && store.state?.sessionMenu && !document.querySelector(".scrim")) {
      api("/api/session/show", {}).then(refresh);
    }
  };
  window.addEventListener("keydown", onKey);
  const stop = watch((st) => [st.session?.serverId, st.connecting?.serverId, st.sessionMenu], render);
  return () => { stop(); stopSound?.(); window.removeEventListener("keydown", onKey); };
}

/** Game mode on the main monitor: the shutter from the board while it switches, then (under Moonlight) what's playing. */
export function game(root) {
  mount(root, html`<div class="screen shutter">
    <div class="panel-top"></div><div class="panel-bottom"></div>
    <div class="center">
      <div class="mark"></div>
      <div class="msg" id="msg" aria-live="polite"></div>
      <div class="sub" id="sub"></div>
      <div class="row" id="actions" style="margin-top:12px"></div>
    </div></div>`);
  requestAnimationFrame(() => $(".shutter", root).classList.add("closed"));
  const render = (st) => {
    const g = st.games;
    const info = g.info || {};
    $("#msg", root).textContent = info.error ? "Couldn't start game mode" : g.state === "stopping" ? "Back to work" : info.step || "Switching to game mode";
    $("#sub", root).textContent = info.error || (g.state === "playing" ? "Ctrl+Alt+Shift+Q leaves the game. Ctrl+Alt+Shift+Z frees the mouse." : "");
    const actions = $("#actions", root);
    mount(actions, g.state === "playing" ? html`
      <button class="btn btn-raised" data-on-click="leave">${icon("back", 16)} Back to work</button>
      <button class="btn btn-ghost btn-danger" data-on-click="quit">Quit the game</button>` : "");
    bind(actions, {
      leave: () => api("/api/games/stop", {}),
      quit: () => api("/api/games/stop", { quit: true }),
    });
  };
  render(store.state);
  return watch((st) => [st.games.state, st.games.info], render);
}
