// Building blocks shared by first-run setup and Settings: Wi-Fi, Tailscale, servers, people, preferences.

import { $, $$, api, bind, html, icon, initial, latencyText, mount, notice, raw, refresh, store, toast, watch } from "./lib.js";

export const ACCOUNT_ERRORS = {
  wrong: ["key", "Wrong password", "Check the user name and password."],
  expired: ["clock", "Password expired", "Change it on Windows first, then try again."],
  locked: ["lock", "Account locked", "Too many tries. Ask your administrator or wait a while."],
  disabled: ["ban", "Account disabled", "This Windows account is turned off."],
  denied: ["shield", "Not allowed to sign in remotely", "Add this user to Remote Desktop Users on the server."],
  cert: ["shield", "Server identity changed", "The server's certificate is different from last time."],
  server: ["server", "Can't reach the server", "The network works. Check that the server is on."],
  offline: ["wifiOff", "No internet", "Check the cable or choose a network."],
  noclient: ["info", "FreeRDP is missing", "Run the installer again to add it."],
  noserver: ["server", "No server set up", "Add a server in Settings."],
  unknown: ["info", "Couldn't sign in", "Something unexpected happened. Try again."],
  internal: ["info", "Something went wrong in Dot", "This is a bug in Dot, not your password. Details shows the error; please send it."],
  noanswer: ["server", "Remote Desktop didn't answer", "The server took too long. Details shows what happened."],
  certcheck: ["shield", "Certificate not checked yet", "Sign in once from the login screen to review this server's certificate, then check the password here."],
  off: ["gamepad", "Game mode isn't set up", "Choose how this server plays games in Settings → Games."],
  nomoonlight: ["gamepad", "Moonlight isn't installed", "Run the installer again without --no-games."],
  nohost: ["gamepad", "No gaming PC chosen", "Pick one in Settings → Games."],
  busy: ["gamepad", "Game mode is already starting", "Give it a moment."],
};

export function modal(markup, handlers = {}) {
  const scrim = document.createElement("div");
  scrim.className = "scrim";
  mount(scrim, html`<div class="modal" role="dialog" aria-modal="true">${markup}</div>`);
  document.getElementById("app").appendChild(scrim);
  const close = () => scrim.remove();
  bind(scrim, { close, ...Object.fromEntries(Object.entries(handlers).map(([k, fn]) => [k, (e, el) => fn(e, el, close)])) });
  scrim.addEventListener("keydown", (e) => { if (e.key === "Escape") close(); });
  scrim.addEventListener("mousedown", (e) => { if (e.target === scrim) close(); });
  setTimeout(() => ($("input", scrim) || $("button", scrim))?.focus(), 30);
  return { el: scrim, close };
}

/** What actually happened: FreeRDP's own output, for a "Details" button on an error. */
export function detailsModal(title, text) {
  modal(html`<h2 class="title" style="font-size:22px">${title}</h2>
    <pre class="mono" style="margin:16px 0 0;padding:12px 14px;border-radius:12px;background:var(--raised);font-size:11.5px;line-height:1.55;white-space:pre-wrap;word-break:break-all;max-height:340px;overflow:auto;user-select:text">${text || "No details."}</pre>
    <div class="actions"><button class="btn btn-primary" data-on-click="close">Close</button></div>`);
}

export function confirmModal(title, text, label, onYes) {
  modal(html`<h2 class="title" style="font-size:22px">${title}</h2><p class="lead">${text}</p>
    <div class="actions"><button class="btn btn-ghost" data-on-click="close">Cancel</button>
    <button class="btn btn-accent" data-on-click="yes">${label}</button></div>`,
  { yes: (e, el, close) => { close(); onYes(); } });
}

// switch / segmented helpers ------------------------------------------------------------------

export const switchEl = (key, on, label) => html`<button class="switch" role="switch" aria-checked="${on ? "true" : "false"}" aria-label="${label}" data-key="${key}" data-on-click="toggle"></button>`;
export const seg = (key, value, options) => html`<div class="seg" role="group">${options.map(([v, l]) => html`<button aria-pressed="${String(v) === String(value) ? "true" : "false"}" data-key="${key}" data-value="${v}" data-on-click="pick">${l}</button>`)}</div>`;

// Wi-Fi ---------------------------------------------------------------------------------------------

