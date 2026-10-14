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
