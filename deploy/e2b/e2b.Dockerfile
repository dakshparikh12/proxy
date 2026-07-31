# The proxy-workroom E2B template image — the per-meeting sandbox that IS the agent
# on the call (SPEC §2). Bakes in EVERYTHING the workroom setup would otherwise
# install at join, so the sandbox is warm and the first ask is instant:
#
#   - native Claude Code CLI (`claude`)  — the in-sandbox brain
#   - the pinned MCP SDK (mcp==1.28.1)   — the one `to_meeting` connection to the room
#   - git                                — shallow-clone the repo at join
#
# Mirrors the runtime setup in services/in-meeting/src/in_meeting/workroom.py
# (npm i -g @anthropic-ai/claude-code · pip install mcp==1.28.1 · git clone). Keep
# the versions here in sync with that module's MCP_PIN.
#
# E2B requires an Ubuntu/Debian-family base. The repo, the prime (CLAUDE.md), the
# map, the transcript, and the MCP server are seeded PER MEETING at provision (not
# baked) — only the toolchain is baked.
FROM ubuntu:22.04

ENV DEBIAN_FRONTEND=noninteractive

# Base toolchain: python3 + pip (the in-sandbox MCP server + mcp SDK), git (clone),
# curl/ca-certificates (node install), and Node.js 20 (the claude-code CLI runtime).
RUN apt-get update && apt-get install -y --no-install-recommends \
      python3 python3-pip git curl ca-certificates \
    && curl -fsSL https://deb.nodesource.com/setup_20.x | bash - \
    && apt-get install -y --no-install-recommends nodejs \
    && rm -rf /var/lib/apt/lists/*

# Native Claude Code CLI — the workroom brain (auth is the per-meeting
# CLAUDE_CODE_OAUTH_TOKEN injected at run time, never baked).
RUN npm install -g @anthropic-ai/claude-code

# The pinned MCP SDK the in-sandbox meeting server imports (keep == workroom.MCP_PIN).
RUN pip3 install --no-cache-dir mcp==1.28.1

# The sandbox works out of the default user's home (WORKROOM_ROOT=/home/user/work).
WORKDIR /home/user