export function wifiPanel(root) {
  let networks = [];
  let open = null; // ssid with password row open
  let busy = false;
  let error = "";

  const load = async (rescan) => {
    const res = await api(`/api/wifi${rescan ? "?rescan=1" : ""}`);
    networks = res.networks || [];
    render();
  };

  function render() {
    const net = store.state?.network || {};
    const status = net.connected
      ? notice(net.kind === "ethernet" ? "globe" : "wifi", net.kind === "ethernet" ? "Connected with a cable" : `Connected to ${net.name}`, [net.detail, net.ip].filter(Boolean).join(" · "), "", "ok")
      : notice("wifiOff", "Not connected", "Plug in a network cable, or pick a Wi-Fi network below.");
    mount(root, html`
      <div class="stack">${status}</div>
      <div class="row section-gap" style="justify-content:space-between;margin-bottom:8px">
        <span class="label" style="margin:0 0 0 4px">Wi-Fi networks</span>
        <button class="btn btn-ghost btn-sm" data-on-click="rescan">Scan again</button>
      </div>
      <div class="panel" style="padding:6px"><div class="list">
        ${networks.length ? networks.map((w) => html`
          <button class="item ${w.active ? "on" : ""}" data-ssid="${w.ssid}" data-secure="${w.secure ? 1 : 0}" data-on-click="choose">
            ${icon(w.secure ? "lock" : "wifi", 16)}<div class="grow"><div class="t">${w.ssid}</div><div class="s">${w.secure ? "Secured" : "Open"}, ${w.signal > 70 ? "strong" : w.signal > 40 ? "good" : "weak"}</div></div>
            <div class="meta">${w.active ? "Connected" : ""}</div>
          </button>
          ${open === w.ssid ? html`<form class="row" style="padding:6px 6px 10px" data-on-submit="join">
            <label class="field grow" style="background:var(--raised)"><span class="sr">Password for ${w.ssid}</span>
              <input type="password" name="pw" placeholder="Password" autocomplete="off"></label>
            <button class="btn btn-primary" ${busy ? "disabled" : ""}>${busy ? raw('<span class="spinner dark"></span>') : "Join"}</button>
          </form>${error ? html`<div class="small" style="padding:0 12px 10px;color:var(--accent)">${error}</div>` : ""}` : ""}`)
        : html`<div class="small" style="padding:14px 12px">Looking for networks…</div>`}
      </div></div>`);
    bind(root, {
      rescan: () => load(true),
      choose: (e, el) => {
        error = "";
        if (el.dataset.secure === "1") { open = open === el.dataset.ssid ? null : el.dataset.ssid; render(); $("input", root)?.focus(); }
        else join(el.dataset.ssid, "");
      },
      join: (e) => { e.preventDefault(); join(open, e.target.pw.value); },
    });
  }

  async function join(ssid, password) {
    busy = true; error = ""; render();
    const res = await api("/api/wifi", { ssid, password });
    busy = false;
    if (res.ok) { open = null; toast(`Connected to ${ssid}`); await refresh(); load(); }
    else { error = res.error || "Could not connect"; render(); }
  }

  render();
  load();
  return watch((st) => st.network, () => { if (document.activeElement?.tagName !== "INPUT" || !root.contains(document.activeElement)) render(); });
}

// Tailscale -------------------------------------------------------------------------------------------

export function tailscalePanel(root) {
  let starting = false;
  function render() {
    const ts = store.state?.tailscale || {};
    let body;
    if (ts.installing) body = html`<div class="row small"><span class="spinner"></span> Installing Tailscale. This takes about a minute.</div>`;
    else if (!ts.installed) body = html`${notice("info", "Tailscale isn't installed yet", "Dot can install it now. It needs an internet connection.")}
      <div style="margin-top:12px"><button class="btn btn-raised" data-on-click="install" ${starting ? "disabled" : ""}>${starting ? raw('<span class="spinner"></span>') : ""} Install Tailscale</button></div>`;
    else if (ts.running) body = html`${notice("checkCircle", "Connected to your tailnet", `This Dot is ${ts.ip || "online"}. Servers on your tailnet are reachable by name.`, "", "ok")}
      <div style="margin-top:12px"><button class="btn btn-raised btn-sm" data-on-click="off">Turn off</button></div>`;
    else if (ts.enabled && (ts.qr || ts.loginUrl)) body = html`<div class="row" style="align-items:flex-start;gap:20px">
        ${ts.qr ? html`<div class="qr" aria-label="QR code to sign in to Tailscale">${ts.qr}</div>` : ""}
        <div class="grow"><div style="font-size:15px;font-weight:600">Scan with your phone</div>
        <p class="lead" style="font-size:14px">Or open this link on any device and sign in to approve this Dot:</p>
        <div class="mono" style="font-size:13px;margin-top:8px;word-break:break-all">${ts.loginUrl}</div>
        <div class="row small" style="margin-top:14px"><span class="spinner"></span> Waiting for approval</div></div></div>`;
    else body = html`<button class="btn btn-raised" data-on-click="on" ${starting ? "disabled" : ""}>${starting ? raw('<span class="spinner"></span>') : ""} Connect with Tailscale</button>`;
    mount(root, body);
    bind(root, {
      on: async () => { starting = true; render(); await api("/api/tailscale/up", {}); starting = false; refresh(); },
      off: async () => { await api("/api/tailscale/down", {}); refresh(); },
      install: async () => { starting = true; render(); await api("/api/tailscale/install", {}); starting = false; refresh(); },
    });
  }
  render();
  return watch((st) => st.tailscale, render);
}

