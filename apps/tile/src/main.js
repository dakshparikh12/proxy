// The in-meeting tile controller (Doc 08 §2.2/§3/§12.9).
//
// The tile is one canvas page (Doc 02's canvas) streamed as Proxy's camera. It renders the
// breathing teal orb and drives it through the eight §2.2 states — each entered ONLY by an
// inbound `tile.state` render frame (from the projector). It never decides a state itself.
//
// OUTBOUND-ONLY (§12.9): the render WS authenticates via a meeting-scoped bearer token
// carried in the Recall URL. The tile only RECEIVES frames — it never originates a WS
// message (there is deliberately no write-back to the socket; a human cannot click a
// video stream, so the tile has no inbound-origination path at all).
import { TileStateMachine } from "./state_machine.js";
import { startOrbLoop } from "./orb.js";

// Read the meeting-scoped bearer token from the render URL Recall was handed (§12.9). The
// token is how the render WS authenticates; the tile never mints or sends it onward.
function renderTokenFromUrl(search) {
  const params = new URLSearchParams(search || "");
  return params.get("token") || "";
}

// Open the render WS as an OUTBOUND-ONLY consumer: every inbound `tile.state` frame drives
// the pure machine; the tile writes NOTHING back (no socket.send anywhere). A malformed or
// ninth/ad-hoc frame is ignored by the machine (applyFrame refuses it) — never rendered.
function connectRenderSocket(url, machine, { WebSocketImpl = globalThis.WebSocket } = {}) {
  const socket = new WebSocketImpl(url);
  socket.addEventListener("message", (event) => {
    let frame;
    try {
      frame = JSON.parse(event.data);
    } catch {
      return; // a malformed frame is dropped, never rendered
    }
    machine.applyFrame(frame); // the SOLE transition path — driven only by the frame
  });
  return socket;
}

function boot() {
  const canvas = document.getElementById("tile-canvas");
  if (!canvas) return;
  const machine = new TileStateMachine();
  startOrbLoop(canvas, machine);

  const token = renderTokenFromUrl(window.location.search);
  // Without a meeting-scoped token there is no authenticated render WS — the orb still
  // breathes in its default `listening` state, but no frames arrive.
  if (token) {
    const base = window.location.origin.replace(/^http/, "ws");
    const url = `${base}/render?token=${encodeURIComponent(token)}`;
    connectRenderSocket(url, machine);
  }
}

if (typeof document !== "undefined") {
  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", boot);
  } else {
    boot();
  }
}

export { renderTokenFromUrl, connectRenderSocket };
