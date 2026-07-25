// The connect page's controller (Doc 08 §2.7): launch the install, drive the REST
// readiness poll, render the five states. A public URL — its only backend calls are the
// two §4.6 REST routes (POST /connect/install/start, GET /connect/status). The poll is
// REST, NOT a WS message (CANONICAL §12.12).
import { renderReadiness, isTerminal } from "./readiness.js";

const POLL_INTERVAL_MS = 1500;

async function startInstall(repoUrl) {
  const resp = await fetch("/connect/install/start", {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ repo_url: repoUrl }),
  });
  if (!resp.ok) throw new Error("install failed");
  return resp.json(); // { install_id, install_url }
}

async function pollStatus(installId) {
  const resp = await fetch(`/connect/status?install_id=${encodeURIComponent(installId)}`);
  if (!resp.ok) throw new Error("status failed");
  return resp.json(); // { status, coverage_pct, gaps, flagged_files }
}

function renderInto(el, report) {
  el.innerHTML = renderReadiness(report);
}

function pollUntilTerminal(installId, target) {
  const tick = async () => {
    let report;
    try {
      report = await pollStatus(installId);
    } catch {
      // A transient poll failure never blanks the panel — keep the last honest state.
      window.setTimeout(tick, POLL_INTERVAL_MS);
      return;
    }
    renderInto(target, report);
    if (!isTerminal(report)) {
      window.setTimeout(tick, POLL_INTERVAL_MS);
    }
  };
  tick();
}

function wireInstallForm() {
  const form = document.getElementById("install-form");
  const readinessCard = document.getElementById("readiness-card");
  const target = document.getElementById("readiness");
  if (!form || !readinessCard || !target) return;

  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    const input = document.getElementById("repo-url");
    const repoUrl = (input && input.value.trim()) || "";
    if (!repoUrl) return;

    readinessCard.hidden = false;
    renderInto(target, { status: "connecting" });

    let handle;
    try {
      handle = await startInstall(repoUrl);
    } catch {
      renderInto(target, {
        status: "not_ready",
        gaps: ["Couldn't start the install — check the repository URL and try again."],
      });
      return;
    }

    // Open the GitHub-App install page in a new tab, then poll readiness here.
    if (handle.install_url) window.open(handle.install_url, "_blank", "noopener");
    pollUntilTerminal(handle.install_id, target);
  });
}

if (typeof document !== "undefined") {
  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", wireInstallForm);
  } else {
    wireInstallForm();
  }
}