// servers ------------------------------------------------------------------------------------------------

export function serverList(root, { onEdit, compact = false } = {}) {
  function render() {
    const servers = store.state?.servers || [];
    mount(root, servers.length ? html`<div class="card-list">${servers.map((s) => html`
      <div class="card">
        <span class="dotlat ${s.latency == null ? "down" : ""}"></span>
        <div class="grow"><div class="t">${s.name}</div><div class="s">${s.host}${s.port !== 3389 ? `:${s.port}` : ""}${s.gateway ? ` via ${s.gateway}` : ""} · ${latencyText(s.latency)}</div></div>
        <button class="icon-btn" aria-label="Edit ${s.name}" data-id="${s.id}" data-on-click="edit">${icon("pencil", 16)}</button>
        ${compact ? "" : html`<button class="icon-btn" aria-label="Remove ${s.name}" data-id="${s.id}" data-on-click="del">${icon("trash", 16)}</button>`}
      </div>`)}</div>` : html`<div class="small" style="padding:4px">No servers yet.</div>`);
    bind(root, {
      edit: (e, el) => onEdit?.(servers.find((s) => s.id === el.dataset.id)),
      del: (e, el) => {
        const s = servers.find((x) => x.id === el.dataset.id);
        confirmModal(`Remove ${s.name}?`, "People who use it will no longer see it.", "Remove", async () => { await api("/api/servers/delete", { id: s.id }); refresh(); });
      },
    });
  }
  render();
  return watch((st) => st.servers, render);
}

