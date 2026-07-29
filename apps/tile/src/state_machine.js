// The tile's PURE state machine (Doc 08 §2.2/§3).
//
// The tile expresses EXACTLY the eight §2.2 states, and each is entered ONLY by its
// driving `tile.state` render frame (from the projector — host-side, driven by the named
// system events in transport/tile_state.py). This module NEVER decides a state on its own:
// there is no timer, no heuristic, no self-transition. It is a pure function of the frame
// the WS delivers. The renderer is handed ONLY this closed set, so a ninth/ad-hoc state is
// unrenderable — the identity cannot drift into a costume (§3 build rule).
//
// The set here is the SAME closed set the Python contract (libs/contracts TileState)
// carries — one source of truth, no drift (a test pins them equal).

// EXACTLY the eight §2.2 states, in spec order. No ninth/ad-hoc member.
export const ALL_TILE_STATES = [
  "listening",
  "listening-to",
  "working",
  "checking",
  "has-something",
  "speaking",
  "muted",
  "reaction",
];

// The default per §2.2: "the session is live" → listening (the orb breathes slowly).
export const DEFAULT_TILE_STATE = "listening";

const KNOWN = new Set(ALL_TILE_STATES);

// A `tile.state` frame carries a closed-Literal `state` (the wire shape of libs/contracts
// TileState). We accept a state ONLY if it is in the known eight — a frame carrying a
// ninth/ad-hoc state is REFUSED, never rendered (the renderer never re-decides state).
export function isKnownTileState(state) {
  return KNOWN.has(state);
}

// The pure machine: it holds the current state and advances it ONLY when a real
// `tile.state` frame arrives. `applyFrame` is the sole transition path — there is no
// method that changes the state without a frame, so the tile can never self-decide.
export class TileStateMachine {
  constructor() {
    this._state = DEFAULT_TILE_STATE;
  }

  get state() {
    return this._state;
  }

  // Apply an inbound `tile.state` frame. Returns the new state on a known frame; on an
  // unknown/ad-hoc state it REFUSES (returns null) and leaves the state unchanged — the
  // renderer is never driven into a state outside the closed §2.2 set (§3 build rule).
  applyFrame(frame) {
    if (!frame || frame.type !== "tile.state") return null;
    if (!isKnownTileState(frame.state)) return null; // no ninth/ad-hoc state ever renders
    this._state = frame.state;
    return this._state;
  }
}
