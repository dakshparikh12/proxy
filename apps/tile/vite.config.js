// Vite static build for the in-meeting tile (Doc 08 §2.2). A tiny canvas app — the
// breathing teal orb + the §2.2 state machine, streamed as Proxy's camera via Doc 02's
// Output Media. Outbound-only (§12.9): it consumes `tile.state` render frames over the
// meeting-scoped render WS and originates nothing, so there is no backend REST proxy here.
import { defineConfig } from "vite";

export default defineConfig({
  root: ".",
  build: {
    outDir: "dist",
    emptyOutDir: true,
  },
});
