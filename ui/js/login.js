// Login: the board's screen plus a server switcher, people picker and a settings entry in the dock.

import { $, api, bind, clock, html, icon, initial, latencyText, mount, notice, raw, refresh, shortDate, store, toast, watch } from "./lib.js";
import { ACCOUNT_ERRORS, modal, setVolume, soundPanel, volumeIcon, wifiPanel } from "./parts.js";

export function login(root, { go }) {
  const st0 = store.state;
  const ui = {
    profileId: st0.lastProfile || st0.profiles[0]?.id,
    serverId: null,
    busy: false,
    error: null, // {kind, serverId}
    popover: null, // "servers" | "wifi" | "power" | null
    tray: false,
    people: st0.profiles.length > 1 && !st0.lastProfile,
  };
  const cleanups = [];
  const profile = () => store.state.profiles.find((p) => p.id === ui.profileId) || store.state.profiles[0];
  const server = () => {
    const p = profile();
    const id = ui.serverId && p?.servers.includes(ui.serverId) ? ui.serverId : p?.defaultServer;
    return store.state.servers.find((s) => s.id === id);
  };

  mount(root, html`<div class="screen" id="login">
    <div class="login-col" id="col">
      <div class="avatar" id="avatar"></div>
      <div class="name" id="name"></div>
      <div id="pillwrap"></div>
      <form class="field" id="field" style="width:100%;padding:0 9px 0 16px" data-on-submit="submit" autocomplete="off">
        <label for="pw" class="sr">Password</label>
        <input id="pw" type="password" placeholder="Password" spellcheck="false">
        <button class="go" id="go" aria-label="Sign in">${icon("arrow", 16, 2.4)}</button>
      </form>
      <div id="notices" style="width:100%;display:flex;flex-direction:column;gap:8px"></div>
      <div class="links" id="links"></div>
    </div>
    <div id="pop"></div>
    <div class="dock" id="dock">
      <div class="tray" id="tray">
        <button class="icon-btn" aria-label="Sound" data-on-click="sound" id="b-sound">${icon("volume", 18)}</button>
        <button class="icon-btn" aria-label="Network" data-on-click="wifi" id="b-wifi">${icon("wifi", 18)}</button>
        <button class="icon-btn" aria-label="Settings" data-on-click="settings">${icon("gear", 18)}</button>
        <button class="icon-btn" aria-label="Shut down or restart" data-on-click="power" id="b-power">${icon("power", 18)}</button>
      </div>
      <div><div class="time" id="time"></div><div class="date" id="date"></div></div>
      <button class="chev" aria-label="Show options" aria-expanded="false" data-on-click="tray" id="chev">${icon("chevLeft", 16, 2.2)}</button>
    </div>
    <div id="people-layer"></div>
  </div>`);

  const handlers = {
    submit: (e) => { e.preventDefault(); signIn(); },
    tray: () => { ui.tray = !ui.tray; if (!ui.tray) ui.popover = null; renderDock(); renderPop(); },
    wifi: () => togglePop("wifi"),
    sound: () => togglePop("sound"),
    power: () => togglePop("power"),
    servers: () => togglePop("servers"),
    settings: openSettings,
    people: () => { ui.people = true; renderPeople(); },
    game: () => toast("Game mode is coming soon"),
    openWifi: () => { ui.tray = true; ui.popover = "wifi"; renderDock(); renderPop(); },
  };
  bind(root, handlers);

  // rendering ------------------------------------------------------------------------------------

  function renderWho() {
    const p = profile();
    const s = server();
    const av = $("#avatar", root);
    av.textContent = initial(p?.name);
    av.style.background = p?.color || "var(--raised)";
    $("#name", root).textContent = p?.name || "No one set up";
    const many = (p?.servers.length || 0) > 1;
    mount($("#pillwrap", root), s ? html`<button class="pill" id="pill" ${many ? "" : "disabled"} aria-haspopup="true" aria-expanded="${ui.popover === "servers"}" data-on-click="servers">
        <span class="dotlat ${s.latency == null ? "down" : ""}"></span>${s.name}${many ? icon("chevDown", 14) : ""}</button>` : "");
    bind($("#pillwrap", root), handlers);
    const many2 = store.state.profiles.length > 1;
    mount($("#links", root), html`${many2 ? html`<button type="button" data-on-click="people">Other user</button>` : ""}
      ${store.state.features.gameMode ? html`<button type="button" data-on-click="game">Game mode</button>` : ""}`);
    bind($("#links", root), handlers);
  }

  function renderNotices() {
    const st = store.state;
    const s = server();
    let card = "";
    const errorCard = (kind) => {
      const [ic, t, sub] = ACCOUNT_ERRORS[kind] || ACCOUNT_ERRORS.unknown;
      return notice(ic, t, sub);
    };
    if (ui.error) card = errorCard(ui.error.kind);
    else if (!st.network.connected) card = notice("wifiOff", "No internet", "Check the cable or choose a network.", '<button type="button" class="btn btn-raised btn-sm" data-on-click="openWifi">Wi-Fi</button>');
    else if (s && s.latency == null) card = notice("server", `Can't reach ${s.name}`, "The network works. Check that the server is on.");
    else if (st.lastEnd?.reason === "screen_off" && Date.now() / 1000 - st.lastEnd.at < 4 * 3600) {
      card = notice("monitor", "Locked while the monitor was off", "Your session is still open on the server. Sign in to carry on.");
    } else if (st.lastEnd && st.lastEnd.reason !== "signed_out" && Date.now() / 1000 - st.lastEnd.at < 600) {
      card = st.lastEnd.reason === "server"
        ? notice("server", "Connection lost", "The session is still running on the server. Sign in to pick up where you left off.")
        : errorCard(st.lastEnd.reason);
    } else if (!profile()) card = notice("info", "No one set up", "Open Settings to add a person.");
    mount($("#notices", root), card);
    bind($("#notices", root), handlers);
  }

  function renderBusy() {
    const go = $("#go", root);
    mount(go, ui.busy ? raw('<span class="spinner"></span>') : icon("arrow", 16, 2.4));
    go.disabled = ui.busy;
    $("#pw", root).disabled = ui.busy;
  }

  function renderDock() {
    const st = store.state;
    $("#dock", root).classList.toggle("open", ui.tray);
    $("#chev", root).setAttribute("aria-expanded", ui.tray);
    $("#time", root).textContent = clock(new Date(), st.device.use24h);
    $("#date", root).textContent = shortDate();
    mount($("#b-wifi", root), icon(st.network.connected ? "wifi" : "wifiOff", 18));
    $("#b-wifi", root).setAttribute("aria-expanded", ui.popover === "wifi");
    mount($("#b-sound", root), icon(volumeIcon(st.audio), 18));
    $("#b-sound", root).setAttribute("aria-expanded", ui.popover === "sound");
    $("#b-power", root).setAttribute("aria-expanded", ui.popover === "power");
  }

  let wifiCleanup = null;
  function renderPop() {
    wifiCleanup?.(); wifiCleanup = null;
    const pop = $("#pop", root);
    const p = profile();
    if (ui.popover === "servers" && p) {
      const servers = p.servers.map((id) => store.state.servers.find((s) => s.id === id)).filter(Boolean);
      const current = server();
      mount(pop, html`<div class="popover panel" style="left:50%;top:330px;margin-left:-160px;padding:6px" role="menu" aria-label="Choose a server"><div class="list">
        ${servers.map((s) => html`<button class="item ${s.id === current?.id ? "on" : ""}" role="menuitemradio" aria-checked="${s.id === current?.id}" data-id="${s.id}" data-on-click="pick">
          <span class="dotlat ${s.latency == null ? "down" : ""}"></span>
          <div class="grow"><div class="t">${s.name}</div><div class="s">${s.host}${s.id === p.defaultServer ? " · default" : ""}</div></div>
          <div class="meta">${latencyText(s.latency)}</div></button>`)}
      </div></div>`);
      bind(pop, { pick: (e, el) => { ui.serverId = el.dataset.id; ui.error = null; ui.popover = null; renderWho(); renderNotices(); renderPop(); $("#pw", root).focus(); } });
      $(".item.on", pop)?.focus();
    } else if (ui.popover === "wifi") {
      mount(pop, html`<div class="popover panel" style="right:32px;bottom:104px;width:380px;max-height:520px;overflow-y:auto"><div style="font-size:16px;font-weight:600;margin:0 0 12px 4px">Network</div><div id="wifi"></div></div>`);
      wifiCleanup = wifiPanel($("#wifi", pop));
    } else if (ui.popover === "sound") {
      mount(pop, html`<div class="popover panel" style="right:32px;bottom:104px;width:340px;padding:10px"><div style="font-size:16px;font-weight:600;margin:4px 0 8px 6px">Sound</div><div id="sound"></div></div>`);
      wifiCleanup = soundPanel($("#sound", pop), { compact: true });
      $("input[type=range]", pop)?.focus();
    } else if (ui.popover === "power") {
      mount(pop, html`<div class="popover panel" style="right:32px;bottom:104px;width:220px;padding:6px" role="menu"><div class="list">
        <button class="item" role="menuitem" data-on-click="restart">${icon("restart", 18)}<div class="grow"><div class="t">Restart</div></div></button>
        <button class="item" role="menuitem" data-on-click="off">${icon("power", 18)}<div class="grow"><div class="t">Shut down</div></div></button>
      </div></div>`);
      bind(pop, {
        restart: () => power("reboot"),
        off: () => power("shutdown"),
      });
      $(".item", pop)?.focus();
    } else mount(pop, "");
    renderDock();
    if ($("#pill", root)) $("#pill", root).setAttribute("aria-expanded", ui.popover === "servers");
  }

  function renderPeople() {
    const layer = $("#people-layer", root);
    if (!ui.people) return mount(layer, "");
    const { profiles } = store.state;
    mount(layer, html`<div class="people" role="dialog" aria-label="Who's signing in?">
      <h1 class="title">Who's signing in?</h1>
      <div class="grid">${profiles.map((p) => html`<button class="person" data-id="${p.id}" data-on-click="choose">
        <div class="avatar" style="background:${p.color}">${initial(p.name)}</div><div class="n">${p.name}</div><div class="u">${p.username}</div></button>`)}</div>
    </div>`);
    bind(layer, { choose: (e, el) => choose(el.dataset.id) });
    $(".person", layer)?.focus();
  }

  function choose(id) {
    ui.profileId = id; ui.serverId = null; ui.error = null; ui.people = false;
    api("/api/profile/last", { profileId: id });
    renderPeople(); renderWho(); renderNotices();
    $("#pw", root).value = "";
    $("#pw", root).focus();
  }

  function togglePop(name) {
    ui.popover = ui.popover === name ? null : name;
    renderPop();
  }

  // actions -------------------------------------------------------------------------------------------

  async function signIn() {
    const pw = $("#pw", root);
    const p = profile();
    const s = server();
    if (!p || !pw.value || ui.busy) return;
    ui.busy = true; ui.error = null; ui.popover = null;
    renderBusy(); renderNotices(); renderPop();
    const res = await api("/api/login", { profileId: p.id, serverId: s?.id, password: pw.value });
    ui.busy = false;
    if (res.ok) {
      pw.value = "";
      if (res.failover) toast(`${s.name} didn't answer, so Dot used ${store.state.servers.find((x) => x.id === res.serverId)?.name}`, 5000);
      await refresh(); // app switches to the session screen
      return;
    }
    ui.error = { kind: res.error, serverId: res.serverId };
    renderBusy(); renderNotices();
    if (res.error === "wrong") {
      const f = $("#field", root);
      f.classList.remove("shake"); void f.offsetWidth; f.classList.add("shake");
    }
    pw.select();
    pw.focus();
  }

  async function power(action) {
    ui.popover = null; renderPop();
    await api("/api/power", { action });
    go("off", { action });
  }

  async function openSettings() {
    if (!store.state.device.hasPin) {
      const res = await api("/api/admin/unlock", { pin: "" });
      if (res.ok) return go("settings", { token: res.token });
    }
    modal(html`<h2 class="title" style="font-size:22px">Settings are locked</h2><p class="lead">Enter the settings PIN.</p>
      <form data-on-submit="unlock" style="margin-top:20px"><label class="field" id="pinfield"><span class="sr">PIN</span><input name="pin" type="password" inputmode="numeric" maxlength="8" placeholder="PIN"></label>
      <div class="actions"><button type="button" class="btn btn-ghost" data-on-click="close">Cancel</button><button class="btn btn-primary">Unlock</button></div></form>`,
    { unlock: async (e, el, close) => {
      e.preventDefault();
      const res = await api("/api/admin/unlock", { pin: e.target.pin.value });
      if (res.ok) { close(); go("settings", { token: res.token }); return; }
      const f = $("#pinfield"); f.classList.remove("shake"); void f.offsetWidth; f.classList.add("shake");
      e.target.pin.select();
    } });
  }

  // keyboard: Escape closes overlays; typing anywhere goes to the password field
  const onKey = (e) => {
    if (document.querySelector(".scrim")) return;
    if (e.key === "Escape") {
      if (ui.people && store.state.lastProfile) { ui.people = false; renderPeople(); }
      else if (ui.popover) { ui.popover = null; renderPop(); }
      else if (ui.tray) { ui.tray = false; renderDock(); }
      $("#pw", root).focus();
      return;
    }
    // the keyboard's volume keys work here too (in a session Windows gets them, and Dot follows)
    const step = { AudioVolumeUp: 5, AudioVolumeDown: -5 }[e.key];
    if (step || e.key === "AudioVolumeMute") {
      e.preventDefault();
      const a = store.state.audio || {};
      if (step) setVolume({ volume: Math.max(0, Math.min(100, (a.volume ?? 50) + step)), muted: false });
      else setVolume({ muted: !a.muted });
      return;
    }
    if (!ui.people && !ui.popover && e.key.length === 1 && document.activeElement?.tagName !== "INPUT") $("#pw", root).focus();
  };
  window.addEventListener("keydown", onKey);
  cleanups.push(() => window.removeEventListener("keydown", onKey));

  renderWho(); renderNotices(); renderBusy(); renderDock(); renderPeople();
  $("#pw", root).focus();
  const timer = setInterval(renderDock, 1000);
  cleanups.push(() => clearInterval(timer));
  cleanups.push(watch((st) => [st.profiles, st.servers, st.network.connected, st.lastEnd, st.features], () => { renderWho(); renderNotices(); }));
  cleanups.push(watch((st) => [st.audio?.volume, st.audio?.muted], renderDock));
  cleanups.push(() => wifiCleanup?.());
  return () => cleanups.forEach((fn) => fn());
}

/** Shown after Restart / Shut down so the screen doesn't look frozen. */
export function off(root, { action }) {
  mount(root, html`<div class="screen session"><div class="mark"></div><div class="msg">${action === "reboot" ? "Restarting" : "Shutting down"}</div></div>`);
}
