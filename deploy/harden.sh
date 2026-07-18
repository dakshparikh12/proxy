#!/usr/bin/env bash
# Host hardening for the code_intel / meeting_runtime GCE hosts (AC-OBS-006).
#
# The ONE idempotent hardening script. Dedicated config-management platforms are
# skip-listed at V0, so hardening is a single guarded shell script:
# every mutation is guarded so a second run is a no-op, and there is NO arbitrary
# host code-execution path — all untrusted/customer code runs ONLY inside E2B
# sandboxes, never on the host.
set -euo pipefail

log() { printf '[harden] %s\n' "$1"; }

# ── 1. key-only SSH: disable password auth + root login ──────────────────────
sshd_conf=/etc/ssh/sshd_config.d/10-hardening.conf
if [ ! -f "$sshd_conf" ] || ! grep -q '^PasswordAuthentication no' "$sshd_conf"; then
  log "enforcing key-only SSH"
  {
    echo 'PasswordAuthentication no'
    echo 'PermitRootLogin no'
    echo 'ChallengeResponseAuthentication no'
  } > "$sshd_conf"
  systemctl reload ssh || systemctl reload sshd || true
fi

# ── 2. fail2ban: brute-force protection (guarded install) ────────────────────
if ! command -v fail2ban-client >/dev/null 2>&1; then
  log "installing fail2ban"
  apt-get update -qq
  DEBIAN_FRONTEND=noninteractive apt-get install -y -qq fail2ban
fi
systemctl enable --now fail2ban || true

# ── 3. unattended-upgrades: automatic security patches ───────────────────────
if ! dpkg -s unattended-upgrades >/dev/null 2>&1; then
  log "installing unattended-upgrades"
  DEBIAN_FRONTEND=noninteractive apt-get install -y -qq unattended-upgrades
fi
if [ ! -f /etc/apt/apt.conf.d/20auto-upgrades ]; then
  cat > /etc/apt/apt.conf.d/20auto-upgrades <<'EOF'
APT::Periodic::Update-Package-Lists "1";
APT::Periodic::Unattended-Upgrade "1";
EOF
fi

# ── 4. non-root service account: services never run as root ──────────────────
if ! id -u proxy >/dev/null 2>&1; then
  log "creating non-root service user 'proxy' (uid 1001)"
  useradd --system --uid 1001 --create-home --shell /usr/sbin/nologin proxy
fi

# ── 5. host firewall (ufw): default-deny, layer 1 of two ─────────────────────
if command -v ufw >/dev/null 2>&1; then
  if ! ufw status | grep -q 'Status: active'; then
    log "enabling ufw default-deny host firewall"
    ufw --force default deny incoming
    ufw --force default allow outgoing
    ufw --force allow 8443/tcp   # tenant+sha-scoped internal API
    ufw --force allow 8080/tcp   # health probe
    ufw --force enable
  fi
fi

# ── 6. encrypted volume: the per-tenant data volume is dm-crypt/LUKS backed ──
# The attached persistent disk is CMEK/KMS-encrypted at the cloud layer AND the
# mounted data path is a dm-crypt (LUKS) volume; only mount if present + not open.
enc_dev=/dev/disk/by-id/code-intel-tenant
if [ -e "$enc_dev" ] && ! [ -e /dev/mapper/encrypted ]; then
  log "opening dm-crypt encrypted volume at /mnt/encrypted"
  cryptsetup luksOpen "$enc_dev" encrypted
  mkdir -p /mnt/encrypted
  mount /dev/mapper/encrypted /mnt/encrypted
fi

# NOTE: arbitrary code execution is scoped to E2B sandboxes ONLY. This host runs
# no customer code; there is deliberately no eval/exec or curl|sh path here.
log "hardening complete (idempotent; re-runs are a no-op)"
