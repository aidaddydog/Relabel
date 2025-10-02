#!/usr/bin/env bash
set -euo pipefail

# Defaults
RELABEL_BASE=${RELABEL_BASE:-/srv/relabel}
RELABEL_DATA=${RELABEL_DATA:-/srv/relabel/data}
PORT=${PORT:-8000}
HOST=${HOST:-0.0.0.0}
DB_USER=${DB_USER:-relabel}
DB_PASS=${DB_PASS:-relabel}
DB_NAME=${DB_NAME:-relabel}
DB_HOST=${DB_HOST:-localhost}
DB_PORT=${DB_PORT:-5432}
ADMIN_USER=${ADMIN_USER:-admin}
ADMIN_PASS=${ADMIN_PASS:-admin123}

usage() {
  cat <<EOF
Usage: sudo bash scripts/install_root.sh [options]

  -b RELABEL_BASE       (default: /srv/relabel)
  -d RELABEL_DATA       (default: /srv/relabel/data)
  -p PORT               (default: 8000)
  -U ADMIN_USER         (default: admin)
  -W ADMIN_PASS         (default: admin123)
  --db-user USER        (default: relabel)
  --db-pass PASS        (default: relabel)
  --db-name NAME        (default: relabel)
  --db-host HOST        (default: localhost)
  --db-port PORT        (default: 5432)
EOF
}

# Parse args
while [[ $# -gt 0 ]]; do
  case "$1" in
    -b) RELABEL_BASE="$2"; shift 2;;
    -d) RELABEL_DATA="$2"; shift 2;;
    -p) PORT="$2"; shift 2;;
    -U) ADMIN_USER="$2"; shift 2;;
    -W) ADMIN_PASS="$2"; shift 2;;
    --db-user) DB_USER="$2"; shift 2;;
    --db-pass) DB_PASS="$2"; shift 2;;
    --db-name) DB_NAME="$2"; shift 2;;
    --db-host) DB_HOST="$2"; shift 2;;
    --db-port) DB_PORT="$2"; shift 2;;
    -h|--help) usage; exit 0;;
    *) echo "Unknown arg: $1"; usage; exit 1;;
  esac
done

echo "[*] Installing OS packages..."
export DEBIAN_FRONTEND=noninteractive
apt-get update -y
apt-get install -y git python3 python3-venv python3-pip build-essential libpq-dev postgresql postgresql-contrib unzip p7zip-full curl ca-certificates

echo "[*] Preparing directories..."
mkdir -p "$RELABEL_BASE" "$RELABEL_DATA" "$RELABEL_BASE/runtime" "$RELABEL_BASE/templates_ext"
chown -R root:root "$RELABEL_BASE"

echo "[*] Creating Python venv..."
cd "$RELABEL_BASE/apps/server"
python3 -m venv .venv
. .venv/bin/activate
python -m pip install --upgrade pip
pip install -r requirements.txt

echo "[*] Configuring Postgres..."
sudo -u postgres psql -tc "SELECT 1 FROM pg_roles WHERE rolname = '$DB_USER'" | grep -q 1 || sudo -u postgres psql -c "CREATE ROLE $DB_USER WITH LOGIN PASSWORD '$DB_PASS';"
sudo -u postgres psql -tc "SELECT 1 FROM pg_database WHERE datname = '$DB_NAME'" | grep -q 1 || sudo -u postgres psql -c "CREATE DATABASE $DB_NAME OWNER $DB_USER;"
export DATABASE_URL="postgresql+psycopg://$DB_USER:$DB_PASS@$DB_HOST:$DB_PORT/$DB_NAME"

echo "[*] Running Alembic migrations..."
alembic upgrade head

echo "[*] Seeding data..."
# 确保可 import app.* ，并按环境变量写入管理员与默认客户端码
PYTHONPATH="$RELABEL_BASE/apps/server" \
RELABEL_ADMIN_USER="$ADMIN_USER" RELABEL_ADMIN_PASSWORD="$ADMIN_PASS" RELABEL_CLIENT_CODE="123456" \
python ../../scripts/dev_seed.py || true

echo "[*] Building frontend..."
# 安装 Node.js 20（如未安装）
if ! command -v node >/dev/null 2>&1; then
  curl -fsSL https://deb.nodesource.com/setup_20.x | bash -
  apt-get install -y nodejs
fi
cd "$RELABEL_BASE/apps/web"
npm config set registry https://registry.npmmirror.com

# 统一使用 npm install（避免 lockfile 与 package.json 不一致导致 npm ci 失败）
npm install

# 强制确保 @vitejs/plugin-react 存在（首次或锁文件缺项时自动补齐）
node -e "require('@vitejs/plugin-react')" >/dev/null 2>&1 || npm install -D @vitejs/plugin-react@^4

# 构建
npm run build
cd "$RELABEL_BASE/apps/server"

echo "[*] Writing environment file..."
mkdir -p /etc/relabel
cat >/etc/relabel/relabel.env <<ENV
RELABEL_BASE=$RELABEL_BASE
RELABEL_DATA=$RELABEL_DATA
HOST=$HOST
PORT=$PORT
DATABASE_URL=$DATABASE_URL
RELABEL_PEPPER=$(head -c 32 /dev/urandom | base64)
SESSION_COOKIE_NAME=relabel_sess
ENV

echo "[*] Installing systemd service..."
cp "$RELABEL_BASE/deploy/Relabel.service" /etc/systemd/system/Relabel.service
systemctl daemon-reload
systemctl enable Relabel.service
systemctl restart Relabel.service

echo "[*] Done. Relabel Server is starting on $HOST:$PORT"
echo "    Health: curl http://$HOST:$PORT/healthz"
