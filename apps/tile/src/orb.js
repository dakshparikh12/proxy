// The presence orb (Doc 08 §2.1): a soft geometric bloom that BREATHES, in teal-ink
// #35c2b8 on a near-black tile. An abstract form — never a person.
//
// THE HARD CEILING (§2.1). This renderer has NO code path for humanoid features, no
// theatrical effect path, no per-glyph animation. The orb's ENTIRE expressive range is
// five scalars — breath rate, firmness, shimmer, dim, pulse — and each §2.2 state is
// nothing but a choice of those five. There is deliberately no such draw call anywhere in
// this file; the costume is unrenderable, so the identity cannot drift into one.
//
// The orb is a pure function of the state the state machine holds; it never decides a
// state itself (§3 build rule). Ambient motion, not a video game: a calm, modest frame
// rate driven by requestAnimationFrame.

// The brand identity (§2.1): teal-ink seed on near-black.
export const TEAL_INK = "#35c2b8";
export const NEAR_BLACK = "#0a0d0e";

// The five-scalar expressive range — the ONLY things a §2.2 state may change. breathHz =
// breaths/sec; firmness = edge sharpness 0..1; shimmer = subtle grain 0..1; dim = overall
// brightness 0..1 (muted dims); pulse = amplitude of an audio-synced beat 0..1 (speaking).
// This table is the whole vocabulary — no state can express anything outside these five.
export const ORB_EXPRESSION = {
  listening: { breathHz: 0.18, firmness: 0.5, shimmer: 0.08, dim: 1.0, pulse: 0.0 },
  "listening-to": { breathHz: 0.22, firmness: 0.6, shimmer: 0.1, dim: 1.0, pulse: 0.0 },
  working: { breathHz: 0.28, firmness: 0.6, shimmer: 0.35, dim: 1.0, pulse: 0.0 },
  checking: { breathHz: 0.5, firmness: 0.7, shimmer: 0.2, dim: 1.0, pulse: 0.0 },
  "has-something": { breathHz: 0.2, firmness: 0.95, shimmer: 0.12, dim: 1.0, pulse: 0.0 },
  speaking: { breathHz: 0.24, firmness: 0.7, shimmer: 0.1, dim: 1.0, pulse: 0.6 },
  muted: { breathHz: 0.12, firmness: 0.4, shimmer: 0.04, dim: 0.45, pulse: 0.0 },
  reaction: { breathHz: 0.2, firmness: 0.85, shimmer: 0.15, dim: 1.0, pulse: 0.3 },
};

// The captioned states (§2.2): a small caption rides the orb. This is AMBIENCE text only
// — the accessibility substance is carried in chat/voice (the host-side fallback), never
// motion-only. The tile never carries small text the room must read (§2.5).
export const ORB_CAPTION = {
  "listening-to": "listening",
  working: "working",
  checking: "checking",
  "has-something": "have the answer when there's a moment",
  muted: "muted",
};

function expressionFor(state) {
  return ORB_EXPRESSION[state] || ORB_EXPRESSION.listening;
}

// Draw ONE frame of the breathing bloom for `state` at animation time `tSec`. Pure
// geometry + colour — a radial teal bloom on near-black whose radius breathes, whose edge
// firms, and which (for speaking/reaction) adds an audio-synced pulse. Bloom only.
export function drawOrb(ctx, width, height, state, tSec) {
  const e = expressionFor(state);
  ctx.save();
  // Near-black backdrop.
  ctx.fillStyle = NEAR_BLACK;
  ctx.fillRect(0, 0, width, height);

  const cx = width / 2;
  const cy = height / 2;
  const base = Math.min(width, height) * 0.28;
  // Breath: a slow sinusoid on the radius (ambient motion, not a video game).
  const breath = 1 + 0.08 * Math.sin(2 * Math.PI * e.breathHz * tSec);
  // Pulse: an audio-synced beat, only when a state carries pulse (speaking/reaction).
  const pulse = 1 + e.pulse * 0.06 * Math.max(0, Math.sin(2 * Math.PI * 2.2 * tSec));
  const radius = base * breath * pulse;

  // A soft radial bloom in teal-ink; firmness sharpens the core, dim scales brightness.
  const grad = ctx.createRadialGradient(cx, cy, radius * (0.1 + 0.5 * e.firmness), cx, cy, radius);
  grad.addColorStop(0, withAlpha(TEAL_INK, e.dim));
  grad.addColorStop(0.6, withAlpha(TEAL_INK, e.dim * 0.35 + e.shimmer * 0.1));
  grad.addColorStop(1, withAlpha(TEAL_INK, 0));
  ctx.fillStyle = grad;
  ctx.beginPath();
  ctx.arc(cx, cy, radius, 0, 2 * Math.PI);
  ctx.fill();
  ctx.restore();
  return { state, radius, dim: e.dim, pulse: e.pulse };
}

function withAlpha(hex, alpha) {
  const a = Math.max(0, Math.min(1, alpha));
  const n = parseInt(hex.slice(1), 16);
  const r = (n >> 16) & 255;
  const g = (n >> 8) & 255;
  const b = n & 255;
  return `rgba(${r}, ${g}, ${b}, ${a})`;
}

// Start the ambient breathing loop over a live TileStateMachine. Pure animation: it reads
// the machine's CURRENT state each frame and draws it — it never sets the state (the
// machine is driven only by inbound frames, §3). Returns a stop() handle.
export function startOrbLoop(canvas, machine, { now = () => performance.now() } = {}) {
  const ctx = canvas.getContext("2d");
  const t0 = now();
  let raf = 0;
  const frame = () => {
    const tSec = (now() - t0) / 1000;
    drawOrb(ctx, canvas.width, canvas.height, machine.state, tSec);
    raf = requestAnimationFrame(frame);
  };
  raf = requestAnimationFrame(frame);
  return () => cancelAnimationFrame(raf);
}
