// Controllers on Dot's own screens, through the browser's Gamepad API: which one was used last (a
// controller or the keyboard and mouse), its type for the button icons, and button presses as events.
//
//   pad.on((button) => ...)   button: "a" "b" "x" "y" "lb" "rb" "start" "back" "guide" "up" "down" "left" "right"
//   pad.active                true while the last input came from a controller
//   pad.glyph("y")            the label on this controller: "Y", "△", or Nintendo's "X"

const BUTTONS = { 0: "a", 1: "b", 2: "x", 3: "y", 4: "lb", 5: "rb", 8: "back", 9: "start", 12: "up", 13: "down", 14: "left", 15: "right", 16: "guide" };
const GLYPHS = {
  xbox: { a: "A", b: "B", x: "X", y: "Y", lb: "LB", rb: "RB", start: "≡", back: "⧉" },
  playstation: { a: "✕", b: "○", x: "□", y: "△", lb: "L1", rb: "R1", start: "≡", back: "⧉" },
  switch: { a: "B", b: "A", x: "Y", y: "X", lb: "L", rb: "R", start: "+", back: "−" },
};

const listeners = new Set();
const modeListeners = new Set();
let held = {};

export const pad = {
  active: false,
  type: "xbox",
  name: "",
  on(fn) { listeners.add(fn); return () => listeners.delete(fn); },
  onMode(fn) { modeListeners.add(fn); return () => modeListeners.delete(fn); },
  glyph(button) { return (GLYPHS[pad.type] || GLYPHS.xbox)[button] || button.toUpperCase(); },
};

function setActive(active) {
  if (pad.active === active) return;
  pad.active = active;
  document.body.dataset.input = active ? "pad" : "keys";
  modeListeners.forEach((fn) => fn(active));
}

function typeOf(id) {
  const s = id.toLowerCase();
  if (/045e|xbox|xinput/.test(s)) return "xbox";
  if (/054c|dualsense|dualshock|playstation|wireless controller/.test(s)) return "playstation";
  if (/057e|pro controller|joy-con|nintendo/.test(s)) return "switch";
  return "xbox";
}

function poll() {
  const pads = navigator.getGamepads ? [...navigator.getGamepads()].filter(Boolean) : [];
  for (const gp of pads) {
    gp.buttons.forEach((b, i) => {
      const name = BUTTONS[i];
      const key = `${gp.index}:${i}`;
      if (!name) return;
      if (b.pressed && !held[key]) {
        held[key] = true;
        pad.type = typeOf(gp.id);
        pad.name = gp.id.replace(/\(.*?\)/g, "").trim();
        setActive(true);
        listeners.forEach((fn) => fn(name));
      } else if (!b.pressed) {
        delete held[key];
      }
    });
  }
  setTimeout(poll, 33);
}

addEventListener("keydown", (e) => { if (e.isTrusted) setActive(false); }, true);
addEventListener("mousedown", () => setActive(false), true);
let last = null;
addEventListener("mousemove", (e) => {
  if (last && Math.abs(e.screenX - last[0]) + Math.abs(e.screenY - last[1]) > 12) setActive(false);
  last = [e.screenX, e.screenY];
}, true);
addEventListener("gamepadconnected", (e) => { pad.type = typeOf(e.gamepad.id); pad.name = e.gamepad.id; });
document.body.dataset.input = "keys";
poll();

// Button icons by position, the same on every controller: four face buttons in a diamond with the one meant
// filled in (a bottom, b right, x left, y top), and a D-pad with its direction(s) filled in.
const FACE = { y: [11, 4.5], b: [17.5, 11], a: [11, 17.5], x: [4.5, 11] };
const ARMS = { up: 0, right: 90, down: 180, left: 270 };
const DPAD = { up: ["up"], down: ["down"], left: ["left"], right: ["right"], updown: ["up", "down"], leftright: ["left", "right"] };

export function padIcon(name, size = 22) {
  const svg = (inner) => `<svg class="padicon" width="${size}" height="${size}" viewBox="0 0 22 22" fill="none" stroke="currentColor" stroke-width="1.4" stroke-linejoin="round" aria-hidden="true">${inner}</svg>`;
  if (FACE[name]) {
    return svg(Object.entries(FACE).map(([b, [x, y]]) => `<circle cx="${x}" cy="${y}" r="3.1" ${b === name ? 'fill="currentColor"' : ""}/>`).join(""));
  }
  if (DPAD[name]) {
    return svg(Object.entries(ARMS).map(([arm, deg]) =>
      `<path d="M8.5 1.5h5v4.6L11 8.6 8.5 6.1z" transform="rotate(${deg} 11 11)" ${DPAD[name].includes(arm) ? 'fill="currentColor"' : ""}/>`).join(""));
  }
  return `<b class="padkey">${pad.glyph(name)}</b>`;  // Start, Select, shoulders: their label
}
