// Settings > Bluetooth: your devices, and adding new ones (with the PIN prompts some devices need).

import { $, api, bind, html, icon, mount, raw, refresh, store, watch } from "./lib.js";
import { confirmModal, modal, switchEl } from "./parts.js";

const KIND = {
  keyboard: ["keyboard", "Keyboard"],
  mouse: ["mouse", "Mouse"],
  controller: ["gamepad", "Game controller"],
  headset: ["headphones", "Headphones"],
  speaker: ["volume", "Speaker"],
  phone: ["info", "Phone"],
  other: ["bluetooth", "Device"],
};

const HINTS = {
  controller: "Xbox: hold the pair button on top until the logo flashes fast. PlayStation: hold Share (or Create) and PS until the light flashes.",
  generic: "Put the device in pairing mode. It usually means holding its Bluetooth or power button until a light flashes.",
};

export function bluetoothPanel(root) {
  let promptModal = null;

  function render() {
    const bt = store.state.bluetooth;
    if (!bt.available) {
      mount(root, html`<div class="notice">${icon("info", 22)}<div class="grow"><div class="t">No Bluetooth on this Dot</div>
        <div class="s">The Pi's Bluetooth isn't available. On a Pi 4 or 5 it's built in; run the installer again to set it up.</div></div></div>`);
      return;
    }
    mount(root, html`<div class="stack">
      <div class="setting"><div class="grow"><div class="t">Bluetooth</div></div>${switchEl("power", bt.powered, "Bluetooth")}</div>
      ${bt.powered ? html`
        <div class="row" style="justify-content:space-between;margin:12px 0 -4px"><span class="label" style="margin:0 0 0 4px">My devices</span>
          <button class="btn ${bt.scanning ? "btn-ghost" : "btn-raised"} btn-sm" data-on-click="scan">${bt.scanning ? raw('<span class="spinner"></span> Looking… Stop') : html`${icon("plus", 14)} Add a device`}</button></div>
        ${bt.devices.length ? html`<div class="card-list">${bt.devices.map((d) => html`<div class="card">
          ${icon(KIND[d.kind]?.[0] || "bluetooth", 20)}
          <div class="grow"><div class="t">${d.name}</div>
            <div class="s">${d.connected ? "Connected" : "Not connected"}${d.battery != null ? ` · battery ${d.battery}%` : ""}</div></div>
          <button class="btn btn-ghost btn-sm" data-mac="${d.mac}" data-on="${d.connected ? "" : "1"}" data-on-click="connect">${d.connected ? "Disconnect" : "Connect"}</button>
          <button class="icon-btn" aria-label="Forget ${d.name}" data-mac="${d.mac}" data-on-click="forget">${icon("trash", 16)}</button></div>`)}</div>`
        : html`<div class="small" style="padding:0 4px">No devices yet.</div>`}
        ${bt.scanning ? html`
          <span class="label section-gap" style="margin-bottom:-4px">Nearby</span>
          <p class="small" style="margin:0 4px">${HINTS.generic} ${HINTS.controller}</p>
          <div class="panel" style="padding:6px"><div class="list">
            ${bt.found.length ? bt.found.map((d) => html`<button class="item" data-mac="${d.mac}" data-on-click="pair">
              ${icon(KIND[d.kind]?.[0] || "bluetooth", 18)}<div class="grow"><div class="t">${d.name}</div><div class="s">${KIND[d.kind]?.[1] || "Device"}</div></div>
              <div class="meta">${bt.pairing?.mac === d.mac && bt.pairing.status === "pairing" ? raw('<span class="spinner"></span>') : "Pair"}</div></button>`)
            : html`<div class="small" style="padding:14px 12px">Looking for devices…</div>`}
          </div></div>` : ""}
        ${bt.pairing?.status === "failed" ? html`<div class="notice">${icon("info", 22)}<div class="grow"><div class="t">Couldn't pair ${bt.pairing.name}</div>
          <div class="s">${bt.pairing.error === "AuthenticationRejected" || bt.pairing.error === "AuthenticationCanceled" ? "The code wasn't accepted. Try again." : "Make sure it's in pairing mode and close to the Dot, then try again."}</div></div></div>` : ""}
      ` : ""}
    </div>`);
    bind(root, {
      toggle: async (e, el) => { await api("/api/bluetooth/power", { on: el.getAttribute("aria-checked") !== "true" }); await refresh(); },
      scan: async () => { await api("/api/bluetooth/scan", { on: !store.state.bluetooth.scanning }); await refresh(); },
      pair: async (e, el) => { await api("/api/bluetooth/pair", { mac: el.dataset.mac }); await refresh(); },
      connect: async (e, el) => { el.disabled = true; await api("/api/bluetooth/connect", { mac: el.dataset.mac, on: el.dataset.on === "1" }); },
      forget: (e, el) => {
        const d = store.state.bluetooth.devices.find((x) => x.mac === el.dataset.mac);
        confirmModal(`Forget ${d.name}?`, "You'll need to pair it again to use it.", "Forget", async () => {
          await api("/api/bluetooth/forget", { mac: d.mac });
          await refresh();
        });
      },
    });
  }

  /** "Type this code on the keyboard" / "Does this code match?" while pairing. */
  function renderPrompt() {
    const bt = store.state.bluetooth;
    const p = bt.prompt;
    if (!p) { promptModal?.close(); promptModal = null; return; }
    const name = bt.pairing?.name || "the device";
    const body = p.kind === "passkey"
      ? html`<h2 class="title" style="font-size:22px">Type this on ${name}</h2><p class="lead">Then press Enter on that keyboard.</p>
        <div class="mono" style="font-size:44px;font-weight:500;letter-spacing:.25em;text-align:center;margin:24px 0">${p.code}</div>
        <div class="actions"><button class="btn btn-ghost" data-on-click="close">Cancel</button></div>`
      : html`<h2 class="title" style="font-size:22px">Pair with ${name}?</h2><p class="lead">Check that ${name} shows this code.</p>
        <div class="mono" style="font-size:44px;font-weight:500;letter-spacing:.25em;text-align:center;margin:24px 0">${p.code}</div>
        <div class="actions"><button class="btn btn-ghost" data-on-click="no">It doesn't match</button><button class="btn btn-primary" data-on-click="yes">Pair</button></div>`;
    promptModal?.close();
    promptModal = modal(body, {
      yes: async (e, el, close) => { close(); await api("/api/bluetooth/answer", { yes: true }); },
      no: async (e, el, close) => { close(); await api("/api/bluetooth/answer", { yes: false }); },
    });
  }

  render();
  const stops = [
    watch((st) => [st.bluetooth.available, st.bluetooth.powered, st.bluetooth.devices, st.bluetooth.scanning, st.bluetooth.found, st.bluetooth.pairing], render),
    watch((st) => st.bluetooth.prompt, renderPrompt),
  ];
  return () => { stops.forEach((s) => s()); promptModal?.close(); api("/api/bluetooth/scan", { on: false }); };
}
