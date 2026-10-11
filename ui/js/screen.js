// Settings > Screen: monitor arrangement (sent to Windows as-is), per-monitor controls over DDC/CI,
// and what happens when the main monitor turns off.

import { $, $$, api, bind, html, icon, mount, refresh, store, toast, watch } from "./lib.js";
import { save, seg, switchEl } from "./parts.js";

const BOX = { w: 560, h: 220, pad: 16 };
const MIN_SCALE = 0.5;
const MAX_SCALE = 4;
const CORNERS = ["nw", "ne", "sw", "se"];

export function screenPanel(root) {
  let outs = []; // working copy of the arrangement: {id, name, label, x, y, w, h, mode, transform, modes}
  let mainId = null;
  let selected = null;
  let dirty = false;
  let monitorInfo = null; // controls for the selected monitor
  const timers = {};

  function loadArrangement() {
    const { outputs = [], main } = store.state.displays || {};
    const layout = store.state.screen.layout || {};
    outs = outputs.filter((o) => o.active).map((o) => ({ ...o, w: o.width, h: o.height, scaleSetting: layout[o.id]?.scale ?? "auto" }));
    mainId = outs.find((o) => o.name === main)?.id || outs[0]?.id;
    if (!outs.find((o) => o.id === selected)) selected = mainId;
    dirty = false;
  }

  async function loadControls() {
    const sel = outs.find((o) => o.id === selected);
    monitorInfo = await api(`/api/monitor${sel ? `?connector=${encodeURIComponent(sel.name)}` : ""}`);
    renderControls();
  }

  // geometry -----------------------------------------------------------------------------------------

  /** Pixels the monitor shows in its current mode and rotation. */
  const pixels = (o) => {
    const m = /^(\d+)x(\d+)/.exec(o.mode || "");
    let w = m ? Number(m[1]) : o.pxWidth, h = m ? Number(m[2]) : o.pxHeight;
    if (o.transform === "90" || o.transform === "270") [w, h] = [h, w];
    return { w, h };
  };
  const SCALES = [1, 1.25, 1.5, 1.75, 2, 2.5, 3];
  /** Same rule as dotd: the scale that makes layout size match real size (96 dpi = 100%). */
  const autoScale = (o) => {
    if (!o.mm) return 1;
    const { w, h } = pixels(o);
    const ppi = Math.hypot(w, h) / (Math.hypot(o.mm[0], o.mm[1]) / 25.4);
    return SCALES.reduce((a, b) => (Math.abs(b - ppi / 96) < Math.abs(a - ppi / 96) ? b : a));
  };
  const scaleOf = (o) => (o.scaleSetting === "auto" ? autoScale(o) : Number(o.scaleSetting));
  /** Size in the arrangement: real-size units, so a 4K and a 1080p monitor of the same size look the same. */
  const sizeFor = (o) => { const { w, h } = pixels(o); const s = scaleOf(o); return { w: Math.round(w / s), h: Math.round(h / s) }; };

  function view() {
    const minX = Math.min(...outs.map((o) => o.x)), minY = Math.min(...outs.map((o) => o.y));
    const maxX = Math.max(...outs.map((o) => o.x + o.w)), maxY = Math.max(...outs.map((o) => o.y + o.h));
    const s = Math.min((BOX.w - BOX.pad * 2) / (maxX - minX), (BOX.h - BOX.pad * 2) / (maxY - minY));
    const ox = (BOX.w - (maxX - minX) * s) / 2 - minX * s;
    const oy = (BOX.h - (maxY - minY) * s) / 2 - minY * s;
    return { s, ox, oy };
  }

  const overlaps = (a, b) => a.x < b.x + b.w && b.x < a.x + a.w && a.y < b.y + b.h && b.y < a.y + a.h;

  /** Put a dropped monitor flush against the nearest edge of another, aligning edges when close. */
  function snap(a) {
    const others = outs.filter((o) => o !== a);
    if (!others.length) return { x: 0, y: 0 };
    const clamp = (v, lo, hi) => Math.max(lo, Math.min(hi, v));
    const align = (v, edges, size) => {
      const near = edges.map((e) => [e, Math.abs(e - v)]).sort((p, q) => p[1] - q[1])[0];
      return near && near[1] < size * 0.08 ? near[0] : v;
    };
    let best = null;
    for (const b of others) {
      const ys = clamp(a.y, b.y - a.h + 1, b.y + b.h - 1);
      const xs = clamp(a.x, b.x - a.w + 1, b.x + b.w - 1);
      const yAligned = align(ys, [b.y, b.y + b.h - a.h, b.y + (b.h - a.h) / 2], a.h);
      const xAligned = align(xs, [b.x, b.x + b.w - a.w, b.x + (b.w - a.w) / 2], a.w);
      for (const c of [
        { x: b.x + b.w, y: yAligned }, { x: b.x - a.w, y: yAligned },
        { x: xAligned, y: b.y + b.h }, { x: xAligned, y: b.y - a.h },
      ]) {
        const moved = { ...a, ...c };
        if (others.some((o) => overlaps(moved, o))) continue;
        const d = Math.hypot(c.x - a.x, c.y - a.y);
        if (!best || d < best.d) best = { ...c, d };
      }
    }
    return best ? { x: Math.round(best.x), y: Math.round(best.y) } : { x: a.x, y: a.y };
  }

  // rendering --------------------------------------------------------------------------------------------

  function render() {
    const st = store.state;
    const s = st.screen;
    const many = outs.length > 1;
    mount(root, html`<div class="stack">
      ${outs.length ? html`
        <span class="label" style="margin-bottom:-4px">Arrangement</span>
        <div class="panel" style="padding:0">
          <div class="arrange" id="arrange" style="width:${BOX.w}px;height:${BOX.h}px" role="group" aria-label="Monitor arrangement. Select a monitor, then use the arrow keys to move it."></div>
          <div class="row small" style="padding:0 16px 14px;justify-content:space-between">
            <span>${many ? "Drawn at real size. Drag them to match your desk; Windows gets the same layout." : "One monitor connected."}</span>
            <span class="row" style="gap:8px">
              ${dirty ? html`<button class="btn btn-ghost btn-sm" data-on-click="reset">Undo</button>` : ""}
              <button class="btn btn-sm ${dirty ? "btn-primary" : "btn-raised"}" data-on-click="apply" ${dirty ? "" : "disabled"}>Apply</button>
            </span>
          </div>
        </div>
        <div id="selected"></div>` : html`<div class="card">${icon("monitor", 22)}<div class="grow"><div class="t">No monitor found</div><div class="s">Check the HDMI cable.</div></div></div>`}

      ${many ? html`
        <span class="label section-gap" style="margin-bottom:-4px">In a session</span>
        <div class="setting"><div class="grow"><div class="t">Several monitors</div><div class="s">${{ extend: "Windows gets each monitor as its own display.", main: "Only the main monitor shows Windows; the others show the clock.", mirror: "Every monitor shows the same picture." }[s.mode]}</div></div>
          ${seg("screen.mode", s.mode, [["extend", "Extend"], ["main", "Main only"], ["mirror", "Mirror"]])}</div>` : ""}
      <div class="setting"><div class="grow"><div class="t">Windows scaling</div><div class="s">How big text and apps are in Windows. Per monitor follows each monitor's Scale above.</div></div>
        ${seg("screen.winScale", s.winScale, [["auto", "Per monitor"], [100, "100%"], [150, "150%"], [200, "200%"]])}</div>

      <span class="label section-gap" style="margin-bottom:-4px">When the main monitor turns off</span>
      <div class="setting"><div class="grow"><div class="t">Your session</div><div class="s">${{ lock: "Disconnects and shows the login screen when the monitor comes back. Windows keeps your apps open.", pause: "Stays open and carries on without a password when the monitor comes back.", none: "Nothing happens to the session." }[s.session]}</div></div>
        ${seg("screen.session", s.session, [["lock", "Lock"], ["pause", "Pause"], ["none", "Nothing"]])}</div>
      <div class="group">
        <div class="setting"><div class="grow"><div class="t">Mute this Dot</div></div>${switchEl("screen.mutePi", s.mutePi, "Mute this Dot")}</div>
        <div class="setting"><div class="grow"><div class="t">Mute the monitors' speakers</div><div class="s">Puts the volume back where it was afterwards.</div></div>${switchEl("screen.muteMonitor", s.muteMonitor, "Mute the monitors' speakers")}</div>
        <div class="setting"><div class="grow"><div class="t">Disconnect USB devices</div><div class="s" id="usbline">The system disk and network adapters are always kept.</div></div>${switchEl("screen.usb", s.usb, "Disconnect USB devices")}</div>
        ${s.usb ? html`<div class="setting"><div class="grow"><div class="t">Including keyboard and mouse</div><div class="s">The monitor's power button becomes the only way to wake Dot. Run Test standby once to be sure it comes back.</div></div>${switchEl("screen.inputs", s.inputs, "Including keyboard and mouse")}</div>` : ""}
      </div>
      <div class="setting"><div class="grow"><div class="t">Wait before acting</div><div class="s">Ignores quick flickers and input switches.</div></div>
        ${seg("screen.delay", s.delay, [[2, "2 s"], [5, "5 s"], [15, "15 s"], [60, "1 min"]])}</div>
      <div class="setting"><div class="grow"><div class="t">Test standby</div><div class="s">Acts as if the main monitor turned off for 10 seconds, then brings everything back.</div></div>
        <button class="btn btn-raised btn-sm" data-on-click="test" ${st.monitor.testing ? "disabled" : ""}>${st.monitor.testing ? "Testing…" : "Test"}</button></div>
    </div>`);
    bind(root, {
      apply,
      reset: () => { loadArrangement(); render(); loadControls(); },
      toggle: (e, el) => { const on = el.getAttribute("aria-checked") !== "true"; save(patchFor(el.dataset.key, on)).then(render); },
      pick: (e, el) => { const v = el.dataset.value; save(patchFor(el.dataset.key, isNaN(Number(v)) ? v : Number(v))).then(render); },
      test: async () => { await api("/api/monitor/test", { seconds: 10 }); refresh(); },
    });
    renderArrangement();
    renderControls();
  }

  function renderArrangement() {
    const box = $("#arrange", root);
    if (!box) return;
    const v = view();
    mount(box, html`${outs.map((o) => html`<button class="mon ${o.id === selected ? "sel" : ""}" data-id="${o.id}"
        style="left:${v.ox + o.x * v.s}px;top:${v.oy + o.y * v.s}px;width:${o.w * v.s}px;height:${o.h * v.s}px"
        aria-pressed="${o.id === selected}" aria-label="${o.label}${o.id === mainId ? ", main" : ""}, ${Math.round(scaleOf(o) * 100)}%. Arrow keys move it, plus and minus resize it.">
        ${o.id === mainId ? html`<span class="badge">Main</span>` : ""}
        <span class="n">${o.label}</span><span class="r">${o.inches ? `${o.inches}″ · ` : ""}${pixels(o).w} × ${pixels(o).h}</span>
        <span class="pct">${Math.round(scaleOf(o) * 100)}%</span>
        ${o.id === selected ? CORNERS.map((c) => html`<span class="handle ${c}" data-corner="${c}" aria-hidden="true"></span>`) : ""}</button>`)}`);
    $$(".mon", box).forEach((el) => {
      el.addEventListener("pointerdown", (e) => (e.target.dataset.corner ? startResize(e, el, e.target.dataset.corner) : startDrag(e, el)));
      el.addEventListener("keydown", (e) => nudge(e, el));
    });
  }

  function renderControls() {
    const box = $("#selected", root);
    if (!box) return;
    const o = outs.find((x) => x.id === selected);
    if (!o) return mount(box, "");
    const info = monitorInfo && monitorInfo.connector === o.name ? monitorInfo : null;
    const disp = info?.displays?.find((d) => d.connector === o.name);
    const slider = (key, label) => {
      const c = info?.controls?.[key];
      return c ? html`<div class="setting"><div style="width:130px" class="t">${label}</div>
        <input class="range grow" type="range" min="0" max="${c.max}" value="${c.value}" data-key="${key}" data-on-input="slide" aria-label="${label} of ${o.label}">
        <div class="small mono" style="width:36px;text-align:right" id="v-${key}">${c.value}</div></div>` : "";
    };
    const channels = disp ? [disp.ddc && "DDC/CI", disp.cec && "HDMI-CEC"].filter(Boolean) : [];
    mount(box, html`<div class="stack">
      <div class="card">${icon("monitor", 22)}<div class="grow">
        <div class="t">${o.label} <span class="small" style="font-weight:400">· ${o.name}</span></div>
        <div class="s">${!info ? "Asking the monitor…" : channels.length ? `Controlled over ${channels.join(" and ")}` : "No DDC/CI. Turn it on in the monitor's own menu to control brightness from Dot."}</div></div>
        ${o.id === mainId ? html`<span class="small">Main monitor</span>` : html`<button class="btn btn-raised btn-sm" data-on-click="makeMain">Make main</button>`}</div>
      <div class="group">
        <div class="setting"><div class="grow"><div class="t">Resolution</div></div>
          <label class="field" style="height:36px;background:var(--raised);min-width:230px"><span class="sr">Resolution of ${o.label}</span>
          <select data-on-change="mode">${o.modes.map((m) => html`<option value="${m.value}" ${m.value === o.mode ? "selected" : ""}>${m.label}</option>`)}</select>${icon("chevDown", 14)}</label></div>
        <div class="setting"><div class="grow"><div class="t">Rotation</div></div>
          ${seg("rotate", o.transform, [["normal", "0°"], ["90", "90°"], ["180", "180°"], ["270", "270°"]])}</div>
        <div class="setting"><div class="grow"><div class="t">Scale</div><div class="s">${o.mm ? `${o.inches}″ at ${Math.round(Math.hypot(pixels(o).w, pixels(o).h) / (Math.hypot(o.mm[0], o.mm[1]) / 25.4))} pixels per inch. Auto matches its real size, so text is the same size on every monitor and the mouse crosses where you expect.` : "This monitor doesn't report its size, so Auto uses 100%."}</div></div>
          ${seg("scale", o.scaleSetting, [["auto", `Auto · ${Math.round(autoScale(o) * 100)}%`], [1, "100%"], [1.5, "150%"], [2, "200%"]])}</div>
        <div class="setting"><div class="grow"><div class="t">Custom size</div><div class="s">Drag a corner of the monitor above to make it bigger or smaller, or type an exact scale. Bigger gives that monitor more room with smaller text; smaller makes everything on it larger.</div></div>
          <label class="field" style="height:36px;width:110px;background:var(--raised)"><span class="sr">Scale of ${o.label} in percent</span>
          <input type="number" inputmode="numeric" min="${MIN_SCALE * 100}" max="${MAX_SCALE * 100}" step="5" value="${Math.round(scaleOf(o) * 100)}" data-on-change="exact"><span class="suffix">%</span></label></div>
        ${slider("brightness", "Brightness")}${slider("contrast", "Contrast")}${slider("volume", "Speaker volume")}
      </div></div>`);
    bind(box, {
      makeMain: () => { mainId = o.id; dirty = true; render(); },
      exact: (e) => {
        const pct = Number(e.target.value);
        if (!pct) return;
        setScale(o, Math.min(MAX_SCALE, Math.max(MIN_SCALE, pct / 100)));
      },
      mode: (e) => { o.mode = e.target.value; Object.assign(o, sizeFor(o)); Object.assign(o, snap(o)); dirty = true; render(); },
      pick: (e, el) => {
        if (el.dataset.key === "rotate") o.transform = el.dataset.value;
        else o.scaleSetting = el.dataset.value === "auto" ? "auto" : Number(el.dataset.value);
        Object.assign(o, sizeFor(o));
        Object.assign(o, snap(o));
        dirty = true;
        render();
      },
      slide: (e, el) => {
        const key = el.dataset.key;
        $(`#v-${key}`, box).textContent = el.value;
        info.controls[key].value = Number(el.value);
        clearTimeout(timers[key]); // DDC is slow and some monitors dislike rapid writes
        timers[key] = setTimeout(() => api("/api/monitor/set", { connector: o.name, control: key, value: Number(el.value) }), 250);
      },
    });
  }

  // interaction -------------------------------------------------------------------------------------------

  function select(id) {
    if (selected === id) return;
    selected = id;
    monitorInfo = null;
    renderArrangement();
    renderControls();
    loadControls();
  }

  function startDrag(e, el) {
    const o = outs.find((x) => x.id === el.dataset.id);
    select(o.id);
    const v = view();
    const start = { px: e.clientX, py: e.clientY, x: o.x, y: o.y };
    const scale = el.getBoundingClientRect().width / (o.w * v.s); // the whole UI may be scaled
    let moved = false;
    const node = $(`.mon[data-id="${CSS.escape(o.id)}"]`, root);
    try { node.setPointerCapture(e.pointerId); } catch { /* pointer already gone */ }
    const move = (ev) => {
      const dx = (ev.clientX - start.px) / scale / v.s, dy = (ev.clientY - start.py) / scale / v.s;
      if (Math.abs(dx) + Math.abs(dy) > 2 / v.s) moved = true;
      o.x = start.x + dx; o.y = start.y + dy;
      node.style.left = `${v.ox + o.x * v.s}px`;
      node.style.top = `${v.oy + o.y * v.s}px`;
      node.classList.add("dragging");
    };
    const up = () => {
      node.removeEventListener("pointermove", move);
      node.removeEventListener("pointerup", up);
      if (moved) { Object.assign(o, snap(o)); dirty = true; render(); }
      else { o.x = start.x; o.y = start.y; renderArrangement(); }
    };
    node.addEventListener("pointermove", move);
    node.addEventListener("pointerup", up);
  }

  /** Custom scale: keeps the monitor's centre where it was, then snaps it back against its neighbours. */
  function setScale(o, scale) {
    const cx = o.x + o.w / 2, cy = o.y + o.h / 2;
    o.scaleSetting = Math.round(scale * 100) / 100;
    Object.assign(o, sizeFor(o));
    o.x = cx - o.w / 2; o.y = cy - o.h / 2;
    Object.assign(o, snap(o));
    dirty = true;
    render();
  }

  /** Drag a corner: the opposite corner stays put, the aspect ratio is fixed, the scale follows the size. */
  function startResize(e, el, corner) {
    e.stopPropagation();
    const o = outs.find((x) => x.id === el.dataset.id);
    const v = view();
    const ui = el.getBoundingClientRect().width / (o.w * v.s); // the whole UI may be scaled
    const px = pixels(o);
    const start = { px: e.clientX, py: e.clientY, x: o.x, y: o.y, w: o.w, h: o.h };
    const east = corner.includes("e"), south = corner.includes("s");
    const handle = e.target;
    const node = el;
    try { handle.setPointerCapture(e.pointerId); } catch { /* pointer already gone */ }
    node.classList.add("dragging");
    const move = (ev) => {
      const dx = (ev.clientX - start.px) / ui / v.s * (east ? 1 : -1);
      const dy = (ev.clientY - start.py) / ui / v.s * (south ? 1 : -1);
      let factor = ((start.w + dx) / start.w + (start.h + dy) / start.h) / 2;
      const scale = Math.min(MAX_SCALE, Math.max(MIN_SCALE, px.w / (start.w * factor)));
      factor = px.w / scale / start.w;
      o.w = start.w * factor; o.h = start.h * factor;
      o.x = east ? start.x : start.x + start.w - o.w;
      o.y = south ? start.y : start.y + start.h - o.h;
      o.scaleSetting = Math.round(scale * 100) / 100;
      Object.assign(node.style, { left: `${v.ox + o.x * v.s}px`, top: `${v.oy + o.y * v.s}px`, width: `${o.w * v.s}px`, height: `${o.h * v.s}px` });
      $(".pct", node).textContent = `${Math.round(scale * 100)}%`;
    };
    const up = () => {
      handle.removeEventListener("pointermove", move);
      handle.removeEventListener("pointerup", up);
      Object.assign(o, sizeFor(o));
      Object.assign(o, snap(o));
      dirty = true;
      render();
    };
    handle.addEventListener("pointermove", move);
    handle.addEventListener("pointerup", up);
  }

  function nudge(e, el) {
    const o = outs.find((x) => x.id === el.dataset.id);
    if (e.key === "+" || e.key === "=" || e.key === "-") {
      e.preventDefault();
      select(o.id);
      setScale(o, Math.min(MAX_SCALE, Math.max(MIN_SCALE, scaleOf(o) + (e.key === "-" ? 0.05 : -0.05))));
      $(`.mon[data-id="${CSS.escape(o.id)}"]`, root)?.focus();
      return;
    }
    const step = (e.shiftKey ? 0.5 : 0.1);
    const d = { ArrowLeft: [-o.w * step, 0], ArrowRight: [o.w * step, 0], ArrowUp: [0, -o.h * step], ArrowDown: [0, o.h * step] }[e.key];
    if (e.key === "Enter" || e.key === " ") { e.preventDefault(); return select(o.id); }
    if (!d) return;
    e.preventDefault();
    select(o.id);
    o.x += d[0]; o.y += d[1];
    Object.assign(o, snap(o));
    dirty = true;
    render();
    $(`.mon[data-id="${CSS.escape(o.id)}"]`, root)?.focus();
  }

  async function apply() {
    const layout = Object.fromEntries(outs.map((o) => [o.id, { x: o.x, y: o.y, mode: o.mode, transform: o.transform, scale: o.scaleSetting }]));
    const res = await api("/api/displays", { main: mainId, layout });
    if (!res.ok) return toast("Couldn't apply the arrangement");
    await refresh();
    loadArrangement();
    render();
    toast("Arrangement applied. Windows gets it at the next sign in.");
  }

  const patchFor = (key, value) => { const [a, b] = key.split("."); return { [a]: { [b]: value } }; };

  loadArrangement();
  render();
  loadControls();
  api("/api/monitor").then((m) => {
    const line = $("#usbline", root);
    const now = (m.usb || []).filter((d) => !d.protected).map((d) => d.name);
    if (line && now.length) line.textContent = `Now: ${now.join(", ")}. The system disk and network adapters are always kept.`;
  });
  return watch((st) => [st.displays, st.monitor.testing, st.monitor.standby], () => {
    if (dirty || document.activeElement?.type === "range") return;
    loadArrangement();
    render();
  });
}