export function serverForm(root, server, { onDone, onCancel, submitLabel = "Save server" } = {}) {
  const s = { name: "", host: "", port: 3389, domain: "", gateway: "", ...server };
  let more = Boolean(s.domain || s.gateway || (s.port && s.port !== 3389));
  let test = null; // null | "busy" | {ok, latency}
  // who signs in here, and as whom: the same data as each person's "Different account"
  const people = (store.state?.profiles || []).map((p) => ({
    id: p.id,
    name: p.name,
    fallback: `${p.domain ? `${p.domain}\\` : ""}${p.username}`,
    on: s.id ? p.servers.includes(s.id) : true,
    username: (s.id && p.accounts?.[s.id]?.username) || "",
    domain: (s.id && p.accounts?.[s.id]?.domain) || "",
  }));

  function render() {
    mount(root, html`<form class="stack" data-on-submit="save" autocomplete="off">
      <label><span class="label">Name</span><span class="field"><input name="name" value="${s.name}" placeholder="Office PC"></span></label>
      <label><span class="label">Address</span><span class="field"><input name="host" value="${s.host}" placeholder="192.168.1.20 or office-pc" required data-on-input="dirty">
        <button type="button" class="btn btn-raised btn-sm" data-on-click="test">${test === "busy" ? raw('<span class="spinner"></span>') : "Test"}</button></span></label>
      ${test && test !== "busy" ? (test.ok ? notice("checkCircle", "Server answered", `${test.latency} ms. Remote Desktop is listening.`, "", "ok") : notice("server", "No answer", "Check the address, that the server is on, and that Remote Desktop is enabled.")) : ""}
      ${more ? html`
        <div class="row"><label class="grow"><span class="label">Port</span><span class="field"><input name="port" inputmode="numeric" value="${s.port}"></span></label>
        <label class="grow"><span class="label">Domain (optional)</span><span class="field"><input name="domain" value="${s.domain}" placeholder="CONTOSO"></span></label></div>
        <label><span class="label">RD Gateway (optional)</span><span class="field"><input name="gateway" value="${s.gateway}" placeholder="gateway.example.com"></span></label>`
      : html`<div><button type="button" class="more" data-on-click="more">${icon("plus", 14)} Port, domain, gateway</button></div>`}
      ${s.certFingerprint ? html`<div class="setting"><div class="grow"><div class="t">Remembered certificate</div>
        <div class="s mono" style="font-size:11.5px">${s.certFingerprint.slice(0, 32)}…</div></div>
        <button type="button" class="btn btn-ghost btn-sm" data-on-click="forgetCert">Forget</button></div>` : ""}
      ${people.length ? html`<div><span class="label">Who signs in here</span><div class="panel" style="padding:6px"><div class="list">
        ${people.map((p) => html`<div class="item ${p.on ? "on" : ""}" style="flex-wrap:wrap">
          <button type="button" class="switch" role="switch" aria-checked="${p.on}" data-id="${p.id}" data-on-click="who"><span class="sr">${p.name} uses this server</span></button>
          <div class="grow"><div class="t">${p.name}</div><div class="s">${p.on ? (p.username ? "Own account on this server" : `Signs in as ${p.fallback}`) : "Doesn't use this server"}</div></div>
          ${p.on ? html`<div class="row" style="width:100%;padding:8px 0 4px 58px">
            <label class="grow"><span class="sr">${p.name}'s user name on this server</span><span class="field" style="height:40px;background:var(--raised)"><input name="who-user-${p.id}" value="${p.username}" placeholder="${p.fallback}" autocapitalize="off" spellcheck="false"></span></label>
            <label style="width:130px"><span class="sr">${p.name}'s domain on this server</span><span class="field" style="height:40px;background:var(--raised)"><input name="who-domain-${p.id}" value="${p.domain}" placeholder="${s.domain || "Domain"}" spellcheck="false"></span></label>
          </div>` : ""}
        </div>`)}
      </div></div><p class="small" style="margin:6px 4px 0">Leave a user name empty to use that person's usual one.</p></div>` : ""}
      <div class="row" style="justify-content:flex-end;margin-top:8px">
        ${onCancel ? html`<button type="button" class="btn btn-ghost" data-on-click="cancel">Cancel</button>` : ""}
        <button class="btn btn-primary">${submitLabel}</button>
      </div>
    </form>`);
    bind(root, {
      dirty: () => { test = null; },
      who: (e, el) => { read(); const p = people.find((x) => x.id === el.dataset.id); p.on = !p.on; render(); },
      forgetCert: async () => {
        read();
        await api("/api/servers/forget-cert", { id: s.id });
        delete s.certFingerprint;
        await refresh();
        render();
        toast("Dot will ask about this server's certificate again");
      },
      more: () => { read(); more = true; render(); },
      cancel: () => onCancel?.(),
      test: async () => {
        read(); if (!s.host) return;
        test = "busy"; render();
        test = await api("/api/servers/test", { host: s.host, port: s.port });
        render();
      },
      save: async (e) => {
        e.preventDefault(); read();
        const res = await api("/api/servers", s);
        if (!res.ok) return toast(res.error || "Couldn't save");
        await refresh();
        await savePeople(res.server.id);
        onDone?.(res.server);
      },
    });
  }
  function read() {
    const f = $("form", root);
    for (const k of ["name", "host", "port", "domain", "gateway"]) if (f[k]) s[k] = k === "port" ? Number(f[k].value) || 3389 : f[k].value.trim();
    for (const p of people) {
      if (f[`who-user-${p.id}`]) p.username = f[`who-user-${p.id}`].value.trim();
      if (f[`who-domain-${p.id}`]) p.domain = f[`who-domain-${p.id}`].value.trim();
    }
  }

  /** Write "who signs in here" back to each person whose access or account changed. */
  async function savePeople(serverId) {
    for (const who of people) {
      const p = store.state.profiles.find((x) => x.id === who.id);
      if (!p) continue;
      const servers = who.on ? [...new Set([...p.servers, serverId])] : p.servers.filter((x) => x !== serverId);
      const accounts = { ...(p.accounts || {}) };
      if (who.on && who.username) accounts[serverId] = { username: who.username, domain: who.domain };
      else delete accounts[serverId];
      const same = JSON.stringify(servers) === JSON.stringify(p.servers) && JSON.stringify(accounts) === JSON.stringify(p.accounts || {});
      if (!same) await api("/api/profiles", { ...p, servers, accounts });
    }
    await refresh();
  }
  render();
  setTimeout(() => $("input", root)?.focus(), 30);
}

// people ----------------------------------------------------------------------------------------------------

export function profileList(root, { onEdit } = {}) {
  function render() {
    const { profiles = [], servers = [] } = store.state || {};
    const name = (id) => servers.find((s) => s.id === id)?.name || "no server";
    mount(root, profiles.length ? html`<div class="card-list">${profiles.map((p) => html`
      <div class="card">
        <div class="avatar" style="width:40px;height:40px;font-size:17px;background:${p.color}">${initial(p.name)}</div>
        <div class="grow"><div class="t">${p.name}</div><div class="s">${p.domain ? `${p.domain}\\` : ""}${p.username} · ${name(p.defaultServer)}${p.servers.length > 1 ? ` +${p.servers.length - 1}` : ""}${Object.keys(p.accounts || {}).length ? " · own accounts on some servers" : ""}</div></div>
        <button class="icon-btn" aria-label="Edit ${p.name}" data-id="${p.id}" data-on-click="edit">${icon("pencil", 16)}</button>
        <button class="icon-btn" aria-label="Remove ${p.name}" data-id="${p.id}" data-on-click="del">${icon("trash", 16)}</button>
      </div>`)}</div>` : html`<div class="small" style="padding:4px">Nobody yet.</div>`);
    bind(root, {
      edit: (e, el) => onEdit?.(profiles.find((p) => p.id === el.dataset.id)),
      del: (e, el) => {
        const p = profiles.find((x) => x.id === el.dataset.id);
        confirmModal(`Remove ${p.name}?`, "Their Windows account isn't touched; they just disappear from this Dot.", "Remove", async () => { await api("/api/profiles/delete", { id: p.id }); refresh(); });
      },
    });
  }
  render();
  return watch((st) => [st.profiles, st.servers.map((s) => s.name)], render);
}

export function profileForm(root, profile, { onDone, onCancel, submitLabel = "Save" } = {}) {
  const servers = store.state?.servers || [];
  const p = { name: "", username: "", domain: "", servers: servers.map((s) => s.id), defaultServer: servers[0]?.id, ...profile };
  p.accounts = { ...(profile?.accounts || {}) }; // server id -> {username, domain} when it differs
  const editingAcct = new Set(Object.keys(p.accounts));
  let check = null; // null | "busy" | result string
  const who = (sid) => {
    const a = p.accounts[sid]?.username ? p.accounts[sid] : p;
    return a.username ? `${a.domain ? `${a.domain}\\` : ""}${a.username}` : "your Windows user name";
  };

  function render() {
    const domainHint = servers.find((s) => s.id === p.defaultServer)?.domain || "Optional";
    mount(root, html`<form class="stack" data-on-submit="save" autocomplete="off">
      <label><span class="label">Name on this Dot</span><span class="field"><input name="name" value="${p.name}" placeholder="Maxime"></span></label>
      <div class="row">
        <label class="grow"><span class="label">${servers.length > 1 ? "Windows user name (unless a server has its own)" : "Windows user name"}</span><span class="field"><input name="username" value="${p.username}" placeholder="maxime" required autocapitalize="off" spellcheck="false"></span></label>
        <label style="width:150px"><span class="label">Domain</span><span class="field"><input name="domain" value="${p.domain}" placeholder="${domainHint}" spellcheck="false"></span></label>
      </div>
      ${servers.length > 1 ? html`<div><span class="label">Servers this person can use</span><div class="panel" style="padding:6px"><div class="list">
        ${servers.map((s) => {
          const on = p.servers.includes(s.id);
          const own = editingAcct.has(s.id);
          return html`<div class="item ${on ? "on" : ""}" style="flex-wrap:wrap">
          <button type="button" class="switch" role="switch" aria-checked="${on}" data-id="${s.id}" data-on-click="toggleServer"><span class="sr">Use ${s.name}</span></button>
          <div class="grow"><div class="t">${s.name}</div><div class="s">${on ? `Signs in as ${who(s.id)}` : s.host}</div></div>
          ${on ? html`<button type="button" class="btn btn-ghost btn-sm" data-id="${s.id}" data-on-click="ownAccount">${own ? "Same as above" : "Different account"}</button>
            <button type="button" class="btn btn-sm ${p.defaultServer === s.id ? "btn-raised" : "btn-ghost"}" data-id="${s.id}" data-on-click="makeDefault" aria-pressed="${p.defaultServer === s.id}">${p.defaultServer === s.id ? "Default" : "Make default"}</button>` : ""}
          ${on && own ? html`<div class="row" style="width:100%;padding:8px 0 4px 58px">
            <label class="grow"><span class="sr">User name on ${s.name}</span><span class="field" style="height:40px;background:var(--raised)"><input name="acct-user-${s.id}" value="${p.accounts[s.id]?.username || ""}" placeholder="User name on ${s.name}" autocapitalize="off" spellcheck="false"></span></label>
            <label style="width:130px"><span class="sr">Domain on ${s.name}</span><span class="field" style="height:40px;background:var(--raised)"><input name="acct-domain-${s.id}" value="${p.accounts[s.id]?.domain || ""}" placeholder="${s.domain || "Domain"}" spellcheck="false"></span></label>
          </div>` : ""}
        </div>`;
        })}
      </div></div></div>` : ""}
      <div><span class="label">Check the password (optional, never saved)</span>
        <span class="field"><input name="pw" type="password" placeholder="Windows password">
        <button type="button" class="btn btn-raised btn-sm" data-on-click="check">${check === "busy" ? raw('<span class="spinner"></span>') : "Check"}</button></span></div>
      ${check && check !== "busy" ? (check === "ok" ? notice("checkCircle", "Password works", "Dot will ask for it at every sign in.", "", "ok") : notice(...(ACCOUNT_ERRORS[check] || ACCOUNT_ERRORS.unknown))) : ""}
      <div class="row" style="justify-content:flex-end;margin-top:8px">
        ${onCancel ? html`<button type="button" class="btn btn-ghost" data-on-click="cancel">Cancel</button>` : ""}
        <button class="btn btn-primary">${submitLabel}</button>
      </div>
    </form>`);
    bind(root, {
      cancel: () => onCancel?.(),
      toggleServer: (e, el) => {
        read();
        const id = el.dataset.id;
        p.servers = p.servers.includes(id) ? p.servers.filter((x) => x !== id) : [...p.servers, id];
        if (!p.servers.includes(p.defaultServer)) p.defaultServer = p.servers[0];
        render();
      },
      makeDefault: (e, el) => { read(); p.defaultServer = el.dataset.id; render(); },
      ownAccount: (e, el) => {
        read();
        const id = el.dataset.id;
        if (editingAcct.has(id)) { editingAcct.delete(id); delete p.accounts[id]; } else editingAcct.add(id);
        render();
        $(`input[name="acct-user-${id}"]`, root)?.focus();
      },
      check: async () => {
        read();
        const pw = $("form", root).pw.value;
        if (!p.username || !pw) return toast("Fill in the user name and password first");
        check = "busy"; render();
        const a = p.accounts[p.defaultServer]?.username ? p.accounts[p.defaultServer] : p;
        const res = await api("/api/account/check", { serverId: p.defaultServer, username: a.username, domain: a.domain, password: pw });
        check = res.result || "unknown"; render();
      },
      save: async (e) => {
        e.preventDefault(); read();
        const { pw, ...payload } = p; // eslint-disable-line no-unused-vars
        const res = await api("/api/profiles", payload);
        if (!res.ok) return toast(res.error || "Couldn't save");
        await refresh();
        onDone?.(res.profile);
      },
    });
  }
  function read() {
    const f = $("form", root);
    p.name = f.name.value.trim(); p.username = f.username.value.trim(); p.domain = f.domain.value.trim();
    for (const id of editingAcct) {
      const u = f[`acct-user-${id}`], d = f[`acct-domain-${id}`];
      if (u) p.accounts[id] = { username: u.value.trim(), domain: d ? d.value.trim() : "" };
    }
  }
  render();
  setTimeout(() => $("input", root)?.focus(), 30);
}

// preferences ----------------------------------------------------------------------------------------------

const KEYBOARDS = [["us", "English (US)"], ["gb", "English (UK)"], ["be", "Belgian"], ["fr", "French"], ["de", "German"], ["nl", "Dutch"], ["es", "Spanish"], ["it", "Italian"], ["pt", "Portuguese"], ["ch", "Swiss"], ["se", "Swedish"], ["no", "Norwegian"], ["dk", "Danish"], ["fi", "Finnish"], ["pl", "Polish"], ["jp", "Japanese"]];

export function regionPanel(root) {
  api("/api/timezones").then(({ timezones = [] }) => {
    const d = store.state.device;
    const zone = d.timezone || Intl.DateTimeFormat().resolvedOptions().timeZone;
    mount(root, html`<div class="stack">
      <label><span class="label">Keyboard layout</span><span class="field">${icon("keyboard", 18)}<select name="keyboard" data-on-change="kb">
        ${KEYBOARDS.map(([v, l]) => html`<option value="${v}" ${v === d.keyboard ? "selected" : ""}>${l}</option>`)}</select>${icon("chevDown", 16)}</span></label>
      <label><span class="label">Time zone</span><span class="field">${icon("globe", 18)}<select name="timezone" data-on-change="tz">
        ${timezones.map((t) => html`<option ${t === zone ? "selected" : ""}>${t}</option>`)}</select>${icon("chevDown", 16)}</span></label>
      <div class="setting"><div class="grow"><div class="t">24-hour clock</div></div>${switchEl("use24h", d.use24h, "24-hour clock")}</div>
      <p class="small" style="margin:4px">A new keyboard layout works right away. Try it in any text field.</p>
    </div>`);
    bind(root, {
      kb: (e) => save({ device: { keyboard: e.target.value } }),
      tz: (e) => save({ device: { timezone: e.target.value } }),
      toggle: (e, el) => { const on = el.getAttribute("aria-checked") !== "true"; el.setAttribute("aria-checked", on); save({ device: { use24h: on } }); },
    });
    if (!d.timezone) save({ device: { timezone: zone } });
  });
}

export function preferencesPanel(root, { connection = true, device = true } = {}) {
  function render() {
    const { rdp, display, device: dv } = store.state;
    mount(root, html`<div class="stack">
      ${connection ? html`<span class="label" style="margin-bottom:-4px">Connection</span>
      <div class="setting"><div class="grow"><div class="t">Picture quality</div><div class="s">Auto adapts to the network. Pick Slow for VPN or mobile data.</div></div>
        ${seg("rdp.quality", rdp.quality, [["auto", "Auto"], ["lan", "Fast"], ["low", "Slow"]])}</div>
      <div class="group">
        <div class="setting"><div class="grow"><div class="t">Sound</div><div class="s">Play the server's audio through this Dot.</div></div>${switchEl("rdp.sound", rdp.sound, "Sound")}</div>
        <div class="setting"><div class="grow"><div class="t">Microphone</div><div class="s">For calls on the server.</div></div>${switchEl("rdp.microphone", rdp.microphone, "Microphone")}</div>
        <div class="setting"><div class="grow"><div class="t">Clipboard</div></div>${switchEl("rdp.clipboard", rdp.clipboard, "Clipboard")}</div>
        <div class="setting"><div class="grow"><div class="t">Graphics</div><div class="s">If Windows stays black after signing in, try Compatible. Fast uses RDP's video stream.</div></div>
          ${seg("rdp.graphics", rdp.graphics || "auto", [["auto", "Fast"], ["compatible", "Compatible"]])}</div>
        <div class="setting"><div class="grow"><div class="t">Remote Desktop client</div><div class="s">Auto uses FreeRDP's X11 client. Try SDL if Windows looks wrong or won't connect.</div></div>
          ${seg("rdp.client", rdp.client || "auto", [["auto", "Auto"], ["x11", "X11"], ["sdl", "SDL"]])}</div>
        <div class="setting"><div class="grow"><div class="t">Detailed log</div><div class="s">FreeRDP writes everything it does to Dot's log (About → Show Dot's log). For finding problems.</div></div>${switchEl("rdp.debugLog", rdp.debugLog, "Detailed log")}</div>
        <div class="setting"><div class="grow"><div class="t">USB drives</div><div class="s">Sticks plugged into the Pi show up on the server.</div></div>${switchEl("rdp.usbDrives", rdp.usbDrives, "USB drives")}</div>
      </div>` : ""}
      ${device ? html`<span class="label ${connection ? "section-gap" : ""}" style="margin-bottom:-4px">This Dot</span>
      <div class="setting"><div class="grow"><div class="t">Show the clock after</div><div class="s">When nobody signs in for a while.</div></div>
        ${seg("device.idleMinutes", dv.idleMinutes, [[0, "Never"], [2, "2 min"], [5, "5 min"], [15, "15 min"]])}</div>
      <div class="setting"><div class="grow"><div class="t">Interface size</div></div>
        ${seg("display.scale", display.scale, [[0.9, "Small"], [1, "Normal"], [1.25, "Large"], [1.5, "Larger"]])}</div>
      <div class="setting"><div class="grow"><div class="t">Detailed start-up</div><div class="s">Show every start-up step as text.</div></div>${switchEl("device.verboseBoot", dv.verboseBoot, "Detailed start-up")}</div>` : ""}
    </div>`);
    bind(root, {
      toggle: (e, el) => { const on = el.getAttribute("aria-checked") !== "true"; el.setAttribute("aria-checked", on); save(path(el.dataset.key, on)); },
      pick: (e, el) => {
        $$(`[data-key="${el.dataset.key}"]`, root).forEach((b) => b.setAttribute("aria-pressed", b === el));
        const v = el.dataset.value;
        save(path(el.dataset.key, isNaN(Number(v)) ? v : Number(v)));
      },
    });
  }
  const path = (key, value) => { const [a, b] = key.split("."); return { [a]: { [b]: value } }; };
  render();
}

export function pinPanel(root) {
  function render() {
    const has = store.state.device.hasPin;
    mount(root, html`<div class="setting"><div class="grow"><div class="t">Settings PIN</div><div class="s">${has ? "Settings are locked with a PIN." : "Anyone at this Dot can open Settings. Add a PIN to lock them."}</div></div>
      <button class="btn btn-raised btn-sm" data-on-click="set">${has ? "Change" : "Add PIN"}</button>
      ${has ? html`<button class="btn btn-ghost btn-sm" data-on-click="clear">Remove</button>` : ""}</div>`);
    bind(root, {
      set: () => modal(html`<h2 class="title" style="font-size:22px">Settings PIN</h2><p class="lead">4 to 8 digits. You'll need it to change servers, people or settings.</p>
        <form data-on-submit="save" style="margin-top:20px"><label class="field"><span class="sr">New PIN</span><input name="pin" type="password" inputmode="numeric" maxlength="8" placeholder="New PIN"></label>
        <div class="actions"><button type="button" class="btn btn-ghost" data-on-click="close">Cancel</button><button class="btn btn-primary">Save PIN</button></div></form>`,
      { save: async (e, el, close) => {
        e.preventDefault();
        const pin = e.target.pin.value;
        if (!/^\d{4,8}$/.test(pin)) return toast("Use 4 to 8 digits");
        await api("/api/pin", { pin }); close(); await refresh(); render(); toast("PIN saved");
      } }),
      clear: async () => { await api("/api/pin", { pin: null }); await refresh(); render(); },
    });
  }
  render();
}

export async function save(patch) {
  await api("/api/settings", patch);
  await refresh();
}

// sound: one volume (the monitor's speakers or this Dot), the output, and Windows' volume ----------------------

export const volumeIcon = (a) => (!a || a.muted || a.volume === 0 ? "volumeX" : a.volume < 50 ? "volumeLow" : "volume");

let volumeTimer;
export function setVolume(patch) {
  clearTimeout(volumeTimer); // slider drags: DDC is slow, send the last value
  volumeTimer = setTimeout(async () => { await api("/api/volume", patch); refresh(); }, patch.volume !== undefined ? 150 : 0);
}

export function soundPanel(root, { compact = false } = {}) {
  let audio = null;
  const load = async () => { audio = await api("/api/audio"); render(); };

  function render() {
    if (!audio) return mount(root, html`<div class="small">Loading…</div>`);
    const where = audio.using === "monitor" ? "the monitor's speakers" : "this Dot";
    mount(root, html`<div class="stack">
      <div class="setting" style="${compact ? "background:transparent;padding:4px 4px 8px" : ""}">
        <button class="icon-btn" aria-label="${audio.muted ? "Unmute" : "Mute"}" data-on-click="mute">${icon(volumeIcon(audio), 18)}</button>
        <input class="range grow" type="range" min="0" max="100" value="${audio.volume ?? 0}" data-on-input="slide" aria-label="Volume">
        <div class="small mono" style="width:32px;text-align:right" id="vol">${audio.volume ?? "–"}</div>
      </div>
      ${compact ? html`<div class="small" style="margin:-4px 8px 6px">Sets ${where}${audio.followWindows ? ". Windows' volume moves it too." : "."}</div>` : ""}
      <div>${compact ? "" : html`<span class="label">Output</span>`}<div class="${compact ? "" : "panel"}" style="padding:${compact ? 0 : 6}px"><div class="list">
        ${audio.sinks.map((k) => html`<button class="item ${k.name === audio.sink ? "on" : ""}" role="menuitemradio" aria-checked="${k.name === audio.sink}" data-name="${k.name}" data-on-click="sink">
          ${icon(/speakers|monitor/i.test(k.label) ? "monitor" : "volume", 16)}<div class="grow"><div class="t">${k.label}</div></div>
          <div class="meta">${k.name === audio.sink ? icon("check", 16) : ""}</div></button>`)}
      </div></div></div>
      ${compact ? "" : html`
      <span class="label section-gap" style="margin-bottom:-4px">Windows</span>
      <div class="setting"><div class="grow"><div class="t">Follow Windows' volume</div><div class="s">Windows sends its volume over Remote Desktop. Dot turns the real speakers up and down to match, so Windows' slider and its volume keys control your speakers. Nothing to install on Windows.</div></div>
        ${switchEl("audio.followWindows", audio.followWindows, "Follow Windows' volume")}</div>
      <div class="setting"><div class="grow"><div class="t">Volume lives on</div><div class="s">${{ auto: "The monitor's speakers when sound plays through a monitor that allows it, otherwise this Dot.", monitor: "The monitor's own volume, over its HDMI cable.", pi: "This Dot's output. Use this for headphones or USB speakers." }[audio.target]}</div></div>
        ${seg("audio.target", audio.target, [["auto", "Auto"], ["monitor", "Monitor"], ["pi", "This Dot"]])}</div>
      ${audio.windows != null ? html`<p class="small" style="margin:0 4px">Windows last set the volume to ${audio.windows}%.</p>` : ""}`}
    </div>`);
    bind(root, {
      slide: (e) => { $("#vol", root).textContent = e.target.value; audio.volume = Number(e.target.value); setVolume({ volume: audio.volume, muted: false }); },
      mute: () => { audio.muted = !audio.muted; setVolume({ muted: audio.muted }); render(); },
      sink: async (e, el) => { audio = { ...audio, ...(await api("/api/volume", { sink: el.dataset.name })) }; render(); refresh(); },
      toggle: (e, el) => { const on = el.getAttribute("aria-checked") !== "true"; save({ audio: { followWindows: on } }).then(load); },
      pick: (e, el) => save({ audio: { target: el.dataset.value } }).then(load),
    });
  }
  render();
  load();
  return watch((st) => [st.audio?.volume, st.audio?.muted, st.audio?.sink], () => { if (document.activeElement?.type !== "range") load(); });
}
