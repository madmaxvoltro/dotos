// Settings > Games: how each server plays games, gaming PCs, pairing, the stream, the second monitor,
// and the one-line installer for the Windows side.

import { $, api, bind, html, icon, mount, refresh, store, toast, watch } from "./lib.js";
import { confirmModal, modal, save, seg } from "./parts.js";

const WINDOWS_INSTALL = "irm https://raw.githubusercontent.com/madmaxvoltro/dotos/main/windows/install.ps1 | iex";

const FLOWS = [
  ["off", "Off"],
  ["same", "This PC"],
  ["other", "Other PC"],
  ["server", "Side by side"],
];
const FLOW_TEXT = {
  off: "No game mode for this server.",
  same: "Games run on this same PC. Windows moves to the game stream while you play, then Dot brings you back.",
  other: "Games run on a separate gaming PC. Windows stays on your second monitor while you play.",
  server: "Windows Server: games in the console session, work in Remote Desktop, both at once.",
};

export function gamesPanel(root) {
  const apps = {}; // target id -> app names | null (not paired)

  async function loadApps(target) {
    apps[target] = "loading";
    render();
    const res = await api(`/api/games/apps?target=${encodeURIComponent(target)}&refresh=1`);
    apps[target] = res.apps;
    render();
  }

  const targetOf = (s) => (s.games?.mode === "other" ? s.games.host : s.id);

  function render() {
    const st = store.state;
    const g = st.games;
    const hosts = g.hosts || [];
    const helperSeen = g.helper && Date.now() / 1000 - g.helper.at < 7 * 86400;
    mount(root, html`<div class="stack">
      ${g.moonlight ? "" : html`${notice("gamepad", "Moonlight isn't installed", "Run the installer again (without --no-games) to add it.")}`}

      <span class="label" style="margin-bottom:-4px">Your servers</span>
      ${st.servers.map((s) => {
        const mode = s.games?.mode || "off";
        const target = targetOf(s);
        const list = apps[target];
        return html`<div class="panel" style="padding:14px 16px">
          <div class="row"><div class="grow"><div style="font-size:15px;font-weight:600">${s.name}</div>
            <div class="small" style="margin-top:2px;line-height:1.4">${FLOW_TEXT[mode]}</div></div>
            ${seg(`flow:${s.id}`, mode, FLOWS)}</div>
          ${mode === "other" ? html`<div class="row" style="margin-top:12px">
            <span class="small">Gaming PC</span>
            <label class="field grow" style="height:36px;background:var(--raised)"><span class="sr">Gaming PC for ${s.name}</span>
              <select data-id="${s.id}" data-on-change="host"><option value="">Choose…</option>
              ${hosts.map((h) => html`<option value="${h.id}" ${s.games?.host === h.id ? "selected" : ""}>${h.name}</option>`)}</select>${icon("chevDown", 14)}</label>
            <button class="btn btn-raised btn-sm" data-on-click="addHost">${icon("plus", 14)} Add</button></div>` : ""}
          ${mode !== "off" && target ? html`<div class="row" style="margin-top:12px">
            <div class="grow small">${list === "loading" ? "Asking the PC for its games…" : Array.isArray(list) ? `Paired · ${list.length} apps: ${list.slice(0, 4).join(", ")}${list.length > 4 ? "…" : ""}` : list === null ? "Not paired yet, or the PC isn't answering." : ""}</div>
            <button class="btn btn-ghost btn-sm" data-target="${target}" data-on-click="check">Check</button>
            <button class="btn btn-raised btn-sm" data-target="${target}" data-on-click="pair">Pair</button></div>` : ""}
        </div>`;
      })}

      <div class="row section-gap" style="justify-content:space-between;margin-bottom:-4px"><span class="label" style="margin:0 0 0 4px">Gaming PCs</span>
        <button class="btn btn-ghost btn-sm" data-on-click="addHost">${icon("plus", 14)} Add a gaming PC</button></div>
      ${hosts.length ? html`<div class="card-list">${hosts.map((h) => html`<div class="card">${icon("gamepad", 20)}
        <div class="grow"><div class="t">${h.name}</div><div class="s">${h.address}${h.mac ? " · wakes over the network" : ""}</div></div>
        <button class="icon-btn" aria-label="Edit ${h.name}" data-id="${h.id}" data-on-click="editHost">${icon("pencil", 16)}</button>
        <button class="icon-btn" aria-label="Remove ${h.name}" data-id="${h.id}" data-on-click="delHost">${icon("trash", 16)}</button></div>`)}</div>`
      : html`<div class="small" style="padding:0 4px">Only needed when games run on a different PC than Windows.</div>`}

      <span class="label section-gap" style="margin-bottom:-4px">While playing</span>
      <div class="setting"><div class="grow"><div class="t">Second monitor</div><div class="s">Windows stays there when games run on another PC or a Windows Server. When games run on the same PC, the game panel shows instead.</div></div>
        ${seg("games.monitor2", g.monitor2, [["rdp", "Windows"], ["browser", "Browser"], ["panel", "Panel"], ["clock", "Clock"], ["off", "Off"]])}</div>
      ${g.monitor2 === "browser" ? html`<label class="setting"><div class="grow"><div class="t">Browser opens</div></div>
        <span class="field" style="height:36px;width:300px;background:var(--raised)"><span class="sr">Browser start page</span><input value="${g.browserUrl}" data-on-change="url" spellcheck="false"></span></label>` : ""}
      <div class="group">
        <div class="setting"><div class="grow"><div class="t">Frame rate</div><div class="s">Max uses the main monitor's fastest refresh, like 144 Hz.</div></div>
          ${seg("games.fps", g.fps, [["max", "Max"], [120, "120"], [60, "60"]])}</div>
        <div class="setting"><div class="grow"><div class="t">Video</div><div class="s">The Pi 5 decodes HEVC in hardware, which is best for 4K and high frame rates.</div></div>
          ${seg("games.codec", g.codec, [["auto", "Auto"], ["HEVC", "HEVC"], ["H.264", "H.264"], ["AV1", "AV1"]])}</div>
        <div class="setting"><div class="grow"><div class="t">Bitrate</div></div>
          ${seg("games.bitrate", g.bitrate, [["auto", "Auto"], [20, "20"], [50, "50"], [80, "80"], [150, "150 Mbps"]])}</div>
      </div>

      <span class="label section-gap" style="margin-bottom:-4px">From the sign-in screen</span>
      <div class="setting"><div class="grow"><div class="t">Game mode PIN</div><div class="s">Game mode starts without your Windows password, unless Windows stays on the second monitor while you play. A PIN keeps others out; you enter it with the controller's arrows.</div></div>
        ${g.pinSet ? html`<div class="row"><button class="btn btn-raised btn-sm" data-on-click="setPin">Change</button><button class="btn btn-ghost btn-sm" data-on-click="clearPin">Remove</button></div>`
                   : html`<button class="btn btn-raised btn-sm" data-on-click="setPin">Set a PIN</button>`}</div>

      <span class="label section-gap" style="margin-bottom:-4px">On Windows</span>
      <div class="panel" style="padding:16px">
        <div class="row"><div class="grow"><div style="font-size:15px;font-weight:600">Game.exe and the game host</div>
          <div class="small" style="margin-top:4px;line-height:1.5">Run this once in PowerShell as administrator on the gaming PC. It installs Game.exe (start game mode from inside Windows) and pairing help. Install Sunshine or Apollo yourself first. ${helperSeen ? `Last seen ${new Date(g.helper.at * 1000).toLocaleDateString()}.` : ""}</div></div>
          ${helperSeen ? html`<span class="row small"><span class="dotlat"></span>Installed</span>` : ""}</div>
        <div class="mono" style="margin-top:12px;padding:12px 14px;border-radius:12px;background:var(--raised);font-size:12.5px;word-break:break-all;user-select:all">${WINDOWS_INSTALL}</div>
      </div>
    </div>`);

    bind(root, {
      pick: async (e, el) => {
        const [kind, id] = el.dataset.key.split(":");
        if (kind === "flow") {
          const s = store.state.servers.find((x) => x.id === id);
          await api("/api/servers", { ...s, games: { ...(s.games || {}), mode: el.dataset.value } });
          await refresh();
          return render();
        }
        const [a, b] = el.dataset.key.split(".");
        const v = el.dataset.value;
        save({ [a]: { [b]: isNaN(Number(v)) ? v : Number(v) } }).then(render);
      },
      host: async (e, el) => {
        const s = store.state.servers.find((x) => x.id === el.dataset.id);
        await api("/api/servers", { ...s, games: { ...(s.games || {}), mode: "other", host: e.target.value || null } });
        await refresh();
        render();
      },
      url: (e) => save({ games: { browserUrl: e.target.value.trim() } }),
      setPin: () => modal(html`<h2 class="title" style="font-size:22px">Game mode PIN</h2>
        <p class="lead">4 to 8 digits. Asked when game mode starts from the sign-in screen.</p>
        <form data-on-submit="savePin"><label class="field" style="margin-top:8px"><span class="sr">PIN</span>
          <input name="pin" type="password" inputmode="numeric" pattern="[0-9]*" maxlength="8" placeholder="PIN" autocomplete="off"></label>
        <div class="actions"><button type="button" class="btn btn-ghost" data-on-click="close">Cancel</button>
        <button class="btn btn-accent">Save</button></div></form>`, {
        savePin: async (e, el, close) => {
          e.preventDefault();
          const pin = e.target.pin.value.trim();
          if (!/^[0-9]{4,8}$/.test(pin)) return toast("Use 4 to 8 digits.");
          const res = await api("/api/games/pin", { pin });
          if (!res.ok) return toast(res.error || "Couldn't save the PIN");
          close(); await refresh(); render(); toast("Game mode PIN saved");
        },
      }),
      clearPin: () => confirmModal("Remove the game mode PIN?", "Anyone at this Dot can then start game mode from the sign-in screen.", "Remove", async () => {
        await api("/api/games/pin", { pin: "" }); await refresh(); render();
      }),
      check: (e, el) => loadApps(el.dataset.target),
      pair: (e, el) => pair(el.dataset.target),
      addHost: () => hostModal(null),
      editHost: (e, el) => hostModal(hosts.find((h) => h.id === el.dataset.id)),
      delHost: (e, el) => {
        const h = hosts.find((x) => x.id === el.dataset.id);
        confirmModal(`Remove ${h.name}?`, "Servers that used it for games go back to Off.", "Remove", async () => {
          await api("/api/games/hosts/delete", { id: h.id });
          await refresh();
          render();
        });
      },
    });
  }

  function hostModal(h) {
    modal(html`<h2 class="title" style="font-size:22px">${h ? `Edit ${h.name}` : "Add a gaming PC"}</h2>
      <p class="lead">The PC running Sunshine or Apollo.</p>
      <form data-on-submit="save" class="stack" style="margin-top:20px" autocomplete="off">
        <label><span class="label">Name</span><span class="field"><input name="name" value="${h?.name || ""}" placeholder="Gaming PC"></span></label>
        <label><span class="label">Address</span><span class="field"><input name="address" value="${h?.address || ""}" placeholder="192.168.1.30 or gaming-pc" required spellcheck="false"></span></label>
        <div class="actions"><button type="button" class="btn btn-ghost" data-on-click="close">Cancel</button><button class="btn btn-primary">Save</button></div>
      </form>`,
    { save: async (e, el, close) => {
      e.preventDefault();
      const res = await api("/api/games/hosts", { id: h?.id, name: e.target.name.value, address: e.target.address.value });
      if (!res.ok) return toast(res.error || "Couldn't save");
      close();
      await refresh();
      render();
    } });
  }

  async function pair(target) {
    const res = await api("/api/games/pair", { target });
    if (!res.ok) return toast(res.error || "Couldn't start pairing");
    const m = modal(html`<div id="pairing"></div>`);
    const show = () => {
      const p = store.state.games.pairing;
      if (!p || !document.body.contains(m.el)) return;
      mount($("#pairing", m.el), html`<h2 class="title" style="font-size:22px">Pair with ${p.name}</h2>
        ${p.status === "paired" ? html`<p class="lead">Paired. Dot can now stream games from ${p.name}.</p>`
        : p.status === "failed" ? html`<p class="lead">Pairing didn't finish. Check that Sunshine or Apollo is running on ${p.name} and try again.</p>`
        : html`<p class="lead">If Dot Helper is installed and you're signed in to ${p.name}, this happens by itself. Otherwise open Sunshine's web page on ${p.name} (https://localhost:47990), go to PIN, and enter:</p>
          <div class="mono" style="font-size:48px;font-weight:500;letter-spacing:.3em;text-align:center;margin:24px 0 8px">${p.pin}</div>
          <div class="row small" style="justify-content:center"><span class="spinner"></span> Waiting for ${p.name}</div>`}
        <div class="actions"><button class="btn ${p.status === "paired" ? "btn-primary" : "btn-ghost"}" data-on-click="close">${p.status === "waiting" ? "Cancel" : "Done"}</button></div>`);
      bind($("#pairing", m.el), { close: m.close });
      if (p.status === "paired") loadApps(target);
    };
    await refresh();
    show();
    const stop = watch((st) => st.games.pairing, show);
    const obs = new MutationObserver(() => { if (!document.body.contains(m.el)) { stop(); obs.disconnect(); } });
    obs.observe(document.getElementById("app"), { childList: true });
  }

  function notice(ic, t, s) {
    return html`<div class="notice">${icon(ic, 22)}<div class="grow"><div class="t">${t}</div><div class="s">${s}</div></div></div>`;
  }

  render();
  for (const s of store.state.servers) if ((s.games?.mode || "off") !== "off" && targetOf(s)) loadApps(targetOf(s));
  return watch((st) => [st.servers.map((s) => s.games), st.games.hosts, st.games.moonlight, st.games.helper], () => {
    if (document.activeElement?.tagName !== "INPUT") render();
  });
}
