// A headless render check for the connect page's readiness renderer (Doc 08 §2.7).
//
// Drives the PURE renderReadiness() over each of the five canonical states and asserts
// the honest output — the real coverage number and flagged files on ready, named gaps on
// not_ready, a labelled progress line for each in-flight state, and NO 'mapping' branch.
// Run headless (no browser): `node apps/connect/src/readiness.render-check.mjs`. Exits 0
// on all-pass, non-zero (with a message) on any failure — the pytest wrapper reads that.
import { renderReadiness, ALL_STATES, PROGRESS_STATES } from "./readiness.js";

const failures = [];
function check(name, cond) {
  if (!cond) failures.push(name);
}

// The enum the renderer knows is EXACTLY the canonical five — no 'mapping'.
check("no mapping state in ALL_STATES", !ALL_STATES.includes("mapping"));
check("all five canonical states present", ALL_STATES.length === 5 &&
  ["connecting", "cloning", "indexing", "ready", "not_ready"].every((s) => ALL_STATES.includes(s)));

// Each progress state renders a labelled, spinnered panel tagged with its state.
for (const state of PROGRESS_STATES) {
  const html = renderReadiness({ status: state });
  check(`progress ${state} tags data-state`, html.includes(`data-state="${state}"`));
  check(`progress ${state} shows a spinner`, html.includes("readiness__spinner"));
}

// ready renders the REAL coverage number and the flagged files honestly.
const readyHtml = renderReadiness({
  status: "ready",
  coverage_pct: 0.94,
  flagged_files: [
    { path: "gen/bundle.min.js", reason: "generated" },
    { path: "vendor/lib.min.js", reason: "generated" },
  ],
});
check("ready shows the real 94% number", readyHtml.includes("94% indexed"));
check("ready summarises flagged files", readyHtml.includes("2 files flagged"));
check("ready names the flag reason", readyHtml.includes("generated"));
check("ready lists the flagged path", readyHtml.includes("gen/bundle.min.js"));
check("ready does not fabricate a gaps block", !readyHtml.includes("readiness__gaps"));

// not_ready NAMES the gaps — never a pretended number, never an error page.
const notReadyHtml = renderReadiness({
  status: "not_ready",
  gaps: ["submodule vendor/ could not be cloned", "3 files failed to parse"],
});
check("not_ready tags data-state", notReadyHtml.includes('data-state="not_ready"'));
check("not_ready names the first gap", notReadyHtml.includes("submodule vendor/ could not be cloned"));
check("not_ready names the second gap", notReadyHtml.includes("3 files failed to parse"));
check("not_ready does not claim a coverage percent", !/\d+% indexed/.test(notReadyHtml));

// An unknown/absent status degrades to the honest initial 'connecting' — never blank,
// never a fabricated 'ready'.
const unknownHtml = renderReadiness({ status: "mapping" });
check("unknown status degrades to connecting", unknownHtml.includes('data-state="connecting"'));

if (failures.length) {
  console.error("RENDER CHECK FAILED:\n  - " + failures.join("\n  - "));
  process.exit(1);
}
console.log("RENDER CHECK OK");
