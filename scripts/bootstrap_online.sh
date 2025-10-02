
#!/usr/bin/env bash
set -euo pipefail

# Default repo (update to your actual repo after import)
REPO_URL=${REPO_URL:-https://github.com/aidaddydog/Relabel.git}
BRANCH=${BRANCH:-main}
RELABEL_BASE=${RELABEL_BASE:-/srv/relabel}
RELABEL_DATA=${RELABEL_DATA:-/srv/relabel/data}
PORT=${PORT:-8000}
HOST=${HOST:-0.0.0.0}
ADMIN_USER=${ADMIN_USER:-admin}
ADMIN_PASS=${ADMIN_PASS:-admin123}

usage() {
  cat <<EOF
One-line install:
  bash <(curl -fsSL https://raw.githubusercontent.com/aidaddydog/Relabel/main/scripts/bootstrap_online.sh)

Optional flags (override with environment variables or CLI):
  --repo URL            Git repository URL (default: $REPO_URL)
  --branch BRANCH       Branch name (default: $BRANCH)
  -b RELABEL_BASE       Install base (default: $RELABEL_BASE)
  -d RELABEL_DATA       Data dir (default: $RELABEL_DATA)
  -p PORT               Listen port (default: $PORT)
  -U ADMIN_USER         Default admin (default: $ADMIN_USER)
  -W ADMIN_PASS         Default admin password (default: $ADMIN_PASS)
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --repo) REPO_URL="$2"; shift 2;;
    --branch) BRANCH="$2"; shift 2;;
    -b) RELABEL_BASE="$2"; shift 2;;
    -d) RELABEL_DATA="$2"; shift 2;;
    -p) PORT="$2"; shift 2;;
    -U) ADMIN_USER="$2"; shift 2;;
    -W) ADMIN_PASS="$2"; shift 2;;
    -h|--help) usage; exit 0;;
    *) echo "Unknown arg: $1"; usage; exit 1;;
  esac
done

echo "[*] Cloning repo $REPO_URL ($BRANCH) to $RELABEL_BASE ..."
mkdir -p "$RELABEL_BASE"
if [[ -d "$RELABEL_BASE/.git" ]]; then
  echo "[*] Repo already exists, pulling latest..."
  git -C "$RELABEL_BASE" fetch --all
  git -C "$RELABEL_BASE" checkout "$BRANCH"
  git -C "$RELABEL_BASE" pull --rebase
else
  git clone --branch "$BRANCH" "$REPO_URL" "$RELABEL_BASE"
fi

echo "[*] Running install_root.sh (root required)..."
cd "$RELABEL_BASE"
sudo RELABEL_BASE="$RELABEL_BASE" RELABEL_DATA="$RELABEL_DATA" PORT="$PORT" HOST="$HOST" \
  ADMIN_USER="$ADMIN_USER" ADMIN_PASS="$ADMIN_PASS" \
  bash scripts/install_root.sh -b "$RELABEL_BASE" -d "$RELABEL_DATA" -p "$PORT" -U "$ADMIN_USER" -W "$ADMIN_PASS"

echo "[*] Installation complete. Try: curl http://$HOST:$PORT/healthz"
