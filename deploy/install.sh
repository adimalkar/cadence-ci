#!/usr/bin/env bash
# Install the Cadence ingest worker as a systemd user service.
#
# Idempotent: safe to re-run after a code change or a token rotation. It never overwrites
# an existing credential file, and it never prints a token.
#
#   ./deploy/install.sh            install (or update) and start
#   ./deploy/install.sh --status   show state without changing anything
#
# Deliberately a *user* unit rather than a system one: the worker needs no root, and a
# user unit keeps the credential in the user's own config directory. `loginctl
# enable-linger` is what makes it survive logout and start at boot.

set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CADENCE_BIN="${PROJECT_DIR}/.venv/bin/cadence"
UNIT_DIR="${HOME}/.config/systemd/user"
UNIT="${UNIT_DIR}/cadence-worker.service"
ENV_DIR="${HOME}/.config/cadence"
ENV_FILE="${ENV_DIR}/worker.env"

say() { printf '  %s\n' "$*"; }

if [[ "${1:-}" == "--status" ]]; then
  systemctl --user status cadence-worker --no-pager || true
  echo
  say "linger: $(loginctl show-user "$USER" --property=Linger --value 2>/dev/null || echo unknown)"
  say "logs:   journalctl --user -u cadence-worker -f"
  exit 0
fi

echo "Installing the Cadence ingest worker"

# --- preflight ------------------------------------------------------------------------
[[ -x "$CADENCE_BIN" ]] || {
  echo "error: $CADENCE_BIN is missing or not executable." >&2
  echo "       Run 'uv sync --extra dev' in $PROJECT_DIR first." >&2
  exit 1
}
command -v systemctl >/dev/null || { echo "error: systemd not available." >&2; exit 1; }

# --- credentials ----------------------------------------------------------------------
# Written once, 0600, outside the repository. A rotation means editing this file, not
# re-running with a new secret on the command line where it would reach the shell history.
mkdir -p "$ENV_DIR"
chmod 700 "$ENV_DIR"

if [[ -f "$ENV_FILE" ]]; then
  say "credential file exists, leaving it alone: $ENV_FILE"
else
  token=""
  if command -v gh >/dev/null && gh auth status >/dev/null 2>&1; then
    token="$(gh auth token 2>/dev/null || true)"
  fi

  umask 077
  cat > "$ENV_FILE" <<ENVEOF
# Cadence worker environment. 0600, never committed.
# Rotate the token by editing this file, then:
#   systemctl --user restart cadence-worker

CADENCE_GITHUB_TOKEN=${token}
CADENCE_DATABASE_URL=postgresql:///cadence
CADENCE_LOG_STORE=${PROJECT_DIR}/data/logs
ENVEOF
  chmod 600 "$ENV_FILE"

  if [[ -n "$token" ]]; then
    say "wrote $ENV_FILE with the gh CLI's token (0600)"
    say ""
    say "NOTE: that is your personal gh token, which is a convenience, not the right"
    say "      credential. It shares one 5,000 req/hour budget with your interactive gh"
    say "      use -- heavy CLI work will stall ingest -- and it carries repo/workflow/"
    say "      gist/read:org scope when the worker needs only public read access."
    say "      Replace it with a fine-grained read-only PAT, or a GitHub App installation"
    say "      token which gets its own hourly budget. See CAVEATS.md item 27."
  else
    say "wrote $ENV_FILE, but CADENCE_GITHUB_TOKEN is EMPTY -- fill it in before starting"
  fi
fi

# --- unit -----------------------------------------------------------------------------
mkdir -p "$UNIT_DIR"
sed -e "s#@PROJECT_DIR@#${PROJECT_DIR}#g" \
    -e "s#@CADENCE_BIN@#${CADENCE_BIN}#g" \
    "${PROJECT_DIR}/deploy/cadence-worker.service" > "$UNIT"
say "installed $UNIT"

# Survive logout and start at boot. Without this the unit stops when the session ends,
# which is the whole failure being fixed.
if [[ "$(loginctl show-user "$USER" --property=Linger --value 2>/dev/null)" != "yes" ]]; then
  if loginctl enable-linger "$USER" 2>/dev/null; then
    say "enabled linger for $USER"
  else
    say "WARNING: could not enable linger; run 'sudo loginctl enable-linger $USER'"
  fi
else
  say "linger already enabled"
fi

systemctl --user daemon-reload
systemctl --user enable --now cadence-worker
say "enabled and started"

echo
sleep 2
systemctl --user is-active --quiet cadence-worker \
  && say "active. follow with: journalctl --user -u cadence-worker -f" \
  || { say "NOT active -- recent log:"; journalctl --user -u cadence-worker -n 20 --no-pager; exit 1; }
