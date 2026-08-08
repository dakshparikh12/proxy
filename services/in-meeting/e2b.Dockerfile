# Per-repo WORKROOM template — the pre-baked E2B sandbox that removes warm-time setup from the
# meeting critical path. Bakes the WHOLE toolchain + code-intel + deps as image LAYERS so a
# provision (or a pause/resume fast-join) starts from "everything already installed", instead of the
# base-sandbox path that clones + pip-installs + npm-installs at warm time (~25-30s, and OOM-prone on
# the 478 MB base box — see workroom.provision_workroom).
#
# WHY PER-REPO: the customer repo itself is baked in (ARG REPO_URL/REPO_SHA below) and Serena's
# language-server index warms against it, so the biggest, slowest layers are done ONCE at bake time.
# The template id this produces is passed per-provision as ``provision_workroom(template=...)``;
# ``workroom.DEFAULT_TEMPLATE`` stays ``None`` so the proven base-sandbox path is unchanged until a
# template is deliberately wired.
#
# BUILD (founder-gated deploy artifact — not run on the meeting path):
#   e2b template build -c "/root/.jupyter/start-up.sh" \
#     --build-arg REPO_URL=https://github.com/<org>/<repo>.git \
#     --build-arg REPO_SHA=<sha>
# (or drive it from e2b.toml). Requires the E2B CLI + an authenticated E2B account.

# Node >=22 base (the CLI's EBADENGINE floor; the base box ships node 20 and only WARNS, but the bake
# ships >=22 cleanly). This image already carries python3 + pip + git + build tooling.
FROM node:22-bookworm

# ── System deps ────────────────────────────────────────────────────────────────────────────────
# python3 + venv/pip for the in-sandbox MCP server and the warm session host; git for the clone;
# curl/ca-certificates for the uv installer; ripgrep as a fast grep the agent already reaches for.
RUN apt-get update && apt-get install -y --no-install-recommends \
        python3 python3-pip python3-venv git curl ca-certificates ripgrep \
    && rm -rf /var/lib/apt/lists/*

# ── The agent runtime: native Claude Code + the pinned Python SDKs ───────────────────────────────
# Keep these pins in LOCKSTEP with workroom.MCP_PIN / workroom.SDK_PIN (the base-sandbox path installs
# the same versions at warm time; the template just pre-bakes them).
RUN npm install -g @anthropic-ai/claude-code
RUN pip3 install --no-cache-dir --break-system-packages \
        "mcp==1.28.1" \
        "claude-agent-sdk>=0.2.115"

# ── Code intel: Serena (LSP symbol nav) + ast-grep (structural search) ────────────────────────────
# Serena is installed as a uv tool onto PATH so ``shutil.which("serena")`` succeeds in the sandbox and
# ``session_host._serena_server`` wires it (as a DEFERRED, cache-safe stdio MCP server). PROXY_SERENA=1
# is also asserted below as a belt-and-suspenders opt-in signal for that gate. ast-grep gives the agent
# structural (AST) search over the repo.
RUN curl -LsSf https://astral.sh/uv/install.sh | sh
ENV PATH="/root/.local/bin:${PATH}"
RUN uv tool install --python 3.13 serena-agent
RUN npm install -g @ast-grep/cli
# Assert Serena is present so the warm session host wires it without a PATH-timing race (Law 2: the
# flag is only set BECAUSE the layer above actually installed the binary — never a fake capability).
ENV PROXY_SERENA=1
ENV PROXY_SERENA_CONTEXT=ide-assistant

# ── The customer repo, baked in ──────────────────────────────────────────────────────────────────
# Cloned at BAKE time so a provision/resume never pays the clone. workroom.provision_workroom's setup
# is idempotent (``test -d repo/.git ||`` skips the clone when it's already here), so a template with
# the repo baked and a base sandbox both work with the SAME provision code — the template just makes
# the clone a no-op. Left OPTIONAL (empty REPO_URL ⇒ skipped) so this same Dockerfile also bakes a
# repo-less "toolchain-only" template.
ARG REPO_URL=""
ARG REPO_SHA=""
ENV PROXY_REPO_DIR=/home/user/work/repo
RUN if [ -n "$REPO_URL" ]; then \
        mkdir -p /home/user/work && \
        git clone "$REPO_URL" "$PROXY_REPO_DIR" && \
        if [ -n "$REPO_SHA" ]; then git -C "$PROXY_REPO_DIR" checkout -q "$REPO_SHA"; fi && \
        # Warm Serena's language-server index against the baked repo so the FIRST symbol lookup in the
        # meeting is instant (the slow index build happens here at bake time, not on the room's clock).
        (serena project index --project "$PROXY_REPO_DIR" || true) ; \
    fi
