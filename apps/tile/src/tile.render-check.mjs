// A headless render check for the tile's orb + state machine (Doc 08 §2.1/§2.2/§3).
//
// Drives the REAL TileStateMachine + drawOrb over each of the eight §2.2 states from real
// `tile.state` frames and asserts the build rules:
//   - EXACTLY the eight §2.2 states, no ninth/ad-hoc member;
//   - each state is entered ONLY by its driving frame (applyFrame is the sole transition);
//   - a ninth/ad-hoc frame is REFUSED and never renders (the renderer never re-decides);
//   - no self-transition — the state is unchanged without a new frame;
//   - the orb draws a teal-ink #35c2b8 bloom on near-black (the presence mark);
//   - the expressive range is the five scalars only — no face/effect draw call.
// Run headless (no browser): `node apps/tile/src/tile.render-check.mjs`. Exits 0 on
// all-pass, non-zero (with a message) on any failure — the pytest wrapper reads that.
import { TileStateMachine, ALL_TILE_STATES, DEFAULT_TILE_STATE, isKnownTileState } from "./state_machine.js";
import { drawOrb, ORB_EXPRESSION, TEAL_INK, NEAR_BLACK } from "./orb.js";

const failures = [];
function check(name, cond) {
  if (!cond) failures.push(name);
}

// The canonical §2.2 set — the oracle. EXACTLY eight, in spec order.
const CANONICAL = [
  "listening",
  "listening-to",
  "working",
  "checking",
  "has-something",
  "speaking",
  "muted",
  "reaction",
];

// 1 · EXACTLY the eight §2.2 states — no ninth/ad-hoc member.
check("exactly eight states", ALL_TILE_STATES.length === 8);
check("state set == canonical §2.2", CANONICAL.every((s) => ALL_TILE_STATES.includes(s)) && ALL_TILE_STATES.every((s) => CANONICAL.includes(s)));
check("no ninth/ad-hoc state is known", !isKnownTileState("dancing") && !isKnownTileState("nod"));

// 2 · Each state is entered ONLY by its driving `tile.state` frame.
for (const state of CANONICAL) {
  const m = new TileStateMachine();
  const applied = m.applyFrame({ type: "tile.state", state });
  check(`frame drives ${state}`, applied === state && m.state === state);
  // Every driven state has an orb expression (the five-scalar vocabulary).
  const e = ORB_EXPRESSION[state];
  check(`${state} has an orb expression`, e && typeof e.breathHz === "number" && typeof e.dim === "number");
}

// 3 · A ninth/ad-hoc frame is REFUSED and never renders (the renderer never re-decides).
{
  const m = new TileStateMachine();
  const before = m.state;
  const applied = m.applyFrame({ type: "tile.state", state: "dancing" }); // ad-hoc → refused
  check("ninth/ad-hoc state is refused", applied === null && m.state === before);
  // A non-tile.state frame is also ignored (outbound-only, frames-only transitions).
  check("non-tile.state frame ignored", m.applyFrame({ type: "voice.speak", text: "x" }) === null);
}

// 4 · No self-transition — the state is unchanged without a NEW frame.
{
  const m = new TileStateMachine();
  m.applyFrame({ type: "tile.state", state: "working" });
  const s1 = m.state;
  // No frame applied → the state does not move on its own (no timer/heuristic).
  check("no self-transition without a frame", m.state === s1 && s1 === "working");
  m.applyFrame({ type: "tile.state", state: "speaking" }); // only a new frame moves it
  check("a new frame is the only mover", m.state === "speaking");
  check("default state is listening", DEFAULT_TILE_STATE === "listening");
}

// 5 · The orb draws a teal-ink bloom on near-black — the presence mark (§2.1). We drive
// drawOrb over a minimal canvas-2d stub and record the fill colours it used.
function makeCtxStub() {
  const used = { fills: [], gradientStops: [], radialGradients: 0, arcs: 0 };
  return {
    _used: used,
    save() {},
    restore() {},
    beginPath() {},
    fill() {},
    fillRect() {},
    arc() {
      used.arcs += 1;
    },
    set fillStyle(v) {
      used.fills.push(v);
    },
    get fillStyle() {
      return used.fills[used.fills.length - 1];
    },
    createRadialGradient() {
      used.radialGradients += 1;
      return {
        addColorStop(_stop, colour) {
          used.gradientStops.push(colour);
        },
      };
    },
  };
}
{
  const ctx = makeCtxStub();
  const out = drawOrb(ctx, 1280, 720, "listening", 0.0);
  check("orb draws on near-black", ctx._used.fills.includes(NEAR_BLACK));
  check("orb is a radial bloom", ctx._used.radialGradients >= 1 && ctx._used.arcs >= 1);
  // The bloom's colour stops are the teal-ink seed (rgba built from #35c2b8).
  const teal = parseInt(TEAL_INK.slice(1), 16);
  const r = (teal >> 16) & 255;
  const g = (teal >> 8) & 255;
  const b = teal & 255;
  const tealStop = ctx._used.gradientStops.some((c) => c.includes(`${r}, ${g}, ${b}`));
  check("orb bloom is teal-ink #35c2b8", tealStop);
  check("drawOrb returns the state it drew", out.state === "listening");
}

// 6 · The expressive range is the five scalars ONLY — no state can express anything else
// (breath rate, firmness, shimmer, dim, pulse — §2.1). Proven by the shape of the table.
{
  const allowed = new Set(["breathHz", "firmness", "shimmer", "dim", "pulse"]);
  let ok = true;
  for (const state of CANONICAL) {
    for (const key of Object.keys(ORB_EXPRESSION[state])) {
      if (!allowed.has(key)) ok = false;
    }
  }
  check("expressive range is the five scalars only", ok);
  // muted dims the orb; speaking carries a pulse — the honest §2.2 look.
  check("muted dims", ORB_EXPRESSION.muted.dim < 1.0);
  check("speaking pulses", ORB_EXPRESSION.speaking.pulse > 0);
}

if (failures.length) {
  console.error("TILE RENDER CHECK FAILED:\n  - " + failures.join("\n  - "));
  process.exit(1);
}
console.log("TILE RENDER CHECK OK");
