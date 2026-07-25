// The pure readiness renderer — the honest brand, drawn (Doc 08 §2.7, Law 1/Law 2).
//
// Renders ALL FIVE states of Doc 01's Readiness enum (CANONICAL §1.5):
//   connecting → cloning → indexing → ready, plus an explicit not_ready(+gaps).
// The happy path carries the REAL coverage number and flagged files
// ("94% indexed · 12 files flagged: generated"); not_ready NAMES the gaps
// rather than pretending. There is NO 'mapping' state, and the input is the
// REST GET /connect/status body — never a WS message (CANONICAL §12.12).
//
// This module is a PURE function of the poll body so it is testable headless:
// renderReadiness(report) -> an HTML string. main.js mounts it into the DOM and
// drives the REST poll; this file draws.

// The canonical progression the page walks through before a terminal state.
export const PROGRESS_STATES = ["connecting", "cloning", "indexing"];
export const TERMINAL_STATES = ["ready", "not_ready"];
export const ALL_STATES = [...PROGRESS_STATES, ...TERMINAL_STATES];

const PROGRESS_LABEL = {
  connecting: "Connecting to GitHub…",
  cloning: "Cloning your repository…",
  indexing: "Indexing — parsing, resolving symbols, building the dependency graph…",
};

function escapeHtml(value) {
  return String(value)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

function coveragePercent(report) {
  const pct = Number(report && report.coverage_pct);
  if (!Number.isFinite(pct)) return 0;
  return Math.round(pct * 100);
}

// The honest flagged-files summary: "12 files flagged: generated, excluded".
function flaggedSummary(flagged) {
  if (!Array.isArray(flagged) || flagged.length === 0) return "";
  const reasons = [...new Set(flagged.map((f) => f.reason).filter(Boolean))];
  const count = flagged.length;
  const noun = count === 1 ? "file" : "files";
  const tail = reasons.length ? `: ${reasons.map(escapeHtml).join(", ")}` : "";
  return `${count} ${noun} flagged${tail}`;
}

function renderProgress(state) {
  const label = PROGRESS_LABEL[state] || "Getting ready…";
  const step = PROGRESS_STATES.indexOf(state) + 1;
  return `
    <div class="readiness__state readiness__state--progress" data-state="${escapeHtml(state)}">
      <span class="readiness__spinner" aria-hidden="true"></span>
      <span class="readiness__label">${escapeHtml(label)}</span>
      <span class="readiness__step muted">step ${step} of 3</span>
    </div>`;
}

function renderReady(report) {
  const pct = coveragePercent(report);
  const flagged = report && report.flagged_files;
  const summary = flaggedSummary(flagged);
  const flaggedList =
    Array.isArray(flagged) && flagged.length
      ? `<ul class="readiness__flagged">${flagged
          .map(
            (f) =>
              `<li><code>${escapeHtml(f.path)}</code> <span class="muted">${escapeHtml(
                f.reason,
              )}</span></li>`,
          )
          .join("")}</ul>`
      : "";
  // The honest headline: the REAL coverage number, and what is flagged — never a claim.
  const flaggedLine = summary ? ` · <span class="readiness__flagged-summary">${summary}</span>` : "";
  return `
    <div class="readiness__state readiness__state--ready" data-state="ready">
      <span class="readiness__check" aria-hidden="true">●</span>
      <span class="readiness__label">
        <strong>${pct}% indexed</strong>${flaggedLine}
      </span>
      ${flaggedList}
      <p class="muted">Proxy can see your code. It'll say what it's sure of, and flag what it isn't.</p>
    </div>`;
}

function renderNotReady(report) {
  const gaps = (report && report.gaps) || [];
  const named =
    Array.isArray(gaps) && gaps.length
      ? `<ul class="readiness__gaps">${gaps
          .map((g) => `<li>${escapeHtml(g)}</li>`)
          .join("")}</ul>`
      : `<p class="muted">Some of the repository couldn't be grounded.</p>`;
  // not_ready is a real terminal state that NAMES the gaps — never a pretended number.
  return `
    <div class="readiness__state readiness__state--not-ready" data-state="not_ready">
      <span class="readiness__warn" aria-hidden="true">△</span>
      <span class="readiness__label"><strong>Not fully ready</strong> — here's what's missing:</span>
      ${named}
      <p class="muted">Proxy will still help with what it can ground, and say so honestly.</p>
    </div>`;
}

// The single entry point: a poll body -> the HTML for the current state.
export function renderReadiness(report) {
  const status = report && report.status;
  if (status === "ready") return renderReady(report);
  if (status === "not_ready") return renderNotReady(report);
  if (PROGRESS_STATES.includes(status)) return renderProgress(status);
  // An unknown/absent status degrades to the honest initial state — never a blank
  // panel and never a fabricated 'ready'.
  return renderProgress("connecting");
}

export function isTerminal(report) {
  return report && TERMINAL_STATES.includes(report.status);
}
